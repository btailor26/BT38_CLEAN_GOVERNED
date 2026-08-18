"""Amazon Shipping API v2 adapter for BT38 FBM.

Amazon is authoritative for on-Amazon Buy Shipping eligibility. This module
uses the existing Store Amazon credentials and never imports orders. Prime/SFP
orders are expected to be locked to this provider by the FBM routing layer.

The deployed python-amazon-sp-api build does not guarantee that ShippingV2 is
exported as a service class. BT38 therefore uses the library's stable generic
Client + sp_endpoint transport for the Shipping v2 operations needed here.
Authentication/signing remains owned by python-amazon-sp-api; no parallel raw
HTTP signing path is introduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sp_api.base import Client, sp_endpoint

from amazon_service_live_patch import _marketplace_for_id, _sp_api_credentials
from services.fbm_order_mapper import order_lines, ship_from


class AmazonShippingError(RuntimeError):
    pass


class _BT38ShippingV2(Client):
    """Minimal Amazon Shipping API v2 client on the installed SP-API transport."""

    @sp_endpoint("/shipping/v2/shipments/rates", method="POST")
    def get_rates(self, *, body: dict[str, Any], **kwargs):
        return self._request(kwargs.pop("path"), data=body)

    @sp_endpoint("/shipping/v2/shipments", method="POST")
    def purchase_shipment(self, *, body: dict[str, Any], **kwargs):
        return self._request(kwargs.pop("path"), data=body)

    @sp_endpoint("/shipping/v2/tracking")
    def get_tracking(self, *, trackingId: str, carrierId: str, **kwargs):
        return self._request(
            kwargs.pop("path"),
            params={"trackingId": trackingId, "carrierId": carrierId},
        )


@dataclass(frozen=True)
class AmazonRateResult:
    request_token: str | None
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
            from sp_api.base import Marketplaces
        except Exception as exc:
            raise AmazonShippingError("Installed amazon-sp-api library is missing its base client support.") from exc

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
        return _BT38ShippingV2(
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
    def _address_payload(address: dict[str, Any], *, fallback_name: str) -> dict[str, Any]:
        payload = {
            "name": address.get("name") or address.get("company") or fallback_name,
            "addressLine1": address.get("address1") or "",
            "city": address.get("city") or "",
            "postalCode": address.get("postcode") or "",
            "countryCode": address.get("country") or "GB",
        }
        if address.get("email"):
            payload["email"] = address["email"]
        if address.get("phone"):
            payload["phoneNumber"] = address["phone"]
        return payload

    def get_rates(self, *, order: Any, parcel: dict[str, Any]) -> AmazonRateResult:
        required = ("weight_kg", "length_cm", "width_cm", "height_cm")
        missing = [field for field in required if not parcel.get(field)]
        if missing:
            raise AmazonShippingError("Missing parcel fields: " + ", ".join(missing))

        lines = order_lines(order)
        missing_item_ids = [line.id for line in lines if not getattr(line, "marketplace_order_item_id", None)]
        if missing_item_ids:
            raise AmazonShippingError("Amazon order item ID is missing from one or more BT38 DB order lines.")

        origin = ship_from()
        total_weight_g = round(float(parcel["weight_kg"]) * 1000, 3)
        total_units = sum(max(1, int(getattr(line, "quantity", 1) or 1)) for line in lines)
        fallback_unit_weight_g = max(1.0, total_weight_g / max(1, total_units))
        total_value = sum(
            max(0.0, float(getattr(line, "unit_price", 0) or 0))
            * max(1, int(getattr(line, "quantity", 1) or 1))
            for line in lines
        )

        items: list[dict[str, Any]] = []
        for line in lines:
            quantity = max(1, int(getattr(line, "quantity", 1) or 1))
            unit_value = max(0.0, float(getattr(line, "unit_price", 0) or 0))
            warehouse = getattr(line, "warehouse_stock", None)
            known_weight_kg = float(getattr(warehouse, "product_weight_kg", 0) or 0) if warehouse is not None else 0.0
            item_weight_g = max(1.0, known_weight_kg * 1000 if known_weight_kg > 0 else fallback_unit_weight_g)
            items.append({
                "itemValue": {"value": unit_value, "unit": "GBP"},
                "description": str(getattr(line, "sku", "") or "Item")[:100],
                "itemIdentifier": str(line.marketplace_order_item_id),
                "quantity": quantity,
                "weight": {"unit": "GRAM", "value": item_weight_g},
                "isHazmat": False,
            })

        # Amazon already owns the buyer/delivery address for an Amazon order.
        # Keep that address inside Amazon for marketplace-native Buy Shipping.
        # BT38 supplies the Amazon order identity plus the packed parcel facts;
        # external providers (for example Packlink) continue to use BT38's
        # persisted ship-to address through their own provider path.
        body = {
            "shipFrom": self._address_payload(origin, fallback_name="B & T Outlet"),
            "packages": [{
                "dimensions": {
                    "length": float(parcel["length_cm"]),
                    "width": float(parcel["width_cm"]),
                    "height": float(parcel["height_cm"]),
                    "unit": "CENTIMETER",
                },
                "weight": {"unit": "GRAM", "value": total_weight_g},
                "insuredValue": {"value": total_value, "unit": "GBP"},
                "isHazmat": False,
                "packageClientReferenceId": f"BT38-{order.store_id}-{order.marketplace_order_id}",
                "items": items,
            }],
            "channelDetails": {
                "channelType": "AMAZON",
                "amazonOrderDetails": {"orderId": str(order.marketplace_order_id)},
            },
        }

        response = self._client().get_rates(body=body)
        payload = self._response_payload(response)
        request_token = str(payload.get("requestToken") or "").strip() or None
        return AmazonRateResult(
            request_token=request_token,
            rates=self._normalise_rates(payload.get("rates") or []),
            ineligible_rates=self._normalise_ineligible(payload.get("ineligibleRates") or []),
        )

    def purchase_shipment(
        self,
        *,
        request_token: str,
        rate_id: str,
        requested_document_specification: dict[str, Any],
        requested_value_added_services: list[dict] | None = None,
        additional_inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Purchase exactly one Amazon Buy Shipping rate using the token from getRates."""
        if not request_token:
            raise AmazonShippingError("Amazon request token is required.")
        if not rate_id:
            raise AmazonShippingError("Amazon rate ID is required.")
        if not requested_document_specification:
            raise AmazonShippingError("Amazon document specification is required.")

        body: dict[str, Any] = {
            "requestToken": request_token,
            "rateId": rate_id,
            "requestedDocumentSpecification": requested_document_specification,
        }
        if requested_value_added_services:
            body["requestedValueAddedServices"] = requested_value_added_services
        if additional_inputs:
            body["additionalInputs"] = additional_inputs

        response = self._client().purchase_shipment(body=body)
        return self._normalise_purchase(self._response_payload(response))

    def get_tracking(self, *, tracking_id: str, carrier_id: str) -> dict[str, Any]:
        if not tracking_id or not carrier_id:
            raise AmazonShippingError("Amazon tracking ID and carrier ID are required.")
        response = self._client().get_tracking(trackingId=tracking_id, carrierId=carrier_id)
        return self._response_payload(response)

    @staticmethod
    def _normalise_rates(rates: list[Any]) -> list[dict]:
        result: list[dict] = []
        for rate in rates:
            if not isinstance(rate, dict):
                continue
            result.append({
                "rate_id": rate.get("rateId"),
                "carrier_id": rate.get("carrierId"),
                "carrier_name": rate.get("carrierName"),
                "service_id": rate.get("serviceId"),
                "service_name": rate.get("serviceName"),
                "price": rate.get("totalCharge") or {},
                "promise": rate.get("promise"),
                "supported_documents": rate.get("supportedDocumentSpecifications") or [],
                "benefits": rate.get("benefits"),
                "requires_additional_inputs": bool(rate.get("requiresAdditionalInputs")),
                "raw": rate,
            })
        return result

    @staticmethod
    def _normalise_ineligible(rates: list[Any]) -> list[dict]:
        return [rate for rate in rates if isinstance(rate, dict)]

    @staticmethod
    def _normalise_purchase(payload: dict[str, Any]) -> dict[str, Any]:
        package_details = payload.get("packageDocumentDetails") or []
        first_package = package_details[0] if package_details and isinstance(package_details[0], dict) else {}
        documents = first_package.get("packageDocuments") or []
        first_label = next(
            (
                doc
                for doc in documents
                if isinstance(doc, dict) and str(doc.get("type") or "").upper() == "LABEL"
            ),
            documents[0] if documents else {},
        )
        return {
            "shipment_id": payload.get("shipmentId"),
            "tracking_id": first_package.get("trackingId"),
            "package_client_reference_id": first_package.get("packageClientReferenceId"),
            "label": {
                "type": first_label.get("type") if isinstance(first_label, dict) else None,
                "format": first_label.get("format") if isinstance(first_label, dict) else None,
                "contents": first_label.get("contents") if isinstance(first_label, dict) else None,
            },
            "promise": payload.get("promise"),
            "benefits": payload.get("benefits"),
            "total_charge": payload.get("totalChargeWithAdjustments"),
            "raw": payload,
        }