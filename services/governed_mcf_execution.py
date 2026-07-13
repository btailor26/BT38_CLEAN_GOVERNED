"""Governed Amazon MCF execution using the MCF order's selected FBA store."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from amazon_rest_api import AmazonRestAPIClient
from models import MCFOrder, MarketplaceOrder

UK_MARKETPLACE_ID = "A1F83G8C2ARO7P"


def _client(mcf_order: MCFOrder) -> AmazonRestAPIClient:
    store = mcf_order.fba_store
    if store is None or not store.is_active:
        raise RuntimeError("mcf_fba_store_missing_or_inactive")
    credentials = store.get_amazon_credentials()
    if not credentials:
        raise RuntimeError("mcf_fba_store_credentials_missing")
    return AmazonRestAPIClient(credentials, UK_MARKETPLACE_ID)


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
            "shippingSpeedCategory": str(mcf_order.shipping_speed or "Standard").upper(),
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
        success, _data, error = _client(mcf_order)._make_request(
            "POST",
            "/fba/outbound/2020-07-01/fulfillmentOrders",
            json_data=payload,
        )
        if not success:
            mcf_order.status = "failed"
            mcf_order.last_error = error
            mcf_order.retry_count = (mcf_order.retry_count or 0) + 1
            for line in mcf_order.marketplace_orders.all():
                line.status = "mcf_submission_failed"
                line.error_message = error
            db.session.commit()
            return False, f"Failed to submit to Amazon: {error}"

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
        success, data, error = _client(mcf_order)._make_request(
            "GET",
            f"/fba/outbound/2020-07-01/fulfillmentOrders/{mcf_order.seller_fulfillment_order_id}",
        )
        if not success:
            return False, {"error": error}

        payload = data or {}
        fulfillment_order = payload.get("fulfillmentOrder") or {}
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
