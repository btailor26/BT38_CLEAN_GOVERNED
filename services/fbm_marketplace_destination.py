"""Marketplace-owned FBM destination hydration.

BT38 keeps MarketplaceOrder as the order source of truth. This helper only
fills shipping facts on an already-existing order by reading that exact order
from its marketplace when BT38 is missing required delivery fields.

Rules:
- never invent a delivery address;
- never create an order;
- never mutate stock;
- never dispatch or buy postage;
- supported marketplaces use their exact-order reader;
- duplicate DB rows for one marketplace order must reuse the same buyer address;
- unsupported marketplaces fail closed and keep the current DB values.
"""
from __future__ import annotations

from typing import Any


_REQUIRED_DESTINATION_FIELDS = (
    "ship_to_name",
    "ship_to_address",
    "ship_to_city",
    "ship_to_postcode",
    "ship_to_country",
)

_DESTINATION_FIELDS = (
    "ship_to_name",
    "ship_to_address",
    "ship_to_address2",
    "ship_to_city",
    "ship_to_region",
    "ship_to_postcode",
    "ship_to_country",
    "ship_to_email",
    "ship_to_phone",
)


def destination_complete(order: Any) -> bool:
    return all(_text(getattr(order, field, None)) for field in _REQUIRED_DESTINATION_FIELDS)


def _copy_destination(source: Any, target: Any) -> bool:
    changed = False
    for field in _DESTINATION_FIELDS:
        value = _text(getattr(source, field, None))
        if value and _text(getattr(target, field, None)) != value:
            setattr(target, field, value)
            changed = True
    return changed


def _hydrate_from_complete_sibling(order: Any) -> bool:
    """Reuse a complete buyer destination already stored for the same order.

    eBay webhook/recovery paths can temporarily leave more than one DB row for
    the same marketplace order. Shipping must never depend on which duplicate
    row the UI happened to select. If any sibling already owns the exact buyer
    address, copy that address to the selected row before any provider handoff.
    """
    store_id = getattr(order, "store_id", None)
    order_id = _text(getattr(order, "marketplace_order_id", None))
    if store_id is None or not order_id:
        return False

    from extensions import db
    from models import MarketplaceOrder

    siblings = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == store_id,
            MarketplaceOrder.marketplace_order_id == order_id,
        )
        .order_by(MarketplaceOrder.id.asc())
        .all()
    )
    source = next((row for row in siblings if destination_complete(row)), None)
    if source is None:
        return False

    changed = False
    for row in siblings:
        changed = _copy_destination(source, row) or changed
    if changed:
        db.session.commit()
    return destination_complete(order)


def hydrate_marketplace_destination(order: Any, *, force: bool = False) -> dict[str, Any]:
    """Hydrate exact marketplace delivery facts onto an existing DB order.

    The function is intentionally provider-neutral. Packlink, or any future
    external shipping provider, gets the same marketplace-owned destination
    rule instead of implementing marketplace-specific address fallbacks.
    """
    if order is None:
        return {"success": False, "skipped": True, "reason": "order_missing"}

    if destination_complete(order) and not force:
        return {"success": True, "skipped": True, "reason": "destination_already_complete"}

    if _hydrate_from_complete_sibling(order) and not force:
        return {
            "success": True,
            "skipped": True,
            "reason": "destination_reused_from_same_marketplace_order",
        }

    store = getattr(order, "store", None)
    platform = _text(getattr(store, "platform", None)).casefold() if store is not None else ""
    order_id = _text(getattr(order, "marketplace_order_id", None))
    if store is None or not platform or not order_id:
        return {"success": False, "skipped": True, "reason": "marketplace_identity_missing"}

    try:
        if platform == "amazon":
            from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile

            get_or_refresh_amazon_profile(order, force=True)
            _hydrate_from_complete_sibling(order)
            return {
                "success": destination_complete(order),
                "skipped": False,
                "reason": None if destination_complete(order) else "amazon_destination_incomplete",
                "platform": platform,
                "order_id": order_id,
            }

        if platform == "ebay":
            from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

            result = hydrate_exact_ebay_order(
                store=store,
                marketplace_order_id=order_id,
                source="fbm_exact_destination_hydration",
            )
            _hydrate_from_complete_sibling(order)
            return {
                **(result or {}),
                "success": destination_complete(order),
                "platform": platform,
                "order_id": order_id,
            }
    except Exception as exc:
        return {
            "success": False,
            "skipped": False,
            "reason": "marketplace_destination_read_failed",
            "platform": platform,
            "order_id": order_id,
            "error": str(exc),
        }

    return {
        "success": False,
        "skipped": True,
        "reason": "exact_destination_reader_not_configured",
        "platform": platform,
        "order_id": order_id,
    }


def _text(value: Any) -> str:
    return str(value or "").strip()
