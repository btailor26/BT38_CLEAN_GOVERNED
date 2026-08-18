"""Amazon Buy Shipping adapter for BT38 FBM.

Amazon owns on-Amazon Buy Shipping eligibility, carrier/service selection,
customer delivery details and shipment confirmation. BT38 supplies the Amazon
order identity, seller ship-from address and packed parcel facts required by the
Merchant Fulfillment Buy Shipping operations.

Prime/SFP orders remain locked to this provider by the FBM routing layer. This
adapter deliberately does not require BT38's local copy of the buyer address.
"""
from __future__ import annotations

import base64
import gzip
import inspect
import secrets
from dataclasses import dataclass
from datetime import timezone
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
            raise AmazonShippingError(
                "Installed amazon-sp-api library does not expose Merchant Fulfillment Buy Shipping."
            ) from exc

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
        required = {
            "address1": "dispatch address",
            "city": "dispatch city",
            "postcode": "dispatch postcode",
            "country": "dispatch country",
        }
        missing = [label for key, label in required.items() if not str(address.get(key) or "").strip()]
        if missing:
            raise AmazonShippingError("BT38 ship-from details are incomplete: " + ", ".join(missing))

        payload = {
            "Name": address.get("name") or address.get("company") or "B & T Outlet",
            "AddressLine1": str(address["address1"]).strip(),
            "City": str(address["city"]).strip(),
            "PostalCode": str(address["postcode"]).strip(),
            "CountryCode": str(address["country"]).strip().upper(),
        }
        if address.get("email"):
            payload["Email"] = str(address["email"]).strip()
        if address.get("phone"):
            payload["Phone"] = str(address["phone"]).strip()
        return payload

    @staticmethod
    def _amazon_timestamp(value: Any) -> str | None:
        if value is None:
            return None
        try:
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            else:
                value = value.astimezone(timezone.utc)
            return value.isoformat(timespec="seconds").replace("+00:00", "Z")
        except Exception:
            return None

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

        details: dict[str, Any] = {
            "AmazonOrderId": str(order.marketplace_order_id),
            "ItemList": [
                {
                    "OrderItemId": str(line.marketplace_order_item_id),
                    "Quantity": max(1, int(getattr(line, "quantity", 1) or 1)),
                }
                for line in lines
            ],
            "ShipFromAddress": AmazonShippingAdapter._ship_from_payload(ship_from()),
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

        try:
            from fbm_models import FBMOrderProfile
            profile = FBMOrderProfile.query.filter_by(
                store_id=order.store_id,
                marketplace_order_id=order.marketplace_order_id,
            ).first()
            latest_ship = getattr(profile, "latest_ship_at", None) if profile is not None else None
            ship_date = AmazonShippingAdapter._amazon_timestamp(latest_ship)
            if ship_date:
                details["ShipDate"] = ship_date
        except Exception:
            pass
        return details

    @staticmethod
    def _assert_method_contract(method: Any, required_names: set[str], operation: str) -> None:
        """Fail before any charge if the installed wrapper signature is incompatible."""
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError) as exc:
            raise AmazonShippingError(f"Could not verify installed amazon-sp-api {operation} signature.") from exc
        params = signature.parameters
        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        missing = sorted(name for name in required_names if name not in params and not has_kwargs)
        if missing:
            raise AmazonShippingError(
                f"Installed amazon-sp-api {operation} signature is incompatible; missing: {', '.join(missing)}."
            )

    def get_rates(self, *, order: Any, parcel: dict[str, Any]) -> AmazonRateResult:
        details = self._shipment_request_details(order, parcel)
        client = self._client()
        method = getattr(client, "get_eligible_shipment_services", None)
        if method is None:
            raise AmazonShippingError("Installed amazon-sp-api does not expose get_eligible_shipment_services.")
        self._assert_method_contract(method, {"shipment_request_details"}, "rate")
        try:
            response = method(shipment_request_details=details)
        except TypeError:
            # Older compatible wrapper builds accept the request body positionally.
            try:
                response = method(details)
            except Exception as exc:
                raise AmazonShippingError(str(exc)) from exc
        except Exception as exc:
            raise AmazonShippingError(str(exc)) from exc

        payload = self._response_payload(response)
        normalised = self._normalise_rates(payload.get("ShippingServiceList") or [])
        unavailable: list[dict] = []
        purchasable: list[dict] = []
        for rate in normalised:
            if rate.get("requires_additional_inputs"):
                unavailable.append({
                    "reason": "additional_seller_inputs_required",
                    "carrier": rate.get("carrier_name"),
                    "service": rate.get("service_name"),
                    "service_id": rate.get("service_id"),
                    "message": "Amazon requires additional seller inputs for this service; BT38 is holding this offer until those inputs are supported.",
                })
            else:
                purchasable.append(rate)
        for key, reason in (
            ("RejectedShippingServiceList", "rejected"),
            ("TemporarilyUnavailableCarrierList", "temporarily_unavailable"),
            ("TermsAndConditionsNotAcceptedCarrierList", "terms_not_accepted"),
        ):
            for item in payload.get(key) or []:
                if isinstance(item, dict):
                    unavailable.append({"reason": reason, **item})

        return AmazonRateResult(
            request_token="mfn_" + secrets.token_urlsafe(24),
            rates=purchasable,
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
        if not request_token:
            raise AmazonShippingError("Amazon Buy Shipping quote token is required.")
        if not rate_id:
            raise AmazonShippingError("Amazon Buy Shipping rate ID is required.")

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
        if selected.get("requires_additional_inputs"):
            raise AmazonShippingError("This Amazon shipping service requires additional seller inputs and is not purchasable through the current BT38 UI.")

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

        label_format = str(
            (requested_document_specification or {}).get("format")
            or (requested_document_specification or {}).get("LabelFormat")
            or ""
        ).strip()
        if label_format and label_format != "ShippingServiceDefault":
            details["ShippingServiceOptions"]["LabelFormat"] = label_format

        kwargs: dict[str, Any] = {}
        if offer_id:
            kwargs["ShippingServiceOfferId"] = offer_id

        client = self._client()
        method = getattr(client, "create_shipment", None)
        if method is None:
            raise AmazonShippingError("Installed amazon-sp-api does not expose create_shipment.")
        self._assert_method_contract(
            method,
            {"shipment_request_details", "shipping_service_id"},
            "purchase",
        )
        try:
            response = method(
                shipment_request_details=details,
                shipping_service_id=service_id,
                **kwargs,
            )
        except Exception as exc:
            raise AmazonShippingError(str(exc)) from exc

        result = self._normalise_purchase(self._response_payload(response))
        # Preserve Amazon-returned physical label dimensions in the existing
        # route contract without changing the route or DB schema.
        label = result.get("label") or {}
        width, length, unit = label.get("width"), label.get("length"), label.get("unit")
        if width is not None and length is not None:
            requested_document_specification["size"] = {
                "width": width,
                "length": length,
                "unit": unit,
            }
        if label.get("format"):
            requested_document_specification["format"] = label["format"]
        return result

    def get_tracking(self, *, tracking_id: str, carrier_id: str | None = None) -> dict[str, Any]:
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

        client = self._client()
        method = getattr(client, "get_shipment", None)
        if method is None:
            raise AmazonShippingError("Installed amazon-sp-api does not expose get_shipment.")
        try:
            response = method(shipment_id)
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
        return {"value": amount, "unit": rate.get("CurrencyCode") or rate.get("Currency") or "GBP"}

    @staticmethod
    def _normalise_rates(rates: list[Any]) -> list[dict]:
        result: list[dict] = []
        for rate in rates:
            if not isinstance(rate, dict):
                continue
            service_id = str(rate.get("ShippingServiceId") or "").strip() or None
            offer_id = str(rate.get("ShippingServiceOfferId") or "").strip() or None
            formats = rate.get("AvailableLabelFormats") or ["ShippingServiceDefault"]
            documents = [{"format": str(fmt), "LabelFormat": str(fmt)} for fmt in formats if fmt]
            adjusted_rate = rate.get("RateWithAdjustments") or rate.get("Rate") or {}
            carrier_name = str(rate.get("CarrierName") or "").strip() or "Amazon Buy Shipping"
            result.append({
                "rate_id": offer_id or service_id,
                "offer_id": offer_id,
                # Existing route only uses provider_carrier_id as a non-empty
                # marker before delegating tracking back to this adapter.
                "carrier_id": carrier_name or "AMAZON_BUY_SHIPPING",
                "carrier_name": carrier_name,
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
