"""Resolve ambiguous eBay variation webhooks before governed stock mutation.

The eBay ORDER_CONFIRMATION notification can contain only listingId +
orderLineItemId. Variation SKUs that share one listing ID therefore cannot be
safely resolved from the notification alone. This helper performs one exact
order read only when that listing ID maps to multiple active BT38 SKUs, then
adds the exact line SKU to the existing webhook payload.

It does not create orders, mutate Warehouse stock, push marketplaces, or submit
MCF.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote

import requests

from extensions import db
from models import MarketplaceListing, Store
from services.governed_marketplace_order_import import (
    EBAY_ORDERS_URL,
    _ebay_access_token,
    _text,
)


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _first(payload: dict, names: set[str]) -> str:
    wanted = {name.replace("_", "").lower() for name in names}
    for key, value in _walk(payload or {}):
        if key.replace("_", "").lower() in wanted and value not in (None, ""):
            return _text(value)
    return ""


def enrich_ambiguous_ebay_order_signal(payload: dict) -> dict:
    """Return payload enriched with exact SKU when listing ID is ambiguous."""
    payload = deepcopy(payload or {})

    store_id = payload.get("_bt38_store_id")
    try:
        store_id = int(store_id) if store_id is not None else None
    except (TypeError, ValueError):
        store_id = None

    order_id = _first(payload, {"orderId", "marketplace_order_id", "order_id"})
    listing_id = _first(payload, {"listingId", "external_listing_id", "itemId"})
    notification_line_id = _first(
        payload,
        {"orderLineItemId", "lineItemId", "marketplace_order_item_id"},
    )

    if not (store_id and order_id and listing_id):
        return payload

    candidates = (
        MarketplaceListing.query
        .filter(
            MarketplaceListing.store_id == store_id,
            MarketplaceListing.external_listing_id == listing_id,
            MarketplaceListing.is_active == True,  # noqa: E712
        )
        .order_by(MarketplaceListing.id)
        .all()
    )

    candidate_skus = {
        _text(row.external_sku)
        for row in candidates
        if _text(row.external_sku)
    }
    if len(candidate_skus) <= 1:
        return payload

    store = db.session.get(Store, store_id)
    if store is None or "ebay" not in _text(store.platform).lower():
        raise RuntimeError("ambiguous_ebay_variation_store_unresolved")

    response = requests.get(
        f"{EBAY_ORDERS_URL}/{quote(order_id, safe='')}",
        headers={
            "Authorization": f"Bearer {_ebay_access_token(store)}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            "ambiguous_ebay_variation_exact_order_read_failed:"
            f"{response.status_code}:{response.text[:500]}"
        )

    order = response.json() or {}
    line_items = order.get("lineItems") or []

    exact = None
    if notification_line_id:
        exact = next(
            (
                item
                for item in line_items
                if _text(item.get("lineItemId")) == notification_line_id
            ),
            None,
        )

    if exact is None and len(line_items) == 1:
        exact = line_items[0]

    exact_sku = _text((exact or {}).get("sku"))
    exact_line_id = _text((exact or {}).get("lineItemId"))

    if not exact_sku or exact_sku not in candidate_skus:
        raise RuntimeError(
            "ambiguous_ebay_variation_exact_sku_unresolved:"
            f"order={order_id}:listing={listing_id}:line={notification_line_id}"
        )

    payload["sku"] = exact_sku
    payload["seller_sku"] = exact_sku
    payload["external_sku"] = exact_sku
    if exact_line_id:
        payload["marketplace_order_item_id"] = exact_line_id
        payload["lineItemId"] = exact_line_id

    payload["_bt38_exact_ebay_variation_resolved"] = True
    return payload
