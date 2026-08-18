"""Amazon Buy Shipping adapter for BT38 FBM.

Amazon is authoritative for on-Amazon Buy Shipping eligibility, carrier/service
selection, customer delivery details and shipment confirmation. BT38 supplies
only the Amazon order identity, the seller ship-from address and packed parcel
facts required by Amazon's Merchant Fulfillment Buy Shipping operations.

This deliberately does not require BT38's local copy of the customer delivery
address. Prime/SFP orders remain locked to this provider by the FBM routing
layer. The existing BT38 quote, duplicate-purchase, label persistence and QZ
printing contracts remain unchanged.
"""
from __future__ import annotations

import base64
import gzip
import secrets
from dataclasses import dataclass
from typing import Any

from amazon_service_live_patch import _marketplace_for_id, _sp_api_credentials
from services.fbm_order_mapper import order_lines, ship_from


class AmazonShippingError(RuntimeError):
    pass


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
            from sp_api.api import MerchantFulfillment
            from sp_api.base import Marketplaces
        except Exception as exc:
            raise AmazonShippingError("Installed amazon-sp-api library does not expose Merchant Fulfillment Buy Shipping.") from exc

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
        return MerchantFulfillment(
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
        if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
            payload = payload["payload"]
        if not isinstance(payload, dict):
            raise AmazonShippingError("Amazon Buy Shipping returned an unexpected response.")
        return payload

    @staticmethod
    def _ship_from_payload(address: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "Name": address.get("name") or address.get("company") or "B & T Outlet",
            "AddressLine1": address.get("address1") or "",
            "City": address.get("city") or "",
            "PostalCode": address.get("postcode") or "",
            "CountryCode": address.get("country") or "GB",
        }
        if address.get("email"):
            payload["Email"] = address["email"]
        if address.get("phone"):
            payload["Phone"] = address["phone"]
        return payload

    @staticmethod
    def _shipment_request_details(order: Any, parcel: dict[str, Any]) -> dict[str, Any]:
        required = ("weight_kg", "length_cm", "width_cm", "height_cm")
        missing = [field for field in required if not parcel.get(field)]
        if missing:
            raise AmazonShippingError("Missing parcel fields: " + ", ".join(missing))

        lines = order_lines(order)
        missing_item_ids = [line.id for line in lines if not getattr(line, "marketplace_order_item_id", None)]
        if missing_item_ids:
            raise AmazonShippingError("Amazon order item ID is missing from one or more BT38 DB order lines.")

        origin = ship_from()
        details: dict[str, Any] = {
            "AmazonOrderId": str(order.marketplace_order_id),
            "ItemList": [
                {
                    "OrderItemId": str(line.marketplace_order_item_id),
                    "Quantity": max(1, int(getattr(line, "quantity", 1) or 1)),
                }
                for line in lines
            ],
            "ShipFromAddress": AmazonShippingAdapter._ship_from_payload(origin),
            "PackageDimensions": {
                "Length": float(parcel["length_cm"]),
                "Width": float(parcel["width_cm"]),
                "Height": float(parcel["height_cm"]),
                "Unit": "centimeters",
            },
            "Weight": {
                "Value": round(float(parcel["weight_kg"]) * 1000, 3),
                "Unit": "g",
            },
            "ShippingServiceOptions": {
                "DeliveryExperience": "DeliveryConfirmationWithoutSignature",
                "CarrierWillPickUp": False,
                "CarrierWillPickUpOption": "NoPreference",
            },
        }

        # The FBM route refreshes this profile immediately before requesting
        # rates. Amazon's Prime guidance says LatestShipDate should be reused as
        # ShipDate when available; absence of it does not justify inventing one.
        try:
            from fbm_models import FBMOrderProfile
            profile = FBMOrderProfile.query.filter_by(
                store_id=order.store_id,
                marketplace_order_id=order.marketplace_order_id,
            ).first()
            latest_ship = getattr(profile, "latest_ship_at", None) if profile is not None else None
            if latest_ship is not None:
                details["ShipDate"] = latest_ship.isoformat(timespec="seconds") + "Z"
        except Exception:
            pass

        return details

    def get_rates(self, *, order: Any, parcel: dict[str, Any]) -> AmazonRateResult:
        details = self._shipment_request_details(order, parcel)
        try:
            response = self._client().get_eligible_shipment_services(details)
        except Exception as exc:
            raise AmazonShippingError(str(exc)) from exc

        payload = self._response_payload(response)
        rates = self._normalise_rates(payload.get("ShippingServiceList") or [])

        unavailable: list[dict] = []
        for item in payload.get("RejectedShippingServiceList") or []:
            if isinstance(item, dict):
                unavailable.append({"reason": "rejected", **item})
        for item in payload.get("TemporarilyUnavailableCarrierList") or []:
            if isinstance(item, dict):
                unavailable.append({"reason": "temporarily_unavailable", **item})
        for item in payload.get("TermsAndConditionsNotAcceptedCarrierList") or []:
            if isinstance(item, dict):
                unavailable.append({"reason": "terms_not_accepted", **item})

        # Merchant Fulfillment does not return a purchase requestToken. Keep the
        # existing BT38 quote contract by issuing an opaque BT38-only token. The
        # token is used only to locate the exact persisted quote/order at purchase.
        request_token = "mfn_" + secrets.token_urlsafe(24)
        return AmazonRateResult(
            request_token=request_token,
            rates=rates,
            ineligible_rates=unavailable,
        )

    def purchase_shipment(
        self,
        *,
        request_token: str,
        rate_id: str,
        requested_document_specification: dict[str, Any],
        requested_value_added_services: list[dict] | None = None,
        additional_inputs: Any = None,
    ) -> dict[str, Any]:
        """Purchase exactly one Amazon Buy Shipping offer from the persisted quote."""
        if not request_token:
            raise AmazonShippingError("Amazon Buy Shipping quote token is required.")
        if not rate_id:
            raise AmazonShippingError("Amazon Buy Shipping rate ID is required.")

        # Resolve the exact DB quote generated by get_rates. This preserves the
        # existing route signature and duplicate-purchase protection while the
        # actual Amazon purchase uses Merchant Fulfillment's shipment details.
        from fbm_models import FBMRateQuote
        from models import MarketplaceOrder

        quote = FBMRateQuote.query.filter_by(
            store_id=getattr(self.store, "id", None),
            provider="amazon_buy_shipping",
            request_token=request_token,
        ).first()
        if quote is None:
            raise AmazonShippingError("Amazon Buy Shipping quote could not be resolved.")

        selected = next(
            (
                rate for rate in (quote.rates or [])
                if isinstance(rate, dict)
                and str(rate.get("rate_id") or rate.get("service_id") or "") == str(rate_id)
            ),
            None,
        )
        if selected is None:
            raise AmazonShippingError("Selected Amazon Buy Shipping offer is not in the persisted quote.")
        if selected.get("requires_additional_inputs") and not additional_inputs:
            raise AmazonShippingError("This Amazon shipping service requires additional seller inputs before purchase.")

        order = MarketplaceOrder.query.filter_by(
            store_id=quote.store_id,
            marketplace_order_id=quote.marketplace_order_id,
        ).order_by(MarketplaceOrder.id.asc()).first()
        if order is None:
            raise AmazonShippingError("Amazon marketplace order could not be resolved for this shipping quote.")

        details = self._shipment_request_details(order, quote.parcel or {})
        service_id = str(selected.get("service_id") or "").strip()
        offer_id = str(selected.get("offer_id") or "").strip()
        if not service_id:
            raise AmazonShippingError("Amazon shipping service ID is missing from the selected offer.")

        kwargs: dict[str, Any] = {}
        if offer_id:
            kwargs["ShippingServiceOfferId"] = offer_id

        # Merchant Fulfillment selects the requested document format through the
        # same ShippingServiceOptions object used when the offer is purchased.
        label_format = str(
            (requested_document_specification or {}).get("format")
            or (requested_document_specification or {}).get("LabelFormat")
            or ""
        ).strip()
        if label_format and label_format != "ShippingServiceDefault":
            details["ShippingServiceOptions"]["LabelFormat"] = label_format

        if additional_inputs:
            if isinstance(additional_inputs, dict) and additional_inputs.get("ShipmentLevelSellerInputsList"):
                kwargs["ShipmentLevelSellerInputsList"] = additional_inputs["ShipmentLevelSellerInputsList"]
            elif isinstance(additional_inputs, list):
                kwargs["ShipmentLevelSellerInputsList"] = additional_inputs

        try:
            response = self._client().create_shipment(
                shipment_request_details=details,
                shipping_service_id=service_id,
                **kwargs,
            )
        except Exception as exc:
            raise AmazonShippingError(str(exc)) from exc

        return self._normalise_purchase(self._response_payload(response))

    def get_tracking(self, *, tracking_id: str, carrier_id: str) -> dict[str, Any]:
        """Return Amazon shipment status using the shipment created by Buy Shipping."""
        if not tracking_id:
            raise AmazonShippingError("Amazon tracking ID is required.")

        from fbm_models import FBMShipment
        shipment = FBMShipment.query.filter_by(
            store_id=getattr(self.store, "id", None),
            provider="amazon_buy_shipping",
            tracking_number=tracking_id,
        ).order_by(FBMShipment.id.desc()).first()
        shipment_id = getattr(shipment, "provider_shipment_id", None) if shipment is not None else None
        if not shipment_id:
            raise AmazonShippingError("Amazon Buy Shipping shipment ID is not available for tracking refresh.")

        try:
            response = self._client().get_shipment(shipment_id)
        except Exception as exc:
            raise AmazonShippingError(str(exc)) from exc
        payload = self._response_payload(response)
        status = str(payload.get("Status") or payload.get("ShipmentStatus") or "").strip()
        return {"summary": {"status": status}, "shipment": payload}

    @staticmethod
    def _money(rate: Any) -> dict[str, Any]:
        if not isinstance(rate, dict):
            return {}
        amount = rate.get("Amount")
        if amount is None:
            amount = rate.get("CurrencyAmount")
        return {
            "value": amount,
            "unit": rate.get("CurrencyCode") or rate.get("Currency") or "GBP",
        }

    @staticmethod
    def _normalise_rates(rates: list[Any]) -> list[dict]:
        result: list[dict] = []
        for rate in rates:
            if not isinstance(rate, dict):
                continue
            service_id = str(rate.get("ShippingServiceId") or "").strip() or None
            offer_id = str(rate.get("ShippingServiceOfferId") or "").strip() or None
            formats = rate.get("AvailableLabelFormats") or []
            if not formats:
                formats = ["ShippingServiceDefault"]
            documents = [
                {"format": str(fmt), "LabelFormat": str(fmt)}
                for fmt in formats
                if fmt
            ]
            adjusted_rate = rate.get("RateWithAdjustments") or rate.get("Rate") or {}
            result.append({
                "rate_id": offer_id or service_id,
                "offer_id": offer_id,
                "carrier_id": rate.get("CarrierName"),
                "carrier_name": rate.get("CarrierName"),
                "service_id": service_id,
                "service_name": rate.get("ShippingServiceName"),
                "price": AmazonShippingAdapter._money(adjusted_rate),
                "promise": {
                    "ship_date": rate.get("ShipDate"),
                    "earliest_delivery": rate.get("EarliestEstimatedDeliveryDate"),
                    "latest_delivery": rate.get("LatestEstimatedDeliveryDate"),
                },
                "supported_documents": documents,
                "benefits": rate.get("Benefits"),
                "requires_additional_inputs": bool(rate.get("RequiresAdditionalSellerInputs")),
                "raw": rate,
            })
        return result

    @staticmethod
    def _label_format(label: dict[str, Any], file_contents: dict[str, Any]) -> str | None:
        value = str(label.get("LabelFormat") or "").strip()
        if value and value != "ShippingServiceDefault":
            return value.upper()
        file_type = str(file_contents.get("FileType") or "").lower()
        if "pdf" in file_type:
            return "PDF"
        if "png" in file_type:
            return "PNG"
        if "zpl" in file_type:
            return "ZPL"
        return value.upper() or None

    @staticmethod
    def _decompress_label(contents: Any) -> str | None:
        encoded = str(contents or "").strip()
        if not encoded:
            return None
        try:
            compressed = base64.b64decode(encoded)
            try:
                document = gzip.decompress(compressed)
            except OSError:
                document = compressed
            return base64.b64encode(document).decode("ascii")
        except Exception as exc:
            raise AmazonShippingError("Amazon returned a shipping label that BT38 could not decode.") from exc

    @staticmethod
    def _normalise_purchase(payload: dict[str, Any]) -> dict[str, Any]:
        shipping_service = payload.get("ShippingService") or {}
        label = payload.get("Label") or {}
        file_contents = label.get("FileContents") or {}
        dimensions = label.get("Dimensions") or {}
        contents = AmazonShippingAdapter._decompress_label(file_contents.get("Contents"))
        return {
            "shipment_id": payload.get("ShipmentId"),
            "tracking_id": payload.get("TrackingId") or shipping_service.get("TrackingId"),
            "package_client_reference_id": payload.get("ShipmentId"),
            "label": {
                "type": "LABEL",
                "format": AmazonShippingAdapter._label_format(label, file_contents),
                "contents": contents,
                "width": dimensions.get("Width"),
                "length": dimensions.get("Length"),
                "unit": dimensions.get("Unit"),
            },
            "promise": {
                "ship_date": shipping_service.get("ShipDate"),
                "earliest_delivery": shipping_service.get("EarliestEstimatedDeliveryDate"),
                "latest_delivery": shipping_service.get("LatestEstimatedDeliveryDate"),
            },
            "benefits": shipping_service.get("Benefits"),
            "total_charge": shipping_service.get("RateWithAdjustments") or shipping_service.get("Rate"),
            "raw": payload,
        }
