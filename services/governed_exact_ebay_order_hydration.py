"""Exact eBay order hydration for the governed webhook/import path.

The durable marketplace path already identifies and creates MarketplaceOrder.
This module reads only that exact eBay order and its exact shipping fulfillments,
then updates marketplace-owned delivery/lifecycle/tracking fields on those same
rows. Exact eBay line-item fulfillment instructions are also persisted into the
existing FBM profile/operational-state promise fields when they are unambiguous.
It does not create orders, mutate Warehouse stock, push marketplaces, or submit MCF.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests
from sqlalchemy import text

from extensions import db
from fbm_models import FBMOrderProfile
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


def _ebay_cancel_state(order_payload: dict[str, Any]) -> str:
    cancel_status = order_payload.get("cancelStatus") or {}
    if not isinstance(cancel_status, dict):
        return ""
    return _text(cancel_status.get("cancelState")).upper()


def _ebay_lifecycle_status(order_payload: dict[str, Any]) -> str | None:
    # eBay's exact getOrder cancellation container is marketplace lifecycle truth.
    # Completed cancellation must outrank payment/fulfillment fields because
    # cancelled orders can still be returned by the Fulfillment API.
    cancel_state = _ebay_cancel_state(order_payload)
    if cancel_state == "CANCELED":
        return "cancelled"
    if cancel_state == "IN_PROGRESS":
        return "cancel_requested"

    payment = _text(order_payload.get("orderPaymentStatus")).upper()
    fulfillment = _text(order_payload.get("orderFulfillmentStatus")).upper()
    if payment and payment != "PAID":
        return "pending"
    if payment == "PAID" and fulfillment == "FULFILLED":
        return "shipped"
    if payment == "PAID":
        return "unshipped"
    return None


def _can_apply_marketplace_status(current: str, incoming: str) -> bool:
    current_value = _text(current).lower()
    incoming_value = _text(incoming).lower()

    protected_issue_states = {
        "return_requested", "returned", "refund_requested", "refunded",
        "replacement_requested", "replacement", "case_open", "dispute",
        "chargeback", "delivered",
    }
    if current_value in protected_issue_states:
        return False

    if incoming_value in {"cancel_requested", "cancelled"}:
        # Exact eBay cancellation truth may replace stale routine queue states,
        # but does not erase a stronger shipped/carrier journey already persisted.
        return current_value in {
            "", "pending", "order", "confirmed", "unshipped",
            "cancel_requested", "cancelled",
        }

    if current_value in {
        "cancel_requested", "cancelled", "picked_up", "accepted",
        "carrier_accepted", "collected", "in_transit", "out_for_delivery",
    }:
        return False

    rank = {
        "pending": 0,
        "order": 1,
        "confirmed": 1,
        "unshipped": 1,
        "partially_shipped": 2,
        "shipped": 3,
    }
    return rank.get(incoming_value, -1) >= rank.get(current_value, -1)


def _single_exact_value(values: list[Any]):
    """Return one marketplace fact only when the exact order is unambiguous."""
    present = [value for value in values if value not in (None, "")]
    if not present:
        return None
    first = present[0]
    return first if all(value == first for value in present) else None


def _exact_ebay_promise(order_payload: dict[str, Any]) -> dict[str, Any]:
    """Read eBay's own handling/delivery windows from the exact getOrder payload.

    eBay exposes shipByDate, minEstimatedDeliveryDate and maxEstimatedDeliveryDate
    under each lineItemFulfillmentInstructions container. The existing FBM schema
    is order-level, so conflicting line-level values are deliberately not collapsed
    or guessed. Single-line and identical multi-line values are safe to persist.
    """
    instructions = []
    for item in order_payload.get("lineItems") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("lineItemFulfillmentInstructions") or {}
        if isinstance(value, dict):
            instructions.append(value)

    ship_by = _single_exact_value([
        _parse_ebay_datetime(value.get("shipByDate")) for value in instructions
    ])
    earliest = _single_exact_value([
        _parse_ebay_datetime(value.get("minEstimatedDeliveryDate")) for value in instructions
    ])
    latest = _single_exact_value([
        _parse_ebay_datetime(value.get("maxEstimatedDeliveryDate")) for value in instructions
    ])

    services = []
    for start in order_payload.get("fulfillmentStartInstructions") or []:
        if not isinstance(start, dict):
            continue
        shipping_step = start.get("shippingStep") or {}
        if isinstance(shipping_step, dict):
            service = _text(shipping_step.get("shippingServiceCode"))
            if service:
                services.append(service)
    service = _single_exact_value(services)

    return {
        "shipping_service": service,
        "ship_by_at": ship_by,
        "earliest_delivery_at": earliest,
        "latest_delivery_at": latest,
    }


def _persist_exact_ebay_promise(*, store, order_id: str, order_payload: dict[str, Any]) -> bool:
    """Persist only exact eBay promise facts already returned by this hydration read."""
    promise = _exact_ebay_promise(order_payload)
    if not any(value is not None for value in promise.values()):
        return False

    now = datetime.utcnow()
    profile = FBMOrderProfile.query.filter_by(
        store_id=store.id,
        marketplace_order_id=order_id,
    ).first()
    if profile is None:
        profile = FBMOrderProfile(
            store_id=store.id,
            marketplace_order_id=order_id,
            platform="ebay",
            source="exact_ebay_order_hydration",
        )
        db.session.add(profile)
    if promise["shipping_service"]:
        profile.shipment_service_level = promise["shipping_service"]
    if promise["ship_by_at"] is not None:
        profile.latest_ship_at = promise["ship_by_at"]
    profile.source = "exact_ebay_order_hydration"
    profile.checked_at = now
    profile.last_error = None

    db.session.execute(
        text(
            """
            INSERT INTO fbm_order_operational_state (
                store_id,
                marketplace_order_id,
                platform,
                shipping_service,
                ship_by_at,
                earliest_delivery_at,
                latest_delivery_at,
                parcel,
                marketplace_checked_at,
                created_at,
                updated_at
            ) VALUES (
                :store_id,
                :order_id,
                'ebay',
                :shipping_service,
                :ship_by_at,
                :earliest_delivery_at,
                :latest_delivery_at,
                CAST(:parcel AS json),
                :checked_at,
                :checked_at,
                :checked_at
            )
            ON CONFLICT (store_id, marketplace_order_id)
            DO UPDATE SET
                shipping_service = COALESCE(EXCLUDED.shipping_service, fbm_order_operational_state.shipping_service),
                ship_by_at = COALESCE(EXCLUDED.ship_by_at, fbm_order_operational_state.ship_by_at),
                earliest_delivery_at = COALESCE(EXCLUDED.earliest_delivery_at, fbm_order_operational_state.earliest_delivery_at),
                latest_delivery_at = COALESCE(EXCLUDED.latest_delivery_at, fbm_order_operational_state.latest_delivery_at),
                marketplace_checked_at = EXCLUDED.marketplace_checked_at,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "store_id": int(store.id),
            "order_id": str(order_id),
            "shipping_service": promise["shipping_service"],
            "ship_by_at": promise["ship_by_at"],
            "earliest_delivery_at": promise["earliest_delivery_at"],
            "latest_delivery_at": promise["latest_delivery_at"],
            "parcel": "{}",
            "checked_at": now,
        },
    )
    return True


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
    marketplace_status = _ebay_lifecycle_status(order)
    marketplace_cancel_state = _ebay_cancel_state(order)
    cancel_status = order.get("cancelStatus") or {}
    marketplace_cancelled_at = _parse_ebay_datetime(
        cancel_status.get("cancelledDate") if isinstance(cancel_status, dict) else None
    )

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
    lifecycle_updates = 0
    fulfillment_lifecycle_rows = 0

    for row in rows:
        row_changed = False
        for field, value in values.items():
            if value and _text(getattr(row, field, None)) != _text(value):
                setattr(row, field, value)
                row_changed = True

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
                        row_changed = True
                else:
                    identity_conflicts.append({
                        "row_id": row.id,
                        "conflicting_row_id": conflict.id,
                        "line_item_id": line_id,
                    })

        # eBay's exact shipping_fulfillment resource is marketplace lifecycle
        # truth. A matched fulfillment establishes that row as shipped even when
        # the order-level orderFulfillmentStatus is stale or incomplete. Carrier,
        # tracking and shippedDate remain optional enrichment only. Exact completed
        # cancellation remains authoritative when there is no stronger shipment.
        fulfillment = _best_fulfillment_for_row(row=row, fulfillments=fulfillments)
        row_marketplace_status = marketplace_status
        if marketplace_status not in {"cancel_requested", "cancelled"} and fulfillment is not None:
            row_marketplace_status = "shipped"
        if fulfillment is not None:
            fulfillment_lifecycle_rows += 1

        if row_marketplace_status and _can_apply_marketplace_status(
            getattr(row, "status", ""), row_marketplace_status
        ):
            if _text(getattr(row, "status", "")).lower() != row_marketplace_status:
                row.status = row_marketplace_status
                lifecycle_updates += 1
                row_changed = True

        if fulfillment is not None:
            shipment = _fulfillment_values(fulfillment)
            changed = False
            if shipment["carrier"] and _text(row.carrier) != _text(shipment["carrier"]):
                row.carrier = shipment["carrier"]
                changed = True
            if shipment["tracking_number"] and _text(row.tracking_number) != _text(shipment["tracking_number"]):
                row.tracking_number = shipment["tracking_number"]
                changed = True
            if shipment["shipped_at"] is not None and row.shipped_at != shipment["shipped_at"]:
                row.shipped_at = shipment["shipped_at"]
                changed = True
            if changed:
                tracking_updates += 1
                row_changed = True

        if row_changed:
            row.updated_at = datetime.utcnow()

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

    promise_persisted = _persist_exact_ebay_promise(
        store=store,
        order_id=order_id,
        order_payload=order,
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
        "marketplace_status": marketplace_status,
        "marketplace_cancel_state": marketplace_cancel_state or None,
        "marketplace_cancelled_at": marketplace_cancelled_at.isoformat() if marketplace_cancelled_at else None,
        "required_address_complete": required_address_complete,
        "rows_hydrated": len(rows),
        "identity_updates": identity_updates,
        "identity_conflicts": identity_conflicts,
        "fulfillments_seen": len(fulfillments),
        "fulfillment_lifecycle_rows": fulfillment_lifecycle_rows,
        "tracking_updates": tracking_updates,
        "lifecycle_updates": lifecycle_updates,
        "promise_persisted": promise_persisted,
        "fulfillment_error": fulfillment_error,
        "marketplace_write_started": False,
    }