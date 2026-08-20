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
from urllib.parse import urlencode
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
        return PacklinkConnectionResult(
            True,
            True,
            200,
            account_country=PACKLINK_ACCOUNT_COUNTRY,
            message="Packlink PRO authentication succeeded.",
        )

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
        missing_destination = [
            field for field in ("name", "address1", "city", "postcode", "country", "phone")
            if not destination.get(field)
        ]
        if missing_destination:
            raise PacklinkConfigurationError(
                "Missing Packlink destination fields: " + ", ".join(missing_destination)
            )
        required = (
            "from_country", "from_zip", "to_country", "to_zip",
            "width_cm", "height_cm", "length_cm", "weight_kg",
        )
        missing = [name for name in required if parcel.get(name) in (None, "")]
        if missing:
            raise PacklinkConfigurationError("Missing Packlink rate fields: " + ", ".join(missing))
        query = [
            ("from[country]", str(parcel["from_country"])),
            ("from[zip]", str(parcel["from_zip"])),
            ("to[country]", str(parcel["to_country"])),
            ("to[zip]", str(parcel["to_zip"])),
            ("packages[0][width]", str(parcel["width_cm"])),
            ("packages[0][height]", str(parcel["height_cm"])),
            ("packages[0][length]", str(parcel["length_cm"])),
            ("packages[0][weight]", str(parcel["weight_kg"])),
        ]
        payload = self._get_json("services", query=query)
        if not isinstance(payload, list):
            return []
        return [self._normalise_rate(rate) for rate in payload if isinstance(rate, dict)]

    def create_shipment_draft(self, *, order: Any, parcel: dict[str, Any], rate: dict[str, Any]) -> dict[str, Any]:
        """Create a Packlink draft from marketplace facts and Packlink's selected postcode IDs.

        Country remains the ISO code expected by Packlink (GB). For the editable
        Packlink PRO draft, BT38 additionally resolves the Packlink postal-zone and
        postcode IDs so the United Kingdom + City/Postcode selectors are genuinely
        selected rather than merely displaying address text.
        """
        service_id = str(rate.get("service_id") or rate.get("id") or "").strip()
        if not service_id:
            raise PacklinkConfigurationError("Selected Packlink service ID is missing.")

        origin = ship_from()
        destination = ship_to(order)
        for side, address in (("Sender", origin), ("Destination", destination)):
            for field in ("address1", "city", "postcode", "country", "phone"):
                if not address.get(field):
                    raise PacklinkConfigurationError(f"{side} {field} is missing from BT38.")
        if not destination.get("name"):
            raise PacklinkConfigurationError("Destination name is missing from the BT38 order.")
        for field in ("weight_kg", "width_cm", "height_cm", "length_cm"):
            if not parcel.get(field):
                raise PacklinkConfigurationError(f"Parcel {field} is missing.")

        from_location = self._resolve_postal_location(origin)
        to_location = self._resolve_postal_location(destination)

        customer_name, customer_surname = self._split_name(
            destination.get("name"), fallback_surname="Customer"
        )
        sender_company = str(origin.get("company") or "B & T OUTLET LTD").strip() or "B & T OUTLET LTD"
        sender_name, sender_surname = self._company_contact_name(sender_company)

        content_parts: list[str] = []
        content_value = 0.0
        for line in order_lines(order):
            qty = max(1, int(getattr(line, "quantity", 1) or 1))
            sku = str(getattr(line, "sku", "Item") or "Item").strip() or "Item"
            content_parts.append(self._line_description(line, fallback=sku))
            line_total = self._positive_amount(getattr(line, "line_total", None))
            if line_total is not None:
                content_value += line_total
            else:
                unit_price = self._positive_amount(getattr(line, "unit_price", None))
                if unit_price is not None:
                    content_value += unit_price * qty
        if content_value <= 0:
            try:
                content_value = float(
                    os.environ.get("PACKLINK_DEFAULT_CONTENT_VALUE", PACKLINK_DEFAULT_CONTENT_VALUE)
                )
            except (TypeError, ValueError):
                content_value = PACKLINK_DEFAULT_CONTENT_VALUE
        if content_value <= 0:
            content_value = PACKLINK_DEFAULT_CONTENT_VALUE

        from_address = {
            "name": sender_name,
            "surname": sender_surname,
            "company": sender_company,
            "street1": self._clean_text(origin.get("address1")),
            "street2": self._clean_text(origin.get("address2")),
            "zip_code": self._clean_postcode(origin.get("postcode")),
            "city": self._clean_text(origin.get("city")),
            "state": self._clean_text(origin.get("region")),
            "country": self._clean_country(origin.get("country")),
            "phone": self._clean_text(origin.get("phone")) or "",
            "email": self._clean_text(origin.get("email")),
        }
        to_address = {
            "name": customer_name,
            "surname": customer_surname,
            "company": self._clean_text(destination.get("company")),
            "street1": self._clean_text(destination.get("address1")),
            "street2": self._clean_text(destination.get("address2")),
            "zip_code": self._clean_postcode(destination.get("postcode")),
            "city": self._clean_text(destination.get("city")),
            "state": self._clean_text(destination.get("region")),
            "country": self._clean_country(destination.get("country")),
            "phone": self._clean_text(destination.get("phone")) or "",
            "email": self._clean_text(destination.get("email")),
        }

        content = ", ".join(dict.fromkeys(part for part in content_parts if part))[:60] or "Goods"
        content_value = round(content_value, 2)
        reference = str(getattr(order, "marketplace_order_id", ""))[:50]
        carrier = self._clean_text(rate.get("carrier") or rate.get("carrier_name")) or ""
        service = self._clean_text(rate.get("service") or rate.get("service_name")) or ""

        additional_data = {
            "collection_date": None,
            "collection_time": None,
            "dropoff_point_id": None,
            "content": content,
            "contentvalue": content_value,
            "content_second_hand": False,
            "shipment_custom_reference": reference,
            "priority": False,
            "contentValue_currency": "GBP",
            "from": dict(from_address),
            "to": dict(to_address),
            "postal_zone_id_from": from_location["postal_zone_id"],
            "postal_zone_name_from": from_location["postal_zone_name"],
            "zip_code_id_from": from_location["zip_code_id"],
            "postal_zone_id_to": to_location["postal_zone_id"],
            "postal_zone_name_to": to_location["postal_zone_name"],
            "zip_code_id_to": to_location["zip_code_id"],
        }
        body = {
            "carrier": carrier,
            "service": service,
            "service_id": int(service_id) if service_id.isdigit() else service_id,
            "adult_signature": False,
            "additional_handling": False,
            "insurance": {"amount": 0, "insurance_selected": False},
            "print_in_store_selected": False,
            "proof_of_delivery": False,
            "priority": False,
            "content": content,
            "contentvalue": content_value,
            "currency": "GBP",
            "contentValue_currency": "GBP",
            "content_second_hand": False,
            "from": from_address,
            "to": to_address,
            "additional_data": additional_data,
            "packages": [{
                "width": int(round(float(parcel["width_cm"]))),
                "height": int(round(float(parcel["height_cm"]))),
                "length": int(round(float(parcel["length_cm"]))),
                "weight": round(float(parcel["weight_kg"]), 2),
            }],
            "has_customs": from_address["country"] != to_address["country"],
            "shipment_custom_reference": reference,
            "source": PACKLINK_DRAFT_SOURCE,
        }

        payload = self._post_json("shipments", body)
        provider_reference = ""
        if isinstance(payload, dict):
            provider_reference = str(
                payload.get("shipment_reference") or payload.get("reference") or ""
            ).strip()
        if not provider_reference:
            raise PacklinkRequestError("Packlink created no shipment reference.")

        created = self.get_shipment(provider_reference)
        state = str(created.get("state") or created.get("status") or "").strip().upper()
        if state != "READY_TO_PURCHASE":
            remote_from = created.get("from") if isinstance(created.get("from"), dict) else {}
            remote_to = created.get("to") if isinstance(created.get("to"), dict) else {}
            raise PacklinkRequestError(
                "Packlink created incomplete draft "
                f"{provider_reference}: state={state or 'UNKNOWN'}; "
                f"sender={self._address_diagnostic(remote_from)}; "
                f"receiver={self._address_diagnostic(remote_to)}; "
                f"sender_selection={from_location['postal_zone_name']}/{from_location['zip_code_id']}; "
                f"receiver_selection={to_location['postal_zone_name']}/{to_location['zip_code_id']}"
            )
        return {
            "reference": provider_reference,
            "payment_status": "pending_packlink_payment",
            "label_ready": False,
            "state": state,
            "raw": payload,
        }

    def _resolve_postal_location(self, address: dict[str, Any]) -> dict[str, Any]:
        country = self._clean_country(address.get("country"))
        postcode = self._clean_postcode(address.get("postcode"))
        if not postcode:
            raise PacklinkConfigurationError("Packlink postcode is missing.")

        zones_payload = self._get_json(
            "locations/postalzones/destinations",
            query={"platform": PACKLINK_PLATFORM, "platform_country": PACKLINK_ACCOUNT_COUNTRY},
        )
        zones = self._payload_items(zones_payload)
        wanted_names = {"UNITED KINGDOM", "UK", "GREAT BRITAIN"} if country == "GB" else set()
        zone = next(
            (
                item for item in zones
                if isinstance(item, dict)
                and (
                    self._clean_country(item.get("iso_code") or item.get("country") or item.get("country_code")) == country
                    or str(item.get("name") or item.get("label") or "").strip().upper() in wanted_names
                )
            ),
            None,
        )
        if zone is None:
            raise PacklinkConfigurationError(
                f"Packlink has no postal zone for {country}; City/Postcode cannot be selected."
            )
        zone_id = zone.get("id")
        if zone_id is None:
            zone_id = zone.get("postal_zone_id")
        if zone_id is None:
            zone_id = zone.get("uuid")
        if zone_id is None:
            raise PacklinkConfigurationError("Packlink postal zone has no identifier.")
        zone_name = str(zone.get("name") or zone.get("label") or "").strip()
        if country == "GB":
            zone_name = "United Kingdom"
        elif not zone_name:
            zone_name = country

        postcodes_payload = self._get_json(
            "locations/postalcodes",
            query={"q": postcode, "postalzone": zone_id},
        )
        postcode_items = self._payload_items(postcodes_payload)
        wanted_postcode = self._normalise_postcode(postcode)
        postcode_item = next(
            (
                item for item in postcode_items
                if isinstance(item, dict)
                and self._normalise_postcode(
                    item.get("zipcode") or item.get("zip_code") or item.get("postcode") or item.get("postal_code")
                ) == wanted_postcode
            ),
            None,
        )
        if postcode_item is None and postcode_items:
            postcode_item = next((item for item in postcode_items if isinstance(item, dict)), None)
        if postcode_item is None:
            raise PacklinkConfigurationError(
                f"Packlink could not resolve City/Postcode {postcode} in {zone_name}."
            )
        postcode_id = postcode_item.get("id")
        if postcode_id is None:
            postcode_id = postcode_item.get("zip_code_id")
        if postcode_id is None:
            postcode_id = postcode_item.get("postal_code_id")
        if postcode_id is None:
            postcode_id = postcode_item.get("uuid")
        if postcode_id is None:
            raise PacklinkConfigurationError(
                f"Packlink resolved {postcode} but returned no City/Postcode identifier."
            )
        return {
            "country": country,
            "postal_zone_id": zone_id,
            "postal_zone_name": zone_name,
            "zip_code_id": postcode_id,
        }

    @staticmethod
    def _payload_items(payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("items", "data", "results", "postal_zones", "postcodes"):
                value = payload.get(key)
                if isinstance(value, list):
                    return value
        return []

    @staticmethod
    def _normalise_postcode(value: Any) -> str:
        return "".join(str(value or "").upper().split())

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
        raise NotImplementedError(
            "Packlink integration creates shipment drafts but does not expose a documented BT38-safe payment call."
        )

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
        value = (
            " ".join(str(getattr(warehouse, "product_name", "") or "").strip().split())
            if warehouse is not None else ""
        )
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
        text = " ".join(str(value or "").strip().split())
        return text or None

    @classmethod
    def _clean_postcode(cls, value: Any) -> str | None:
        text = cls._clean_text(value)
        return text.upper() if text else None

    @staticmethod
    def _clean_country(value: Any) -> str:
        text = str(value or PACKLINK_ACCOUNT_COUNTRY).strip().upper()
        aliases = {
            "UNITED KINGDOM": "GB",
            "UK": "GB",
            "GREAT BRITAIN": "GB",
        }
        return aliases.get(text, text) or PACKLINK_ACCOUNT_COUNTRY

    @staticmethod
    def _address_diagnostic(address: dict[str, Any]) -> str:
        """Safe provider read-back for debugging address hydration (no PII fields)."""
        if not address:
            return "missing"
        return "/".join(
            str(address.get(key) or "-").strip()
            for key in ("country", "zip_code", "city", "state")
        )

    @staticmethod
    def _normalise_rate(rate: dict[str, Any]) -> dict[str, Any]:
        carrier = rate.get("carrier")
        carrier_name = (
            carrier.get("name") or carrier.get("label")
            if isinstance(carrier, dict)
            else carrier or rate.get("carrier_name")
        )
        service = rate.get("service")
        service_name = (
            service.get("name") or service.get("label")
            if isinstance(service, dict)
            else service or rate.get("name") or rate.get("service_name")
        )
        price = rate.get("price")
        if isinstance(price, dict):
            normal_price = {
                "value": price.get("total_price") if price.get("total_price") is not None else price.get("value"),
                "unit": price.get("currency") or rate.get("currency") or "GBP",
                "base_price": price.get("base_price"),
                "tax_price": price.get("tax_price"),
                "total_price": price.get("total_price"),
            }
        else:
            fallback_price = (
                price if price is not None
                else rate.get("total_price") if rate.get("total_price") is not None
                else rate.get("base_price")
            )
            normal_price = (
                {"value": fallback_price, "unit": rate.get("currency") or "GBP"}
                if fallback_price is not None else {}
            )
        service_id = rate.get("service_id") or rate.get("id") or rate.get("serviceId")
        return {
            "id": service_id,
            "service_id": service_id,
            "carrier": carrier_name,
            "carrier_name": carrier_name,
            "service": service_name,
            "service_name": service_name,
            "price": normal_price,
            "delivery": (
                rate.get("delivery")
                or rate.get("delivery_time")
                or rate.get("estimated_delivery")
                or rate.get("transit_time")
            ),
            "raw": rate,
        }
