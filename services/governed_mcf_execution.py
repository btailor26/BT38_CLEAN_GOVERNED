"""Governed Amazon MCF execution using the MCF order's selected FBA store."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import json
import os

from sp_api.api import FulfillmentOutbound
from sp_api.base import Marketplaces

from extensions import db
from models import MCFOrder, MarketplaceOrder

UK_MARKETPLACE_ID = "A1F83G8C2ARO7P"


def _credentials_for_store(store) -> dict[str, str]:
    """
    Use the same Store.api_key and environment credential path as the existing
    governed Amazon importer.
    """
    raw = store.api_key or {}

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}

    credentials = {
        "refresh_token": (
            raw.get("refresh_token")
            or os.getenv("AMAZON_REFRESH_TOKEN")
            or os.getenv("SP_API_REFRESH_TOKEN")
        ),
        "lwa_app_id": (
            raw.get("lwa_app_id")
            or raw.get("lwa_client_id")
            or raw.get("client_id")
            or os.getenv("AMAZON_LWA_CLIENT_ID")
            or os.getenv("AMAZON_LWA_APP_ID")
            or os.getenv("SP_API_LWA_CLIENT_ID")
        ),
        "lwa_client_secret": (
            raw.get("lwa_client_secret")
            or raw.get("client_secret")
            or os.getenv("AMAZON_LWA_CLIENT_SECRET")
            or os.getenv("SP_API_LWA_CLIENT_SECRET")
        ),
    }

    aws_access_key = (
        raw.get("aws_access_key")
        or raw.get("aws_access_key_id")
        or os.getenv("AMAZON_AWS_ACCESS_KEY_ID")
        or os.getenv("SP_API_AWS_ACCESS_KEY_ID")
    )

    aws_secret_key = (
        raw.get("aws_secret_key")
        or raw.get("aws_secret_access_key")
        or os.getenv("AMAZON_AWS_SECRET_ACCESS_KEY")
        or os.getenv("SP_API_AWS_SECRET_ACCESS_KEY")
    )

    role_arn = (
        raw.get("role_arn")
        or raw.get("aws_user_arn")
        or os.getenv("AMAZON_AWS_ROLE_ARN")
        or os.getenv("SP_API_ROLE_ARN")
    )

    if aws_access_key:
        credentials["aws_access_key"] = aws_access_key

    if aws_secret_key:
        credentials["aws_secret_key"] = aws_secret_key

    if role_arn:
        credentials["role_arn"] = role_arn

    missing = [
        key
        for key in (
            "refresh_token",
            "lwa_app_id",
            "lwa_client_secret",
        )
        if not credentials.get(key)
    ]

    if missing:
        raise RuntimeError(
            "mcf_fba_store_credentials_missing:"
            + ",".join(missing)
        )

    return credentials


def _client(mcf_order: MCFOrder) -> FulfillmentOutbound:
    store = mcf_order.fba_store

    if store is None or not store.is_active:
        raise RuntimeError(
            "mcf_fba_store_missing_or_inactive"
        )

    return FulfillmentOutbound(
        marketplace=Marketplaces.UK,
        credentials=_credentials_for_store(store),
    )


def _bind_source_lines(mcf_order: MCFOrder) -> list[MarketplaceOrder]:
    lines = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == mcf_order.source_store_id,
            MarketplaceOrder.marketplace_order_id == mcf_order.source_order_id,
        )
        .all()
    )
    for line in lines:
        if line.mcf_order_id and line.mcf_order_id != mcf_order.id:
            raise RuntimeError(f"source_order_already_bound_to_mcf:{line.mcf_order_id}")
        line.mcf_order_id = mcf_order.id
        line.fulfillment_type = "FBA"
        line.status = "mcf_pending_submission"
        line.processed_at = datetime.utcnow()
    db.session.commit()
    return lines


def _shipping_speed_category(value: str | None) -> str:
    """
    Amazon expects the documented enum casing, not an upper-case variant.
    """
    normalized = str(value or "Standard").strip().lower()

    return {
        "standard": "Standard",
        "expedited": "Expedited",
        "priority": "Priority",
    }.get(normalized, "Standard")


def _amazon_visibility_pending(
    mcf_order: MCFOrder,
    error: Exception,
) -> bool:
    """
    CreateFulfillmentOrder may be accepted before GetFulfillmentOrder can see
    the merchant order ID. Treat that short propagation period as pending.
    """
    message = str(error)

    visibility_error = (
        "InvalidInput" in message
        and "Unable to get order info" in message
    )

    if not visibility_error:
        return False

    status = str(mcf_order.status or "").lower()
    amazon_status = str(
        mcf_order.amazon_status or ""
    ).upper()

    if status not in {"submitted", "processing"}:
        return False

    if amazon_status != "RECEIVED":
        return False

    submitted_at = (
        mcf_order.amazon_status_updated_at
        or mcf_order.updated_at
        or mcf_order.created_at
    )

    if submitted_at is None:
        return False

    return (
        datetime.utcnow() - submitted_at
        <= timedelta(minutes=30)
    )


def submit_mcf_order(mcf_order: MCFOrder) -> tuple[bool, str]:
    try:
        _bind_source_lines(mcf_order)
        items_payload = [
            {
                "sellerSku": item.fba_sku,
                "sellerFulfillmentOrderItemId": f"{mcf_order.seller_fulfillment_order_id}-{item.id}",
                "quantity": item.quantity,
                "perUnitDeclaredValue": {
                    "currencyCode": "GBP",
                    "value": str(item.unit_price or 0),
                },
            }
            for item in mcf_order.items.all()
        ]
        payload = {
            "sellerFulfillmentOrderId": mcf_order.seller_fulfillment_order_id,
            "displayableOrderId": mcf_order.displayable_order_id,
            "displayableOrderDate": mcf_order.created_at.isoformat() + "Z",
            "displayableOrderComment": mcf_order.displayable_comment or "",
            "shippingSpeedCategory": _shipping_speed_category(
                mcf_order.shipping_speed
            ),
            "destinationAddress": {
                "name": mcf_order.destination_name or "Customer",
                "addressLine1": mcf_order.destination_address_line1 or "",
                "addressLine2": mcf_order.destination_address_line2 or "",
                "city": mcf_order.destination_city or "",
                "stateOrRegion": mcf_order.destination_state or "",
                "postalCode": mcf_order.destination_postcode or "",
                "countryCode": mcf_order.destination_country or "GB",
                "phone": mcf_order.destination_phone or "",
            },
            "items": items_payload,
        }

        source_email = next(
            (
                str(line.ship_to_email or "").strip()
                for line in mcf_order.marketplace_orders.all()
                if str(line.ship_to_email or "").strip()
            ),
            "",
        )

        if source_email:
            payload["notificationEmails"] = [
                source_email
            ]

        try:
            response = _client(
                mcf_order
            ).create_fulfillment_order(**payload)

            response_payload = (
                getattr(response, "payload", None)
                or {}
            )
        except Exception as exc:
            error = str(exc)
            mcf_order.status = "failed"
            mcf_order.last_error = error
            mcf_order.retry_count = (
                mcf_order.retry_count or 0
            ) + 1

            for line in mcf_order.marketplace_orders.all():
                line.status = "mcf_submission_failed"
                line.error_message = error

            db.session.commit()
            return False, (
                f"Failed to submit to Amazon: {error}"
            )

        mcf_order.status = "submitted"
        mcf_order.amazon_status = "RECEIVED"
        mcf_order.amazon_status_updated_at = datetime.utcnow()
        mcf_order.last_error = None
        for line in mcf_order.marketplace_orders.all():
            line.status = "mcf_accepted"
            line.error_message = None
        db.session.commit()
        return True, "MCF order submitted to Amazon successfully"
    except Exception as exc:
        mcf_order.status = "failed"
        mcf_order.last_error = str(exc)
        mcf_order.retry_count = (mcf_order.retry_count or 0) + 1
        db.session.commit()
        return False, f"Error submitting MCF order: {exc}"


def refresh_mcf_status(mcf_order: MCFOrder) -> tuple[bool, dict[str, Any]]:
    try:
        try:
            response = _client(
                mcf_order
            ).get_fulfillment_order(
                sellerFulfillmentOrderId=(
                    mcf_order.seller_fulfillment_order_id
                )
            )

            payload = (
                getattr(response, "payload", None)
                or {}
            )
        except Exception as exc:
            if _amazon_visibility_pending(
                mcf_order,
                exc,
            ):
                return True, {
                    "status": (
                        mcf_order.amazon_status
                        or "RECEIVED"
                    ),
                    "carrier": mcf_order.carrier,
                    "tracking_number": (
                        mcf_order.tracking_number
                    ),
                    "ship_date": (
                        mcf_order.ship_date.isoformat()
                        if mcf_order.ship_date
                        else None
                    ),
                    "pending_visibility": True,
                    "message": (
                        "Amazon accepted the MCF order "
                        "but it is not visible in status "
                        "lookup yet."
                    ),
                }

            return False, {"error": str(exc)}

        fulfillment_order = (
            payload.get("fulfillmentOrder")
            or {}
        )
        mcf_order.amazon_status = fulfillment_order.get("fulfillmentOrderStatus") or mcf_order.amazon_status
        mcf_order.amazon_status_updated_at = datetime.utcnow()

        status = str(mcf_order.amazon_status or "").upper()
        if status in {"COMPLETE", "COMPLETE_PARTIALLED"}:
            mcf_order.status = "completed"
        elif status in {"CANCELLED", "INVALID"}:
            mcf_order.status = "cancelled"
        elif status in {"PLANNING", "PROCESSING", "RECEIVED"}:
            mcf_order.status = "processing"

        shipments = payload.get("fulfillmentShipments") or []
        first_shipment = shipments[0] if shipments else {}
        packages = first_shipment.get("fulfillmentShipmentPackage") or first_shipment.get("fulfillmentShipmentPackages") or []
        first_package = packages[0] if packages else first_shipment

        mcf_order.carrier = first_package.get("carrierCode") or first_shipment.get("carrierCode") or mcf_order.carrier
        mcf_order.tracking_number = first_package.get("trackingNumber") or first_shipment.get("trackingNumber") or mcf_order.tracking_number

        ship_date = first_shipment.get("shippingDate") or first_shipment.get("shipDate")
        if ship_date:
            try:
                mcf_order.ship_date = datetime.fromisoformat(str(ship_date).replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass

        estimated = first_shipment.get("estimatedArrivalDate")
        if estimated:
            try:
                mcf_order.estimated_arrival_date = datetime.fromisoformat(str(estimated).replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                pass

        db.session.commit()
        return True, {
            "status": mcf_order.amazon_status,
            "carrier": mcf_order.carrier,
            "tracking_number": mcf_order.tracking_number,
            "ship_date": mcf_order.ship_date.isoformat() if mcf_order.ship_date else None,
        }
    except Exception as exc:
        return False, {"error": str(exc)}
