"""Amazon Shipping API v2 adapter for BT38 FBM.

Amazon is authoritative for on-Amazon Buy Shipping eligibility. This module
uses the existing Store Amazon credentials and never imports orders. Prime/SFP
orders are expected to be locked to this provider by the FBM routing layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from amazon_service_live_patch import _marketplace_for_id, _sp_api_credentials
from services.fbm_order_mapper import ship_from


class AmazonShippingError(RuntimeError):
    pass


@dataclass(frozen=True)
class AmazonRateResult:
    rates: list[dict]
    ineligible_rates: list[dict]


class AmazonShippingAdapter:
    provider = "amazon_buy_shipping"

    def __init__(self, store: Any):
        self.store = store
        self.credentials = getattr(store, "amazon_credentials", None) if store is not None else None
        if not self.credentials or not getattr(self.credentials, "is_valid", lambda: False)():
            raise AmazonShippingError("Amazon credentials are not configured for this store.")

    def _client(self):
        try:
            from sp_api.api import ShippingV2
            from sp_api.base import Marketplaces
        except Exception as exc:
            raise AmazonShippingError("Installed amazon-sp-api library does not expose ShippingV2.") from exc

        creds = {
            "refresh_token": self.credentials.refresh_token,
            "lwa_app_id": self.credentials.lwa_app_id,
            "lwa_client_secret": self.credentials.lwa_client_secret,
            "seller_id": self.credentials.seller_id,
            "marketplace_id": self.credentials.marketplace_id,
            "aws_access_key_id": getattr(self.credentials, "aws_access_key_id", None),
            "aws_secret_access_key": getattr(self.credentials, "aws_secret_access_key", None),
            "role_arn": getattr(self.credentials, "aws_user_arn", None),
        }
        return ShippingV2(
            credentials=_sp_api_credentials(creds),
            marketplace=_marketplace_for_id(self.credentials.marketplace_id, Marketplaces),
        )

    @staticmethod
    def _response_payload(response: Any) -> dict:
        payload = getattr(response, "payload", None)
        if payload is None and hasattr(response, "json"):
            payload = response.json()
        if payload is None and isinstance(response, dict):
            payload = response.get("payload", response)
        if not isinstance(payload, dict):
            raise AmazonShippingError("Amazon Shipping returned an unexpected response.")
        return payload

    @staticmethod
    def _address_payload(address: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": address.get("name") or address.get("company") or "B & T Outlet",
            "addressLine1": address.get("address1") or "",
            "city": address.get("city") or "",
            "postalCode": address.get("postcode") or "",
            "countryCode": address.get("country") or "GB",
            "email": address.get("email") or None,
            "phoneNumber": address.get("phone") or None,
        }

    def get_rates(self, *, order: Any, parcel: dict[str, Any]) -> AmazonRateResult:
        required = ("weight_kg", "length_cm", "width_cm", "height_cm")
        missing = [field for field in required if not parcel.get(field)]
        if missing:
            raise AmazonShippingError("Missing parcel fields: " + ", ".join(missing))
        if not getattr(order, "marketplace_order_item_id", None):
            raise AmazonShippingError("Amazon order item ID is missing from the BT38 DB order.")

        origin = ship_from()
        quantity = max(1, int(getattr(order, "quantity", 1) or 1))
        unit_value = float(getattr(order, "unit_price", 0) or 0)
        total_weight_g = round(float(parcel["weight_kg"]) * 1000, 3)
        item_weight_g = max(1.0, total_weight_g / quantity)
        sku = str(getattr(order, "sku", "") or "Item")

        body = {
            "shipFrom": self._address_payload(origin),
            "packages": [
                {
                    "dimensions": {
                        "length": float(parcel["length_cm"]),
                        "width": float(parcel["width_cm"]),
                        "height": float(parcel["height_cm"]),
                        "unit": "CENTIMETER",
                    },
                    "weight": {"unit": "GRAM", "value": total_weight_g},
                    "insuredValue": {"value": max(0.0, unit_value * quantity), "unit": "GBP"},
                    "isHazmat": False,
                    "packageClientReferenceId": f"BT38-{order.id}",
                    "items": [
                        {
                            "itemValue": {"value": max(0.0, unit_value), "unit": "GBP"},
                            "description": sku[:100],
                            "itemIdentifier": str(order.marketplace_order_item_id),
                            "quantity": quantity,
                            "weight": {"unit": "GRAM", "value": item_weight_g},
                            "isHazmat": False,
                        }
                    ],
                }
            ],
            "channelDetails": {
                "channelType": "AMAZON",
                "amazonOrderDetails": {"orderId": str(order.marketplace_order_id)},
            },
        }

        client = self._client()
        method = getattr(client, "get_rates", None)
        if method is None:
            raise AmazonShippingError("Installed amazon-sp-api ShippingV2 client does not implement get_rates().")
        response = method(body=body)
        payload = self._response_payload(response)
        return AmazonRateResult(
            rates=self._normalise_rates(payload.get("rates") or []),
            ineligible_rates=self._normalise_ineligible(payload.get("ineligibleRates") or []),
        )

    def purchase_shipment(
        self,
        *,
        order: Any,
        parcel: dict[str, Any],
        rate_id: str,
        label_specification: dict[str, Any],
    ) -> dict[str, Any]:
        """Purchase exactly one Amazon Buy Shipping rate.

        Idempotency is enforced by the caller using FBMShipment before this write
        is allowed. The selected document specification must come from getRates.
        """
        if not rate_id:
            raise AmazonShippingError("Amazon rate ID is required.")
        if not label_specification:
            raise AmazonShippingError("Amazon label specification is required.")

        # Rebuild the same shipment request to prevent rate/order cross-wiring.
        rates = self.get_rates(order=order, parcel=parcel)
        chosen = next((rate for rate in rates.rates if rate.get("rate_id") == rate_id), None)
        if chosen is None:
            raise AmazonShippingError("Selected Amazon rate is no longer eligible for this order.")

        origin = ship_from()
        quantity = max(1, int(getattr(order, "quantity", 1) or 1))
        unit_value = float(getattr(order, "unit_price", 0) or 0)
        total_weight_g = round(float(parcel["weight_kg"]) * 1000, 3)
        item_weight_g = max(1.0, total_weight_g / quantity)
        sku = str(getattr(order, "sku", "") or "Item")
        package = {
            "dimensions": {
                "length": float(parcel["length_cm"]), "width": float(parcel["width_cm"]),
                "height": float(parcel["height_cm"]), "unit": "CENTIMETER",
            },
            "weight": {"unit": "GRAM", "value": total_weight_g},
            "insuredValue": {"value": max(0.0, unit_value * quantity), "unit": "GBP"},
            "isHazmat": False,
            "packageClientReferenceId": f"BT38-{order.id}",
            "items": [{
                "itemValue": {"value": max(0.0, unit_value), "unit": "GBP"},
                "description": sku[:100],
                "itemIdentifier": str(order.marketplace_order_item_id),
                "quantity": quantity,
                "weight": {"unit": "GRAM", "value": item_weight_g},
                "isHazmat": False,
            }],
        }
        body = {
            "shipFrom": self._address_payload(origin),
            "packages": [package],
            "channelDetails": {"channelType": "AMAZON", "amazonOrderDetails": {"orderId": str(order.marketplace_order_id)}},
            "labelSpecifications": label_specification,
            "serviceSelection": {"rateId": rate_id},
        }

        client = self._client()
        method = getattr(client, "purchase_shipment", None)
        if method is None:
            raise AmazonShippingError("Installed amazon-sp-api ShippingV2 client does not implement purchase_shipment().")
        response = method(body=body)
        return self._response_payload(response)

    @staticmethod
    def _normalise_rates(rates: list[Any]) -> list[dict]:
        result: list[dict] = []
        for rate in rates:
            if not isinstance(rate, dict):
                continue
            price = rate.get("totalCharge") or rate.get("billedWeight") or {}
            documents = rate.get("supportedDocumentSpecifications") or []
            result.append({
                "rate_id": rate.get("rateId"),
                "carrier_id": rate.get("carrierId"),
                "carrier_name": rate.get("carrierName"),
                "service_id": rate.get("serviceId"),
                "service_name": rate.get("serviceName"),
                "price": price,
                "promise": rate.get("promise"),
                "supported_documents": documents,
                "benefits": rate.get("benefits"),
                "raw": rate,
            })
        return result

    @staticmethod
    def _normalise_ineligible(rates: list[Any]) -> list[dict]:
        return [rate for rate in rates if isinstance(rate, dict)]
