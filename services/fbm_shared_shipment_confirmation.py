"""Confirm one physical FBM shipment against every explicitly linked order.

The physical shipment remains the single tracking/label authority. Marketplace
orders stay separate and each linked order receives its own confirmation result.
This module is invoked only after a paid/confirmed external label already exists;
it never buys postage and never performs broad marketplace reads.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from fbm_parcel_models import FBMShipmentOrderLink
from models import MarketplaceOrder
from services.fbm_order_mapper import order_lines


def _platform(order: MarketplaceOrder) -> str:
    return str(getattr(getattr(order, "store", None), "platform", "") or "").strip().lower()


def _order_for_link(link: FBMShipmentOrderLink) -> MarketplaceOrder | None:
    return (
        MarketplaceOrder.query
        .filter_by(store_id=link.store_id, marketplace_order_id=link.marketplace_order_id)
        .order_by(MarketplaceOrder.id.asc())
        .first()
    )


def _persist_lines(order: MarketplaceOrder, shipment, *, carrier: str, tracking: str, now: datetime) -> None:
    for line in order_lines(order):
        line.carrier = carrier
        line.tracking_number = tracking
        line.shipped_at = line.shipped_at or now
        line.updated_at = now


def confirm_linked_external_orders(*, shipment, mapping) -> dict[str, Any]:
    """Confirm secondary linked orders using the same physical tracking authority."""
    links = (
        FBMShipmentOrderLink.query
        .filter_by(shipment_id=shipment.id)
        .order_by(FBMShipmentOrderLink.is_primary.desc(), FBMShipmentOrderLink.id.asc())
        .all()
    )
    secondary = [link for link in links if not link.is_primary]
    if not secondary:
        return {"attempted": 0, "confirmed": 0, "failed": 0, "results": []}

    tracking = str(shipment.tracking_number or "").strip()
    if not tracking:
        return {"attempted": 0, "confirmed": 0, "failed": 0, "results": [], "held": "tracking_required"}

    primary_order = (
        MarketplaceOrder.query
        .filter_by(store_id=shipment.store_id, marketplace_order_id=shipment.marketplace_order_id)
        .order_by(MarketplaceOrder.id.asc())
        .first()
    )
    primary_platform = _platform(primary_order) if primary_order is not None else ""
    results: list[dict[str, Any]] = []
    confirmed = 0
    failed = 0

    for link in secondary:
        if link.marketplace_confirmed_at:
            confirmed += 1
            results.append({"order_id": link.marketplace_order_id, "success": True, "already_confirmed": True})
            continue

        order = _order_for_link(link)
        if order is None:
            link.marketplace_confirmation_status = "order_missing"
            link.marketplace_confirmation_error = "Linked marketplace order is missing from BT38."
            failed += 1
            results.append({"order_id": link.marketplace_order_id, "success": False, "reason": "order_missing"})
            continue

        platform = _platform(order)
        if platform != primary_platform:
            link.marketplace_confirmation_status = "mixed_marketplace_blocked"
            link.marketplace_confirmation_error = "One physical parcel can only share the existing carrier mapping within the same marketplace."
            failed += 1
            results.append({"order_id": link.marketplace_order_id, "success": False, "reason": "mixed_marketplace_blocked"})
            continue

        try:
            now = datetime.utcnow()
            if platform == "ebay":
                from services.fbm_marketplace_confirmation import _confirm_ebay_external
                _confirm_ebay_external(order=order, shipment=shipment, mapping=mapping, tracking=tracking)
                carrier = str(mapping.marketplace_carrier_name or mapping.marketplace_carrier_code or shipment.carrier or "").strip()
                _persist_lines(order, shipment, carrier=carrier, tracking=tracking, now=now)
            elif platform == "amazon":
                from services.fbm_marketplace_confirmation import (
                    _amazon_client,
                    _amazon_order_items,
                    _amazon_package_reference_id,
                    _amazon_ship_date,
                )
                client, marketplace_id = _amazon_client(order)
                carrier_code = str(mapping.marketplace_carrier_code or "").strip()
                carrier_name = str(mapping.marketplace_carrier_name or shipment.carrier or carrier_code).strip()
                shipping_method = str(mapping.marketplace_service_name or mapping.marketplace_service_code or shipment.service or carrier_name).strip()
                client.confirm_shipment(
                    str(order.marketplace_order_id),
                    marketplaceId=marketplace_id,
                    packageDetail={
                        "packageReferenceId": _amazon_package_reference_id(shipment),
                        "carrierCode": carrier_code,
                        "carrierName": carrier_name,
                        "shippingMethod": shipping_method,
                        "trackingNumber": tracking,
                        "shipDate": _amazon_ship_date(shipment),
                        "orderItems": _amazon_order_items(order),
                    },
                )
                _persist_lines(order, shipment, carrier=carrier_name, tracking=tracking, now=now)
            else:
                raise RuntimeError(f"Shared shipment confirmation is not implemented for {platform or 'this marketplace'}." )

            link.marketplace_confirmed_at = now
            link.marketplace_confirmation_status = "confirmed"
            link.marketplace_confirmation_error = None
            confirmed += 1
            results.append({"order_id": link.marketplace_order_id, "success": True, "confirmed_at": now.isoformat()})
        except Exception as exc:
            link.marketplace_confirmation_status = "confirmation_failed"
            link.marketplace_confirmation_error = str(exc)
            failed += 1
            results.append({"order_id": link.marketplace_order_id, "success": False, "reason": "confirmation_failed", "error": str(exc)})

    db.session.commit()
    return {"attempted": len(secondary), "confirmed": confirmed, "failed": failed, "results": results}
