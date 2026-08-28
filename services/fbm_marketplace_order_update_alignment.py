"""Persist marketplace-owned FBM facts when governed orders update.

This is deliberately NOT a page-render path. /fbm stays DB-only and fast.
After the existing governed order update completes, exact marketplace orders
touched by that update are refreshed for shipping facts. A bounded Amazon
backfill also closes historical Prime/SFP profile gaps from the live feed.
Amazon owns IsPrime/EarliestDeliveryDate/LatestDeliveryDate; eBay owns its
line-item fulfilment delivery window. No delivery dates are calculated by BT38.
"""
from __future__ import annotations

from typing import Any

from extensions import db
from models import MarketplaceOrder


def _text(value: Any) -> str:
    return str(value or "").strip()


def _collect_order_ids(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        order_id = _text(value.get("order_id"))
        if order_id:
            found.add(order_id)
        for child in value.values():
            _collect_order_ids(child, found)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_order_ids(child, found)


def _refresh_exact_updated_order(order: MarketplaceOrder) -> dict[str, Any]:
    store = getattr(order, "store", None)
    platform = _text(getattr(store, "platform", None)).lower()
    order_id = _text(getattr(order, "marketplace_order_id", None))
    if not store or not order_id:
        return {"success": False, "skipped": True, "reason": "order_identity_missing"}

    if "amazon" in platform:
        from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile

        profile = get_or_refresh_amazon_profile(order, force=True)
        return {
            "success": True,
            "marketplace": "amazon",
            "order_id": order_id,
            "is_prime": getattr(profile, "is_prime", None),
            "source": "amazon_exact_order",
        }

    if "ebay" in platform:
        from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

        result = hydrate_exact_ebay_order(
            store=store,
            marketplace_order_id=order_id,
            source="governed_order_update_shipping_facts",
        )
        result["source"] = "ebay_exact_order"
        return result

    return {"success": True, "skipped": True, "reason": "unsupported_marketplace"}


def refresh_updated_fbm_marketplace_facts(result: Any) -> list[dict[str, Any]]:
    """Refresh exact order IDs returned by the governed update."""
    order_ids: set[str] = set()
    _collect_order_ids(result, order_ids)
    refreshed: list[dict[str, Any]] = []

    for order_id in sorted(order_ids):
        rows = (
            MarketplaceOrder.query
            .filter(MarketplaceOrder.marketplace_order_id == order_id)
            .order_by(MarketplaceOrder.id)
            .all()
        )
        if not rows:
            continue
        row = rows[0]
        fulfillment = _text(getattr(row, "fulfillment_type", None)).upper()
        if fulfillment in {"FBA", "AFN", "MCF"}:
            continue
        try:
            refreshed.append(_refresh_exact_updated_order(row))
        except Exception as exc:
            db.session.rollback()
            refreshed.append({
                "success": False,
                "order_id": order_id,
                "reason": "marketplace_shipping_fact_refresh_failed",
                "error": str(exc),
            })

    return refreshed


def install_governed_order_update_alignment() -> None:
    """Wrap the existing governed importer in-place; do not create a new path."""
    from services import governed_marketplace_order_import as importer

    current = importer.run_governed_marketplace_order_import
    if getattr(current, "_bt38_fbm_update_alignment", False):
        return

    def aligned_run(*args, **kwargs):
        result = current(*args, **kwargs)
        refreshes = refresh_updated_fbm_marketplace_facts(result)

        # Historical Prime/SFP alignment is intentionally bounded and only runs
        # with the existing governed feed cycle. It never runs from /fbm page
        # rendering and never guesses Prime from premium/NextDay service text.
        from services.fbm_prime_feed_alignment import refresh_amazon_prime_profiles
        try:
            prime_backfill = refresh_amazon_prime_profiles()
        except Exception as exc:
            db.session.rollback()
            prime_backfill = {
                "success": False,
                "reason": "amazon_prime_backfill_failed",
                "error": str(exc),
            }

        if isinstance(result, dict):
            result["fbm_marketplace_facts"] = refreshes
            result["fbm_prime_backfill"] = prime_backfill
        return result

    aligned_run._bt38_fbm_update_alignment = True
    aligned_run.__name__ = current.__name__
    aligned_run.__doc__ = current.__doc__
    importer.run_governed_marketplace_order_import = aligned_run


install_governed_order_update_alignment()
