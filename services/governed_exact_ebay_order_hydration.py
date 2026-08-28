"""Exact eBay order hydration for the governed webhook path.

The durable webhook already identifies and creates the MarketplaceOrder. This
module reads only that exact eBay order and its exact shipping fulfillments,
then fills missing delivery/timestamp/tracking fields on those existing rows.
It does not create orders, mutate Warehouse stock, push marketplaces, or submit
MCF.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests
from sqlalchemy import text

from extensions import db
from models import MarketplaceOrder
from services.governed_marketplace_order_import import (
    EBAY_ORDERS_URL,
    _ebay_access_token,
    _parse_ebay_datetime,
    _text,
)


def _fulfillment_truth(
    *,
    access_token: str,
    order_id: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read eBay's exact shipment fulfillments without making a marketplace write."""
    response = requests.get(
        f"{EBAY_ORDERS_URL}/{quote(order_id, safe='')}/shipping_fulfillment",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if response.status_code == 404:
        return [], None
    if response.status_code >= 400:
        return [], f"shipping_fulfillment_read_failed:{response.status_code}:{response.text[:500]}"

    payload = response.json() or {}
    fulfillments = payload.get("fulfillments") or []
    return [row for row in fulfillments if isinstance(row, dict)], None


def _fulfillment_line_ids(fulfillment: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for item in fulfillment.get("lineItems") or []:
        if not isinstance(item, dict):
            continue
        line_id = _text(item.get("lineItemId") or item.get("orderLineItemId"))
        if line_id:
            result.add(line_id)
    return result


def _fulfillment_values(fulfillment: dict[str, Any]) -> dict[str, Any]:
    return {
        "carrier": _text(
            fulfillment.get("shippingCarrierCode")
            or fulfillment.get("carrier")
            or fulfillment.get("carrierCode")
        ),
        "tracking_number": _text(
            fulfillment.get("trackingNumber")
            or fulfillment.get("shipmentTrackingNumber")
        ),
        "shipped_at": _parse_ebay_datetime(
            fulfillment.get("shippedDate")
            or fulfillment.get("shipDate")
        ),
    }


def _best_fulfillment_for_row(
    *,
    row: MarketplaceOrder,
    fulfillments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select only unambiguous shipment truth for one order line."""
    if not fulfillments:
        return None

    line_id = _text(row.marketplace_order_item_id)
    line_matches = [
        fulfillment
        for fulfillment in fulfillments
        if line_id and line_id in _fulfillment_line_ids(fulfillment)
    ]
    candidates = line_matches

    # A single order-level fulfillment is safe for every line when eBay did not
    # return line-item linkage. Never collapse multiple shipment tracking IDs
    # into one MarketplaceOrder field.
    if not candidates and len(fulfillments) == 1:
        candidates = fulfillments
    if len(candidates) != 1:
        return None
    return candidates[0]


def hydrate_exact_ebay_order(*, store, marketplace_order_id: str, source: str) -> dict[str, Any]:
    order_id = _text(marketplace_order_id)
    if not order_id:
        return {"success": False, "skipped": True, "reason": "ebay_order_id_missing"}

    rows = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == store.id,
            MarketplaceOrder.marketplace_order_id == order_id,
        )
        .order_by(MarketplaceOrder.id)
        .all()
    )
    if not rows:
        return {
            "success": False,
            "skipped": True,
            "reason": "existing_marketplace_order_missing",
            "order_id": order_id,
        }

    access_token = _ebay_access_token(store)
    response = requests.get(
        f"{EBAY_ORDERS_URL}/{quote(order_id, safe='')}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        return {
            "success": False,
            "skipped": False,
            "reason": "exact_ebay_order_read_failed",
            "status_code": response.status_code,
            "error": response.text[:1000],
            "order_id": order_id,
        }

    order = response.json() or {}
    returned_id = _text(order.get("orderId"))
    if returned_id and returned_id != order_id:
        return {
            "success": False,
            "skipped": False,
            "reason": "exact_ebay_order_identity_mismatch",
            "order_id": order_id,
            "returned_order_id": returned_id,
        }

    fulfillments, fulfillment_error = _fulfillment_truth(
        access_token=access_token,
        order_id=order_id,
    )

    instructions = order.get("fulfillmentStartInstructions") or []
    instruction = instructions[0] if instructions else {}
    shipping_step = instruction.get("shippingStep") or {}
    ship_to = shipping_step.get("shipTo") or {}
    address = ship_to.get("contactAddress") or {}

    delivery_address = ", ".join(
        part for part in (
            _text(address.get("addressLine1")),
            _text(address.get("addressLine2")),
        ) if part
    )
    values = {
        "ship_to_name": _text(ship_to.get("fullName")),
        "ship_to_address": delivery_address,
        "ship_to_city": _text(address.get("city")),
        "ship_to_postcode": _text(address.get("postalCode")),
        "ship_to_country": _text(address.get("countryCode")).upper()[:2],
        "ship_to_email": _text(ship_to.get("email")),
        "ship_to_phone": _text((ship_to.get("primaryPhone") or {}).get("phoneNumber"))
        or _text(ship_to.get("phoneNumber")),
    }
    marketplace_created_at = _parse_ebay_datetime(order.get("creationDate"))

    item_by_line_id = {
        _text(item.get("lineItemId")): item
        for item in (order.get("lineItems") or [])
        if _text(item.get("lineItemId"))
    }
    item_by_sku = {
        _text(item.get("sku")): item
        for item in (order.get("lineItems") or [])
        if _text(item.get("sku"))
    }

    identity_updates = 0
    identity_conflicts = []
    tracking_updates = 0

    for row in rows:
        for field, value in values.items():
            if value:
                setattr(row, field, value)

        item = (
            item_by_line_id.get(_text(row.marketplace_order_item_id))
            or item_by_sku.get(_text(row.sku))
        )
        if item:
            line_id = _text(item.get("lineItemId"))
            if line_id:
                canonical_key = f"{store.id}:{order_id}:{line_id}:{_text(row.sku)}"
                conflict = (
                    MarketplaceOrder.query
                    .filter(
                        MarketplaceOrder.idempotency_key == canonical_key,
                        MarketplaceOrder.id != row.id,
                    )
                    .first()
                )
                if conflict is None:
                    if (
                        _text(row.marketplace_order_item_id) != line_id
                        or _text(row.idempotency_key) != canonical_key
                    ):
                        row.marketplace_order_item_id = line_id
                        row.idempotency_key = canonical_key
                        identity_updates += 1
                else:
                    identity_conflicts.append({
                        "row_id": row.id,
                        "conflicting_row_id": conflict.id,
                        "line_item_id": line_id,
                    })

        fulfillment = _best_fulfillment_for_row(row=row, fulfillments=fulfillments)
        if fulfillment is not None:
            shipment = _fulfillment_values(fulfillment)
            changed = False
            if shipment["carrier"] and not _text(row.carrier):
                row.carrier = shipment["carrier"]
                changed = True
            if shipment["tracking_number"] and not _text(row.tracking_number):
                row.tracking_number = shipment["tracking_number"]
                changed = True
            if shipment["shipped_at"] is not None and row.shipped_at is None:
                row.shipped_at = shipment["shipped_at"]
                changed = True
            if changed:
                tracking_updates += 1

        db.session.execute(
            text(
                """
                UPDATE marketplace_orders
                SET marketplace_created_at = COALESCE(:created_at, marketplace_created_at),
                    import_source = :source
                WHERE id = :row_id
                """
            ),
            {
                "created_at": marketplace_created_at,
                "source": source,
                "row_id": row.id,
            },
        )

    db.session.commit()

    required_address_complete = bool(
        values["ship_to_name"]
        and values["ship_to_address"]
        and values["ship_to_city"]
        and values["ship_to_postcode"]
        and values["ship_to_country"]
    )
    return {
        "success": required_address_complete and not identity_conflicts,
        "skipped": False,
        "reason": (
            "exact_ebay_order_identity_conflict"
            if identity_conflicts
            else (None if required_address_complete else "exact_ebay_order_missing_delivery_fields")
        ),
        "order_id": order_id,
        "marketplace_created_at": marketplace_created_at.isoformat() if marketplace_created_at else None,
        "required_address_complete": required_address_complete,
        "rows_hydrated": len(rows),
        "identity_updates": identity_updates,
        "identity_conflicts": identity_conflicts,
        "fulfillments_seen": len(fulfillments),
        "tracking_updates": tracking_updates,
        "fulfillment_error": fulfillment_error,
        "marketplace_write_started": False,
    }
