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
PACKLINK_DRAFT_SOURCE = "bt38"


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

        parcel["_packlink_handoff_destination"] = dict(destination)

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
        """Send the stored quote snapshot directly to Packlink.

        Location IDs are added when Packlink resolves them, but they are enrichment
        only. A failed location lookup must never block the shipment POST.
        """
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
        platform_country = (
            str((account or {}).get("country") or origin.get("country") or "GB").upper()
            if isinstance(account, dict)
            else str(origin.get("country") or "GB").upper()
        )

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
            "name": sender_name,
            "surname": sender_surname,
            "company": origin.get("company") or "B & T Outlet",
            "street1": origin.get("address1"),
            "street2": origin.get("address2") or "",
            "zip_code": self._clean_postcode(origin.get("postcode")),
            "city": origin.get("city"),
            "state": origin.get("region") or None,
            "country": self._clean_country(origin.get("country") or "GB"),
            "phone": origin.get("phone") or "",
            "email": origin.get("email") or "",
        }
        to_address = {
            "name": customer_name,
            "surname": customer_surname,
            "company": destination.get("company") or "",
            "street1": destination.get("address1"),
            "street2": destination.get("address2") or "",
            "zip_code": self._clean_postcode(destination.get("postcode")),
            "city": destination.get("city"),
            "state": destination.get("region") or None,
            "country": self._clean_country(destination.get("country") or "GB"),
            "phone": destination.get("phone") or "",
            "email": destination.get("email") or "",
        }
        custom_reference = str(getattr(order, "marketplace_order_id", ""))[:50]
        content = ", ".join(content_parts)[:60] or "Goods"
        content_value = round(content_value, 2)

        additional_data = {
            "order_id": custom_reference,
            "from": from_address,
            "to": to_address,
            "content": content,
            "contentvalue": content_value,
            "shipment_custom_reference": custom_reference,
            "contentValue_currency": "GBP",
            "items": [
                {
                    "title": str(getattr(line, "sku", "Item") or "Item"),
                    "quantity": max(1, int(getattr(line, "quantity", 1) or 1)),
                    "price": (
                        (self._positive_amount(getattr(line, "unit_price", None)) or 0.0)
                        * max(1, int(getattr(line, "quantity", 1) or 1))
                    ),
                }
                for line in lines
            ],
        }

        # Packlink's web editor needs its own country/postcode selection IDs.
        # Resolve them best-effort and enrich the shipment, but never fail the
        # handoff if these auxiliary endpoints are unavailable or reject a lookup.
        additional_data.update(self._best_effort_location_ids(from_address, to_address))

        body = {
            "user_id": (account or {}).get("id") if isinstance(account, dict) else None,
            "client_id": (account or {}).get("client_id") if isinstance(account, dict) else None,
            "platform": PACKLINK_PLATFORM,
            "platform_country": platform_country,
            "source": PACKLINK_DRAFT_SOURCE,
            "from": from_address,
            "to": to_address,
            "service": rate.get("service_name") or rate.get("service") or "",
            "carrier": rate.get("carrier_name") or rate.get("carrier") or "",
            "service_id": int(service_id) if service_id.isdigit() else service_id,
            "packages": [{
                "width": int(round(float(parcel["width_cm"]))),
                "height": int(round(float(parcel["height_cm"]))),
                "length": int(round(float(parcel["length_cm"]))),
                "weight": round(float(parcel["weight_kg"]), 2),
            }],
            "content": content,
            "contentvalue": content_value,
            "content_second_hand": False,
            "shipment_custom_reference": custom_reference,
            "priority": False,
            "contentValue_currency": "GBP",
            "has_customs": False,
            "additional_data": additional_data,
        }

        payload = self._post_json("shipments", body)
        provider_reference = ""
        if isinstance(payload, dict):
            provider_reference = str(payload.get("shipment_reference") or payload.get("reference") or "").strip()
        if not provider_reference:
            raise PacklinkRequestError("Packlink created no shipment reference.")

        return {
            "reference": provider_reference,
            "payment_status": "pending_packlink_payment",
            "label_ready": False,
            "raw": payload,
        }

    def _best_effort_location_ids(self, from_address: dict[str, Any], to_address: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for suffix, address in (("from", from_address), ("to", to_address)):
            try:
                country = self._clean_country(address.get("country") or PACKLINK_ACCOUNT_COUNTRY)
                zones = self._get_json(
                    "locations/postalzones/destinations",
                    query={"platform": PACKLINK_PLATFORM, "platform_country": country},
                )
                if not isinstance(zones, list):
                    continue
                zone = next(
                    (
                        item for item in zones
                        if isinstance(item, dict)
                        and self._clean_country(item.get("iso_code") or item.get("country") or "") == country
                    ),
                    zones[0] if zones and isinstance(zones[0], dict) else None,
                )
                if not isinstance(zone, dict) or zone.get("id") in (None, ""):
                    continue
                zone_id = zone.get("id")
                postcode = self._clean_postcode(address.get("zip_code"))
                if not postcode:
                    continue
                postcodes = self._get_json(
                    "locations/postalcodes",
                    query={"q": postcode, "postalzone": zone_id},
                )
                if not isinstance(postcodes, list):
                    continue
                postcode_row = next(
                    (
                        item for item in postcodes
                        if isinstance(item, dict)
                        and self._clean_postcode(item.get("zipcode") or item.get("zip_code")) == postcode
                    ),
                    postcodes[0] if postcodes and isinstance(postcodes[0], dict) else None,
                )
                if not isinstance(postcode_row, dict) or postcode_row.get("id") in (None, ""):
                    continue
                result[f"postal_zone_id_{suffix}"] = zone_id
                if zone.get("name"):
                    result[f"postal_zone_name_{suffix}"] = zone.get("name")
                result[f"zip_code_id_{suffix}"] = postcode_row.get("id")
            except (PacklinkRequestError, PacklinkConfigurationError, TypeError, ValueError, KeyError):
                continue
        return result

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
