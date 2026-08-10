"""Exact eBay order hydration for the governed webhook path.

This is not a second order importer. It reuses the existing eBay credential
reader and MarketplaceOrder upsert authority, but reads only the order ID that
the durable webhook has already identified. It never mutates Warehouse stock,
never pushes a marketplace, and never submits MCF itself.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from services.governed_marketplace_order_import import (
    EBAY_ORDERS_URL,
    _ebay_access_token,
    _parse_ebay_datetime,
    _safe_float,
    _safe_int,
    _text,
    upsert_governed_marketplace_order_line,
)


def hydrate_exact_ebay_order(*, store, marketplace_order_id: str, source: str) -> dict[str, Any]:
    """Hydrate one webhook-identified eBay order through existing DB authority."""
    order_id = _text(marketplace_order_id)
    if not order_id:
        return {"success": False, "skipped": True, "reason": "ebay_order_id_missing"}

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
    if _text(order.get("orderId")) and _text(order.get("orderId")) != order_id:
        return {
            "success": False,
            "skipped": False,
            "reason": "exact_ebay_order_identity_mismatch",
            "order_id": order_id,
        }

    instructions = order.get("fulfillmentStartInstructions") or []
    instruction = instructions[0] if instructions else {}
    shipping_step = instruction.get("shippingStep") or {}
    ship_to = shipping_step.get("shipTo") or {}
    contact_address = ship_to.get("contactAddress") or {}

    address_parts = [
        _text(contact_address.get("addressLine1")),
        _text(contact_address.get("addressLine2")),
    ]
    delivery_address = ", ".join(part for part in address_parts if part)
    delivery_name = _text(ship_to.get("fullName"))
    delivery_city = _text(contact_address.get("city"))
    delivery_postcode = _text(contact_address.get("postalCode"))
    delivery_country = _text(contact_address.get("countryCode")).upper()[:2]
    delivery_email = _text(ship_to.get("email"))
    primary_phone = ship_to.get("primaryPhone") or {}
    delivery_phone = (
        _text(primary_phone.get("phoneNumber"))
        or _text(ship_to.get("phoneNumber"))
    )
    marketplace_created_at = _parse_ebay_datetime(order.get("creationDate"))

    results = []
    rows = []
    for item in order.get("lineItems") or []:
        sku = _text(item.get("sku")) or _text(item.get("legacyItemId"))
        line_id = _text(item.get("lineItemId")) or f"{order_id}:{sku}"
        price = item.get("lineItemCost") or {}
        unit_price = _safe_float(price.get("value")) if isinstance(price, dict) else 0.0

        result = upsert_governed_marketplace_order_line(
            store=store,
            marketplace_order_id=order_id,
            marketplace_order_item_id=line_id,
            sku=sku,
            quantity=_safe_int(item.get("quantity")),
            unit_price=unit_price,
            fulfillment_type="FBM",
            status="pending",
            ship_to_name=delivery_name,
            ship_to_address=delivery_address,
            ship_to_city=delivery_city,
            ship_to_postcode=delivery_postcode,
            ship_to_country=delivery_country,
            ship_to_email=delivery_email,
            ship_to_phone=delivery_phone,
            marketplace_created_at=marketplace_created_at,
            import_source=source,
        )
        row = result.pop("_order_row", None)
        if row is not None:
            rows.append(row)
        results.append(result)

    required_address_complete = bool(
        delivery_name
        and delivery_address
        and delivery_city
        and delivery_postcode
        and delivery_country
    )
    return {
        "success": bool(rows) and required_address_complete,
        "skipped": False,
        "reason": (
            None if rows and required_address_complete
            else "exact_ebay_order_missing_mcf_delivery_fields"
        ),
        "order_id": order_id,
        "marketplace_created_at": (
            marketplace_created_at.isoformat() if marketplace_created_at else None
        ),
        "required_address_complete": required_address_complete,
        "rows": rows,
        "results": results,
    }
