"""Exact eBay order hydration for the governed webhook -> MCF path.

The durable webhook already identifies and creates the MarketplaceOrder. This
module reads only that exact eBay order and fills missing delivery/timestamp
fields on those existing rows. It does not create orders, mutate Warehouse
stock, push marketplaces, or submit MCF.
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
from services.fbm_operational_state import update_marketplace_facts
from services.governed_marketplace_order_import import (
    EBAY_ORDERS_URL,
    _ebay_access_token,
    _parse_ebay_datetime,
    _text,
)


def _ebay_shipping_facts(order: dict[str, Any]) -> dict[str, Any]:
    """Read eBay-owned service, ship-by and delivery-window facts.

    Fulfillment API exposes the customer delivery window on each line item's
    lineItemFulfillmentInstructions. For multi-line orders BT38 uses the widest
    buyer promise: earliest minimum and latest maximum. No dates are inferred.
    """
    earliest_values = []
    latest_values = []
    ship_by_values = []
    service = None

    instructions = order.get("fulfillmentStartInstructions") or []
    first_instruction = instructions[0] if instructions else {}
    shipping_step = first_instruction.get("shippingStep") or {}
    service = (
        _text(shipping_step.get("shippingServiceCode"))
        or _text(shipping_step.get("shippingService"))
        or _text(order.get("shippingServiceCode"))
        or _text(order.get("shippingService"))
    )

    for item in order.get("lineItems") or []:
        if not isinstance(item, dict):
            continue
        facts = item.get("lineItemFulfillmentInstructions") or {}
        if not isinstance(facts, dict):
            continue
        earliest = _parse_ebay_datetime(facts.get("minEstimatedDeliveryDate"))
        latest = _parse_ebay_datetime(facts.get("maxEstimatedDeliveryDate"))
        ship_by = _parse_ebay_datetime(facts.get("shipByDate"))
        if earliest:
            earliest_values.append(earliest)
        if latest:
            latest_values.append(latest)
        if ship_by:
            ship_by_values.append(ship_by)
        if not service:
            service = (
                _text(facts.get("shippingServiceCode"))
                or _text(facts.get("shippingService"))
            )

    return {
        "shipping_service": service,
        "earliest_delivery_at": min(earliest_values) if earliest_values else None,
        "latest_delivery_at": max(latest_values) if latest_values else None,
        "ship_by_at": min(ship_by_values) if ship_by_values else None,
    }


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
    shipping_facts = _ebay_shipping_facts(order)

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
                # eBay notifications may initially identify a sold item with the
                # legacy listing/item id. The Fulfillment API then gives us the
                # canonical order lineItemId. Keep BOTH identity columns aligned
                # when canonicalising the row; otherwise the later recovery read
                # builds a different idempotency key and can create a duplicate
                # pending MarketplaceOrder for the same sale.
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

    profile = FBMOrderProfile.query.filter_by(
        store_id=store.id,
        marketplace_order_id=order_id,
    ).first()
    if profile is None:
        profile = FBMOrderProfile(
            store_id=store.id,
            marketplace_order_id=order_id,
            platform="ebay",
        )
    profile.is_prime = False
    profile.is_premium = False
    profile.fulfillment_channel = "FBM"
    if shipping_facts["shipping_service"]:
        profile.shipment_service_level = shipping_facts["shipping_service"]
    if shipping_facts["ship_by_at"]:
        profile.latest_ship_at = shipping_facts["ship_by_at"]
    profile.checked_at = datetime.utcnow()
    profile.last_error = None
    db.session.add(profile)

    update_marketplace_facts(
        rows[0],
        platform="ebay",
        shipping_service=shipping_facts["shipping_service"],
        ship_by_at=shipping_facts["ship_by_at"],
        earliest_delivery_at=shipping_facts["earliest_delivery_at"],
        latest_delivery_at=shipping_facts["latest_delivery_at"],
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
            else (
                None if required_address_complete
                else "exact_ebay_order_missing_mcf_delivery_fields"
            )
        ),
        "order_id": order_id,
        "marketplace_created_at": (
            marketplace_created_at.isoformat() if marketplace_created_at else None
        ),
        "required_address_complete": required_address_complete,
        "rows_hydrated": len(rows),
        "identity_updates": identity_updates,
        "identity_conflicts": identity_conflicts,
        "shipping_service": shipping_facts["shipping_service"],
        "earliest_delivery_at": (
            shipping_facts["earliest_delivery_at"].isoformat()
            if shipping_facts["earliest_delivery_at"] else None
        ),
        "latest_delivery_at": (
            shipping_facts["latest_delivery_at"].isoformat()
            if shipping_facts["latest_delivery_at"] else None
        ),
        "ship_by_at": (
            shipping_facts["ship_by_at"].isoformat()
            if shipping_facts["ship_by_at"] else None
        ),
    }
