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


def destination_complete(order: Any) -> bool:
    return all(_text(getattr(order, field, None)) for field in _REQUIRED_DESTINATION_FIELDS)


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

    store = getattr(order, "store", None)
    platform = _text(getattr(store, "platform", None)).casefold() if store is not None else ""
    order_id = _text(getattr(order, "marketplace_order_id", None))
    if store is None or not platform or not order_id:
        return {"success": False, "skipped": True, "reason": "marketplace_identity_missing"}

    try:
        if platform == "amazon":
            from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile

            get_or_refresh_amazon_profile(order, force=True)
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
