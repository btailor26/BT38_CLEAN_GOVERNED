"""Packlink PRO adapter for BT38 FBM.

Marketplace orders remain the source of truth. Packlink is an external shipping
provider only: BT38 maps the exact marketplace delivery facts into Packlink's
shipment contract and never invents buyer data.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from services.fbm_order_mapper import order_lines, ship_from, ship_to
from services.fbm_provider_contract import ProviderCapabilities

PACKLINK_BASE_URL = "https://api.packlink.com/v1/"
PACKLINK_TIMEOUT_SECONDS = 12
PACKLINK_DEFAULT_CONTENT_VALUE = 20.0
PACKLINK_ACCOUNT_COUNTRY = "GB"
PACKLINK_PLATFORM = "PRO"
PACKLINK_DRAFT_SOURCE = "source_inbound"


class PacklinkConfigurationError(RuntimeError):
    pass


class PacklinkRequestError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PacklinkConnectionResult:
    ok: bool
    configured: bool
    status_code: int | None
    account_country: str | None = None
    account_email: str | None = None
    message: str | None = None


class PacklinkAdapter:
    capabilities = ProviderCapabilities(
        provider="packlink", quotes=True, label_purchase=False,
        tracking_status=True, case_opening=False, return_labels=False,
    )

    def __init__(self, api_key: str | None = None):
        self._api_key = (api_key or os.environ.get("PACKLINK_API_KEY") or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise PacklinkConfigurationError("PACKLINK_API_KEY is not configured.")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": self._api_key,
            "User-Agent": "BT38-FBM/1.0",
        }

    def _request_json(self, method: str, endpoint: str, *, query=None, body=None) -> Any:
        url = PACKLINK_BASE_URL + endpoint.lstrip("/")
        if query:
            url += "?" + urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url=url, method=method.upper(), headers=self._headers(), data=data)
        try:
            with urlopen(request, timeout=PACKLINK_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", 200) or 200)
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            message = "Packlink request failed."
            try:
                payload = json.loads(raw) if raw else {}
                if isinstance(payload, dict):
                    message = str(payload.get("message") or payload.get("error") or payload.get("detail") or message)
            except Exception:
                pass
            raise PacklinkRequestError(message, status_code=exc.code) from exc
        except URLError as exc:
            raise PacklinkRequestError("Packlink could not be reached.") from exc
        if status < 200 or status >= 300:
            raise PacklinkRequestError("Packlink request failed.", status_code=status)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PacklinkRequestError("Packlink returned an invalid JSON response.", status_code=status) from exc

    def _get_json(self, endpoint: str, *, query=None) -> Any:
        return self._request_json("GET", endpoint, query=query)

    def _post_json(self, endpoint: str, body: dict[str, Any]) -> Any:
        return self._request_json("POST", endpoint, body=body)

    def connection_check(self) -> PacklinkConnectionResult:
        if not self.configured:
            return PacklinkConnectionResult(False, False, None, message="PACKLINK_API_KEY is not configured.")
        try:
            payload = self._get_json("users/api/keys")
        except PacklinkRequestError as exc:
            return PacklinkConnectionResult(False, True, exc.status_code, message=str(exc))
        returned_token = str(payload.get("token") or "").strip() if isinstance(payload, dict) else ""
        if not returned_token:
            return PacklinkConnectionResult(False, True, 200, message="Packlink did not confirm the configured API key.")
        return PacklinkConnectionResult(True, True, 200, account_country=PACKLINK_ACCOUNT_COUNTRY, message="Packlink PRO authentication succeeded.")

    def register_callback(self, callback_url: str) -> bool:
        callback_url = str(callback_url or "").strip()
        if not callback_url.startswith("https://"):
            raise PacklinkConfigurationError("Packlink callback URL must use HTTPS.")
        payload = self._post_json("shipments/callback", {"url": callback_url})
        if payload is None:
            return True
        if isinstance(payload, bool):
            return payload
        if isinstance(payload, dict):
            return payload.get("success") is not False
        return bool(payload)

    def get_rates(self, *, order: Any, parcel: dict) -> list[dict]:
        destination = ship_to(order)
        missing_destination = [field for field in ("name", "address1", "city", "postcode", "country", "phone") if not destination.get(field)]
        if missing_destination:
            raise PacklinkConfigurationError("Missing Packlink destination fields: " + ", ".join(missing_destination))
        required = ("from_country", "from_zip", "to_country", "to_zip", "width_cm", "height_cm", "length_cm", "weight_kg")
        missing = [name for name in required if parcel.get(name) in (None, "")]
        if missing:
            raise PacklinkConfigurationError("Missing Packlink rate fields: " + ", ".join(missing))
        parcel["_packlink_handoff_destination"] = dict(destination)
        query = [
            ("from[country]", str(parcel["from_country"])), ("from[zip]", str(parcel["from_zip"])),
            ("to[country]", str(parcel["to_country"])), ("to[zip]", str(parcel["to_zip"])),
            ("packages[0][width]", str(parcel["width_cm"])), ("packages[0][height]", str(parcel["height_cm"])),
            ("packages[0][length]", str(parcel["length_cm"])), ("packages[0][weight]", str(parcel["weight_kg"])),
        ]
        payload = self._get_json("services", query=query)
        if not isinstance(payload, list):
            return []
        return [self._normalise_rate(rate) for rate in payload if isinstance(rate, dict)]

    def create_shipment_draft(self, *, order: Any, parcel: dict[str, Any], rate: dict[str, Any]) -> dict[str, Any]:
        """Create a Packlink draft and verify the exact provider draft once before success."""
        service_id = str(rate.get("service_id") or rate.get("id") or "").strip()
        if not service_id:
            raise PacklinkConfigurationError("Selected Packlink service ID is missing.")
        origin = ship_from()
        stored_destination = parcel.get("_packlink_handoff_destination")
        destination = dict(stored_destination) if isinstance(stored_destination, dict) else ship_to(order)
        for field in ("name", "address1", "city", "postcode", "country", "phone"):
            if not destination.get(field):
                raise PacklinkConfigurationError(f"Destination {field} is missing from the BT38 order.")
        for field in ("weight_kg", "width_cm", "height_cm", "length_cm"):
            if not parcel.get(field):
                raise PacklinkConfigurationError(f"Parcel {field} is missing.")
        account = self._get_json("clients")
        platform_country = str((account or {}).get("country") or origin.get("country") or "GB").upper() if isinstance(account, dict) else str(origin.get("country") or "GB").upper()
        customer_name, customer_surname = self._split_name(destination.get("name"), fallback_surname="Customer")
        sender_name, sender_surname = self._split_name(origin.get("name") or "B & T Outlet", fallback_surname="Outlet")
        lines = order_lines(order)
        content_parts: list[str] = []
        content_value = 0.0
        for line in lines:
            qty = max(1, int(getattr(line, "quantity", 1) or 1))
            sku = str(getattr(line, "sku", "Item") or "Item").strip() or "Item"
            content_parts.append(f"{qty} {sku}")
            unit_price = self._positive_amount(getattr(line, "unit_price", None))
            if unit_price is not None:
                content_value += unit_price * qty
        if content_value <= 0:
            content_value = PACKLINK_DEFAULT_CONTENT_VALUE
        from_address = {
            "name": sender_name, "surname": sender_surname, "company": origin.get("company") or "B & T Outlet",
            "street1": origin.get("address1"), "street2": origin.get("address2") or "",
            "zip_code": self._clean_postcode(origin.get("postcode")), "city": origin.get("city"),
            "state": origin.get("region") or None, "country": self._clean_country(origin.get("country") or "GB"),
            "phone": origin.get("phone") or "", "email": origin.get("email") or "",
        }
        to_address = {
            "name": customer_name, "surname": customer_surname, "company": destination.get("company") or "",
            "street1": destination.get("address1"), "street2": destination.get("address2") or "",
            "zip_code": self._clean_postcode(destination.get("postcode")), "city": destination.get("city"),
            "state": destination.get("region") or None, "country": self._clean_country(destination.get("country") or "GB"),
            "phone": destination.get("phone") or "", "email": destination.get("email") or "",
        }
        custom_reference = str(getattr(order, "marketplace_order_id", ""))[:50]
        content = ", ".join(content_parts)[:60] or "Goods"
        content_value = round(content_value, 2)
        items = [{"title": str(getattr(line, "sku", "Item") or "Item"), "quantity": max(1, int(getattr(line, "quantity", 1) or 1)), "price": (self._positive_amount(getattr(line, "unit_price", None)) or 0.0) * max(1, int(getattr(line, "quantity", 1) or 1))} for line in lines]
        location_data = self._best_effort_location_ids(from_address, to_address)
        additional_data = {
            "postal_zone_id_from": self._selector_id(location_data.get("postal_zone_id_from")),
            "postal_zone_id_to": None,
            "shipping_service_name": rate.get("service_name") or rate.get("service") or None,
            "zip_code_id_from": self._selector_id(location_data.get("zip_code_id_from")),
            "zip_code_id_to": self._selector_id(location_data.get("zip_code_id_to")),
            "selectedWarehouseId": None, "parcel_Ids": [], "postal_zone_name_to": None,
            "order_id": custom_reference, "seller_user_id": None, "items": items,
        }
        body = {
            "user_id": (account or {}).get("id") if isinstance(account, dict) else None,
            "client_id": (account or {}).get("client_id") if isinstance(account, dict) else None,
            "platform": PACKLINK_PLATFORM, "platform_country": platform_country, "source": PACKLINK_DRAFT_SOURCE,
            "from": from_address, "to": to_address,
            "service": rate.get("service_name") or rate.get("service") or "",
            "carrier": rate.get("carrier_name") or rate.get("carrier") or "",
            "service_id": int(service_id) if service_id.isdigit() else service_id,
            "packages": [{"width": int(round(float(parcel["width_cm"]))), "height": int(round(float(parcel["height_cm"]))), "length": int(round(float(parcel["length_cm"]))), "weight": round(float(parcel["weight_kg"]), 2)}],
            "content": content, "contentvalue": content_value, "content_second_hand": False,
            "shipment_custom_reference": custom_reference, "priority": False, "contentValue_currency": "GBP",
            "has_customs": False, "additional_data": additional_data,
        }
        payload = self._post_json("shipments", body)
        provider_reference = ""
        if isinstance(payload, dict):
            provider_reference = str(payload.get("shipment_reference") or payload.get("reference") or "").strip()
        if not provider_reference:
            raise PacklinkRequestError("Packlink created no shipment reference.")
        provider_snapshot = self.get_shipment(provider_reference)
        missing_fields = self._draft_required_fields_missing(provider_snapshot)
        if missing_fields:
            raise PacklinkRequestError("Packlink handoff incomplete; provider draft is missing required fields: " + ", ".join(missing_fields))
        return {"reference": provider_reference, "payment_status": "pending_packlink_payment", "label_ready": False, "raw": payload, "verified": True}

    def _best_effort_location_ids(self, from_address: dict[str, Any], to_address: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for suffix, address in (("from", from_address), ("to", to_address)):
            try:
                country = self._clean_country(address.get("country") or PACKLINK_ACCOUNT_COUNTRY)
                postcode = self._clean_postcode(address.get("zip_code"))
                if not postcode:
                    continue
                try:
                    zones_payload = self._get_json("locations/postalzones/destinations", query={"platform": PACKLINK_PLATFORM, "platform_country": country, "language": "en"})
                    zones = zones_payload if isinstance(zones_payload, list) else []
                    if isinstance(zones_payload, dict):
                        for key in ("items", "results", "destinations", "postalzones", "postal_zones"):
                            candidate = zones_payload.get(key)
                            if isinstance(candidate, list):
                                zones = candidate
                                break
                    zone = next((item for item in zones if isinstance(item, dict) and self._clean_country(self._first_scalar(item.get("isoCode"), item.get("iso_code"), item.get("countryCode"), item.get("country_code"), item.get("code"), item.get("value")) or "") == country), zones[0] if len(zones) == 1 and isinstance(zones[0], dict) else None)
                    if isinstance(zone, dict):
                        selector_zone_id = self._selector_id(self._first_scalar(zone.get("id"), zone.get("postalZoneId"), zone.get("postal_zone_id"), zone.get("postalzone_id")))
                        zone_name = self._first_scalar(zone.get("name"), zone.get("label"))
                        if selector_zone_id:
                            result[f"postal_zone_id_{suffix}"] = selector_zone_id
                        if zone_name and suffix == "to":
                            result["postal_zone_name_to"] = zone_name
                except Exception:
                    pass
                endpoint = f"locations/postalcodes/{quote(country, safe='')}/{quote(postcode, safe='')}"
                payload = self._get_json(endpoint)
                row = self._canonical_postcode_row(payload)
                if not isinstance(row, dict):
                    continue
                country_obj = row.get("country") if isinstance(row.get("country"), dict) else {}
                city_obj = row.get("city") if isinstance(row.get("city"), dict) else {}
                postcode_obj = row.get("postcode") if isinstance(row.get("postcode"), dict) else {}
                postal_zone = row.get("postal_zone") if isinstance(row.get("postal_zone"), dict) else {}
                canonical_country_raw = self._first_scalar(row.get("country_code"), row.get("countryCode"), row.get("iso_code"), row.get("isoCode"), country_obj.get("country_code"), country_obj.get("countryCode"), country_obj.get("iso_code"), country_obj.get("isoCode"), country_obj.get("code"), country_obj.get("value"), country)
                canonical_country = self._clean_country(canonical_country_raw or country)
                canonical_postcode_raw = self._first_scalar(row.get("zipcode"), row.get("zip_code"), row.get("zipCode"), row.get("postcode") if not isinstance(row.get("postcode"), (dict, list)) else None, postcode_obj.get("zipcode"), postcode_obj.get("zip_code"), postcode_obj.get("zipCode"), postcode_obj.get("postcode"), postcode_obj.get("value"), postcode)
                canonical_postcode = self._clean_postcode(canonical_postcode_raw or postcode)
                canonical_city = self._first_scalar(row.get("city") if not isinstance(row.get("city"), (dict, list)) else None, row.get("locality") if not isinstance(row.get("locality"), (dict, list)) else None, row.get("town") if not isinstance(row.get("town"), (dict, list)) else None, row.get("municipality") if not isinstance(row.get("municipality"), (dict, list)) else None, city_obj.get("name"), city_obj.get("label"), city_obj.get("city"), city_obj.get("locality"), city_obj.get("town"), city_obj.get("municipality"), city_obj.get("value"))
                if canonical_country:
                    address["country"] = canonical_country
                if canonical_postcode:
                    address["zip_code"] = canonical_postcode
                if canonical_city:
                    address["city"] = canonical_city
                postcode_id = self._first_scalar(row.get("id"), row.get("zip_code_id"), row.get("zipCodeId"), row.get("postcode_id"), row.get("uuid"), postcode_obj.get("id"), city_obj.get("id"))
                fallback_zone_id = self._first_scalar(row.get("postal_zone_id"), row.get("postalZoneId"), row.get("postalzone_id"), postal_zone.get("id"), country_obj.get("postal_zone_id"), country_obj.get("postalZoneId"), country_obj.get("id"))
                fallback_zone_name = self._first_scalar(row.get("postal_zone_name"), row.get("postalZoneName"), row.get("postalzone_name"), postal_zone.get("name"), postal_zone.get("label"), country_obj.get("name"), country_obj.get("label"))
                if f"postal_zone_id_{suffix}" not in result:
                    selector_zone_id = self._selector_id(fallback_zone_id)
                    if selector_zone_id:
                        result[f"postal_zone_id_{suffix}"] = selector_zone_id
                if suffix == "to" and "postal_zone_name_to" not in result and fallback_zone_name:
                    result["postal_zone_name_to"] = fallback_zone_name
                selector_postcode_id = self._selector_id(postcode_id)
                if selector_postcode_id:
                    result[f"zip_code_id_{suffix}"] = selector_postcode_id
            except (PacklinkRequestError, PacklinkConfigurationError, TypeError, ValueError, KeyError):
                continue
        return result

    @staticmethod
    def _canonical_postcode_row(payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, list):
            return next((item for item in payload if isinstance(item, dict)), None)
        if not isinstance(payload, dict):
            return None
        for key in ("data", "result", "postcode", "location"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                return nested
        for key in ("items", "results", "postcodes", "locations"):
            nested = payload.get(key)
            if isinstance(nested, list):
                return next((item for item in nested if isinstance(item, dict)), None)
        return payload

    @staticmethod
    def _first_scalar(*values: Any) -> str | int | float | None:
        for value in values:
            if value is None or value == "":
                continue
            if isinstance(value, (dict, list, tuple, set)):
                continue
            if isinstance(value, (str, int, float)):
                text = value.strip() if isinstance(value, str) else value
                if text != "":
                    return text
        return None

    @staticmethod
    def _selector_id(value: Any) -> str | None:
        if value is None or value == "" or isinstance(value, (dict, list, tuple, set)):
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _draft_required_fields_missing(cls, payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload, dict):
            return ["sender details", "recipient details"]
        from_address = cls._shipment_address(payload, "from")
        to_address = cls._shipment_address(payload, "to")
        missing: list[str] = []
        required = {
            "name": ("name", "first_name", "firstname"),
            "surname": ("surname", "last_name", "lastname"),
            "email": ("email",),
            "phone": ("phone", "mobile", "mobile_phone", "telephone"),
            "address": ("street1", "address1", "address", "street"),
            "city": ("city", "locality", "town"),
            "postcode": ("zip_code", "zipcode", "zip", "postcode", "postal_code"),
        }
        for field, aliases in required.items():
            if not cls._address_has_value(from_address, aliases):
                missing.append(f"Sender {field}")
        if not cls._address_has_value(from_address, ("country", "country_code", "countryCode")):
            missing.append("Sender country")
        for field, aliases in required.items():
            if not cls._address_has_value(to_address, aliases):
                missing.append(f"Recipient {field}")
        if not cls._recipient_country_selector_valid(to_address):
            missing.append("Recipient country")
        return missing

    @staticmethod
    def _shipment_address(payload: dict[str, Any], side: str) -> dict[str, Any]:
        aliases = (("from", "from_address", "sender", "origin") if side == "from" else ("to", "to_address", "recipient", "destination"))
        for key in aliases:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        shipment = payload.get("shipment")
        if isinstance(shipment, dict):
            for key in aliases:
                value = shipment.get(key)
                if isinstance(value, dict):
                    return value
        return {}

    @staticmethod
    def _address_has_value(address: dict[str, Any], aliases: tuple[str, ...]) -> bool:
        if not isinstance(address, dict):
            return False
        for key in aliases:
            value = address.get(key)
            if isinstance(value, dict):
                for nested_key in ("code", "iso_code", "isoCode", "value", "name", "label"):
                    nested = value.get(nested_key)
                    if nested is not None and str(nested).strip():
                        return True
                continue
            if value is not None and str(value).strip():
                return True
        return False

    @staticmethod
    def _recipient_country_selector_valid(address: dict[str, Any]) -> bool:
        """Reject a display label alone; require Packlink's actual country selector identity."""
        if not isinstance(address, dict):
            return False
        for key in ("country_code", "countryCode", "iso_code", "isoCode"):
            value = address.get(key)
            if value is not None:
                text = str(value).strip().upper()
                if len(text) == 2 and text.isalpha():
                    return True
        country = address.get("country")
        if isinstance(country, dict):
            for key in ("id", "country_id", "countryId"):
                value = country.get(key)
                if value is not None and str(value).strip():
                    return True
            for key in ("code", "country_code", "countryCode", "iso_code", "isoCode", "value"):
                value = country.get(key)
                if value is not None:
                    text = str(value).strip().upper()
                    if len(text) == 2 and text.isalpha():
                        return True
            return False
        if country is not None:
            text = str(country).strip().upper()
            return len(text) == 2 and text.isalpha()
        return False

    def get_shipment(self, reference: str) -> dict[str, Any]:
        payload = self._get_json(f"shipments/{reference}")
        if not isinstance(payload, dict):
            return {}
        result = dict(payload)
        carrier = payload.get("carrier")
        if isinstance(carrier, dict):
            result["carrier"] = carrier.get("name") or carrier.get("label") or carrier.get("code")
            result.setdefault("carrier_id", carrier.get("id") or carrier.get("code"))
        service = payload.get("service")
        if isinstance(service, dict):
            result["service"] = service.get("name") or service.get("label") or service.get("code")
            result.setdefault("service_id", service.get("id") or service.get("code"))
        return result

    def get_labels(self, reference: str) -> list[Any]:
        try:
            payload = self._get_json(f"shipments/{reference}/labels")
        except PacklinkRequestError as exc:
            if exc.status_code == 404:
                return []
            raise
        return payload if isinstance(payload, list) else []

    def get_tracking_status(self, *, reference: str) -> list[dict[str, Any]]:
        try:
            payload = self._get_json(f"shipments/{reference}/track")
        except PacklinkRequestError as exc:
            if exc.status_code == 404:
                return []
            raise
        if isinstance(payload, dict):
            payload = payload.get("history") or []
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def purchase_label(self, **_: Any) -> dict:
        raise NotImplementedError("Packlink integration creates shipment drafts but does not expose a documented BT38-safe payment call.")

    def open_case(self, **_: Any) -> dict:
        raise NotImplementedError("Packlink case opening is not enabled yet.")

    def create_return_label(self, **_: Any) -> dict:
        raise NotImplementedError("Packlink return labels are not enabled yet.")

    @staticmethod
    def _is_amazon_order(order: Any) -> bool:
        store = getattr(order, "store", None)
        return str(getattr(store, "platform", "") or "").strip().casefold() == "amazon"

    @staticmethod
    def _split_name(value: Any, *, fallback_surname: str) -> tuple[str, str]:
        text = " ".join(str(value or "").strip().split())
        if not text:
            return "Customer", fallback_surname
        parts = text.split(" ")
        if len(parts) == 1:
            return parts[0], fallback_surname
        return " ".join(parts[:-1]), parts[-1]

    @staticmethod
    def _company_contact_name(value: Any) -> tuple[str, str]:
        text = " ".join(str(value or "B & T OUTLET LTD").strip().split())
        if text.upper().startswith("B & T OUTLET"):
            return "B & T", "Outlet"
        parts = text.split(" ")
        if len(parts) == 1:
            return parts[0], "Company"
        return " ".join(parts[:-1]), parts[-1]

    @staticmethod
    def _line_description(line: Any, *, fallback: str) -> str:
        for attr in ("title", "product_title", "item_title", "name"):
            value = " ".join(str(getattr(line, attr, "") or "").strip().split())
            if value:
                return value
        warehouse = getattr(line, "warehouse_stock", None)
        value = " ".join(str(getattr(warehouse, "product_name", "") or "").strip().split()) if warehouse is not None else ""
        return value or fallback

    @staticmethod
    def _positive_amount(value: Any) -> float | None:
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        return amount if amount > 0 else None

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if isinstance(value, (dict, list, tuple, set)):
            return None
        text = " ".join(str(value or "").strip().split())
        return text or None

    @classmethod
    def _clean_postcode(cls, value: Any) -> str | None:
        text = cls._clean_text(value)
        return text.upper() if text else None

    @staticmethod
    def _clean_country(value: Any) -> str:
        if isinstance(value, (dict, list, tuple, set)):
            value = PACKLINK_ACCOUNT_COUNTRY
        text = str(value or PACKLINK_ACCOUNT_COUNTRY).strip().upper()
        aliases = {"UNITED KINGDOM": "GB", "UK": "GB", "GREAT BRITAIN": "GB"}
        return aliases.get(text, text) or PACKLINK_ACCOUNT_COUNTRY

    @staticmethod
    def _address_diagnostic(address: dict[str, Any]) -> str:
        if not address:
            return "missing"
        return "/".join(str(address.get(key) or "-").strip() for key in ("country", "zip_code", "city", "state"))

    @staticmethod
    def _normalise_rate(rate: dict[str, Any]) -> dict[str, Any]:
        carrier = rate.get("carrier")
        carrier_name = carrier.get("name") or carrier.get("label") if isinstance(carrier, dict) else carrier or rate.get("carrier_name")
        service = rate.get("service")
        service_name = service.get("name") or service.get("label") if isinstance(service, dict) else service or rate.get("name") or rate.get("service_name")
        price = rate.get("price")
        if isinstance(price, dict):
            normal_price = {"value": price.get("total_price") if price.get("total_price") is not None else price.get("value"), "unit": price.get("currency") or rate.get("currency") or "GBP", "base_price": price.get("base_price"), "tax_price": price.get("tax_price"), "total_price": price.get("total_price")}
        else:
            fallback_price = price if price is not None else rate.get("total_price") if rate.get("total_price") is not None else rate.get("base_price")
            normal_price = {"value": fallback_price, "unit": rate.get("currency") or "GBP"} if fallback_price is not None else {}
        service_id = rate.get("service_id") or rate.get("id") or rate.get("serviceId")
        return {"id": service_id, "service_id": service_id, "carrier": carrier_name, "carrier_name": carrier_name, "service": service_name, "service_name": service_name, "price": normal_price, "delivery": rate.get("delivery") or rate.get("delivery_time") or rate.get("estimated_delivery") or rate.get("transit_time"), "raw": rate}
