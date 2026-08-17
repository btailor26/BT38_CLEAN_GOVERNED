"""Packlink PRO adapter for BT38 FBM.

Packlink's public integration API supports authenticated service discovery,
shipment draft creation, shipment reads, labels and tracking. Creating a draft
is NOT represented as payment/purchase: Packlink PRO currently keeps incomplete
shipments in Draft/Ready for payment until the merchant completes payment in
Packlink. BT38 must never claim the API key charged postage when it did not.
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
    """Packlink PRO provider adapter using only documented integration calls."""

    capabilities = ProviderCapabilities(
        provider="packlink",
        quotes=True,
        label_purchase=False,  # public integration flow creates a draft; payment remains Packlink-side
        tracking_status=True,
        case_opening=False,
        return_labels=False,
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

    def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        query: list[tuple[str, str]] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
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
                    message = str(
                        payload.get("message")
                        or payload.get("error")
                        or payload.get("detail")
                        or message
                    )
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

    def _get_json(self, endpoint: str, *, query: list[tuple[str, str]] | None = None) -> Any:
        return self._request_json("GET", endpoint, query=query)

    def _post_json(self, endpoint: str, body: dict[str, Any]) -> Any:
        return self._request_json("POST", endpoint, body=body)

    def connection_check(self) -> PacklinkConnectionResult:
        if not self.configured:
            return PacklinkConnectionResult(False, False, None, message="PACKLINK_API_KEY is not configured.")
        try:
            payload = self._get_json("clients")
        except PacklinkRequestError as exc:
            return PacklinkConnectionResult(False, True, exc.status_code, message=str(exc))

        account_country = None
        account_email = None
        if isinstance(payload, dict):
            account_country = str(payload.get("country") or payload.get("platform_country") or "").strip() or None
            account_email = str(payload.get("email") or "").strip() or None
        return PacklinkConnectionResult(
            True, True, 200,
            account_country=account_country,
            account_email=account_email,
            message="Packlink PRO authentication succeeded.",
        )

    def get_rates(self, *, order: Any, parcel: dict) -> list[dict]:
        required = ("from_country", "from_zip", "to_country", "to_zip", "width_cm", "height_cm", "length_cm", "weight_kg")
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
        """Create a Packlink shipment draft. This does not claim payment occurred."""
        service_id = str(rate.get("service_id") or rate.get("id") or "").strip()
        if not service_id:
            raise PacklinkConfigurationError("Selected Packlink service ID is missing.")

        origin = ship_from()
        destination = ship_to(order)
        for field in ("name", "address1", "city", "postcode", "country"):
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
        content_parts = []
        content_value = 0.0
        for line in lines:
            qty = max(1, int(getattr(line, "quantity", 1) or 1))
            sku = str(getattr(line, "sku", "Item") or "Item")
            content_parts.append(f"{qty} {sku}")
            content_value += max(0.0, float(getattr(line, "unit_price", 0) or 0)) * qty
        content = ", ".join(content_parts)[:60] or "Goods"

        body = {
            "user_id": (account or {}).get("id") if isinstance(account, dict) else None,
            "client_id": (account or {}).get("client_id") if isinstance(account, dict) else None,
            "platform": "PRO",
            "platform_country": platform_country,
            "source": "bt38",
            "from": {
                "name": sender_name,
                "surname": sender_surname,
                "company": origin.get("company") or "B & T Outlet",
                "street1": origin.get("address1"),
                "street2": "",
                "zip_code": origin.get("postcode"),
                "city": origin.get("city"),
                "country": origin.get("country") or "GB",
                "phone": origin.get("phone"),
                "email": origin.get("email"),
            },
            "to": {
                "name": customer_name,
                "surname": customer_surname,
                "company": "",
                "street1": destination.get("address1"),
                "street2": "",
                "zip_code": destination.get("postcode"),
                "city": destination.get("city"),
                "country": destination.get("country") or "GB",
                "phone": destination.get("phone") or "",
                "email": destination.get("email") or "",
            },
            "service": rate.get("service_name") or rate.get("service") or "",
            "carrier": rate.get("carrier_name") or rate.get("carrier") or "",
            "service_id": service_id,
            "packages": [{
                "width": int(round(float(parcel["width_cm"]))),
                "height": int(round(float(parcel["height_cm"]))),
                "length": int(round(float(parcel["length_cm"]))),
                "weight": round(float(parcel["weight_kg"]), 2),
            }],
            "content": content,
            "contentvalue": round(content_value, 2),
            "content_second_hand": False,
            "shipment_custom_reference": str(getattr(order, "marketplace_order_id", ""))[:50],
            "priority": False,
            "contentValue_currency": "GBP",
            "has_customs": False,
            "additional_data": {
                "order_id": str(getattr(order, "marketplace_order_id", "")),
                "items": [
                    {
                        "title": str(getattr(line, "sku", "Item") or "Item"),
                        "quantity": max(1, int(getattr(line, "quantity", 1) or 1)),
                        "price": max(0.0, float(getattr(line, "unit_price", 0) or 0)) * max(1, int(getattr(line, "quantity", 1) or 1)),
                    }
                    for line in lines
                ],
            },
        }
        payload = self._post_json("shipments", body)
        reference = str((payload or {}).get("reference") or "").strip() if isinstance(payload, dict) else ""
        if not reference:
            raise PacklinkRequestError("Packlink created no shipment reference.")
        return {
            "reference": reference,
            "payment_status": "pending_packlink_payment",
            "label_ready": False,
            "raw": payload,
        }

    def get_shipment(self, reference: str) -> dict[str, Any]:
        payload = self._get_json(f"shipments/{reference}")
        return payload if isinstance(payload, dict) else {}

    def get_labels(self, reference: str) -> list[Any]:
        payload = self._get_json(f"shipments/{reference}/labels")
        return payload if isinstance(payload, list) else []

    def get_tracking_status(self, *, reference: str) -> list[dict[str, Any]]:
        payload = self._get_json(f"shipments/{reference}/track")
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def purchase_label(self, **_: Any) -> dict:
        raise NotImplementedError(
            "Packlink public integration API creates shipment drafts but does not expose a documented BT38-safe payment call."
        )

    def open_case(self, **_: Any) -> dict:
        raise NotImplementedError("Packlink case opening is not enabled yet.")

    def create_return_label(self, **_: Any) -> dict:
        raise NotImplementedError("Packlink return labels are not enabled yet.")

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
    def _normalise_rate(rate: dict[str, Any]) -> dict[str, Any]:
        carrier = rate.get("carrier")
        if isinstance(carrier, dict):
            carrier_name = carrier.get("name") or carrier.get("label")
        else:
            carrier_name = carrier
        service_name = rate.get("service") or rate.get("name") or rate.get("service_name")
        price = rate.get("price") or rate.get("total_price") or rate.get("base_price")
        if isinstance(price, dict):
            normal_price = price
        else:
            normal_price = {"value": price, "unit": rate.get("currency") or "GBP"} if price is not None else {}
        service_id = rate.get("service_id") or rate.get("id") or rate.get("serviceId")
        return {
            "id": service_id,
            "service_id": service_id,
            "carrier": carrier_name,
            "carrier_name": carrier_name,
            "service": service_name,
            "service_name": service_name,
            "price": normal_price,
            "delivery": rate.get("delivery") or rate.get("delivery_time") or rate.get("estimated_delivery"),
            "raw": rate,
        }
