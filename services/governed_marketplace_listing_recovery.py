"""Marketplace listing recovery dispatcher.

This module routes marketplace listing notifications through the existing
marketplace-specific recovery path.

It does not:
- construct MarketplaceListing rows;
- mutate WarehouseStock directly;
- create a second importer;
- create a second marketplace writer;
- run recurring scans.

For eBay listing notifications only, a successful recovery is followed by a
bounded warehouse-authority comparison. Any recovered listing whose observed
eBay quantity differs from its existing governed effective quantity is handed
to the existing governed push service. The push service remains the only
quantity writer and still owns Product Linking expansion, fuses, audit state and
marketplace verification.
"""

from __future__ import annotations

from typing import Any


def _push_ebay_warehouse_corrections(
    recovery: dict[str, Any],
    *,
    event_type: str,
) -> dict[str, Any]:
    """Hand exact recovered eBay mismatches to the existing governed writer."""
    from extensions import db
    from models import MarketplaceListing
    from services.governed_push_execution import push_marketplace_listing

    raw_listing_ids = list((recovery or {}).get("affected_listing_ids") or [])
    listing_ids = []
    for value in raw_listing_ids:
        try:
            listing_id = int(value)
        except (TypeError, ValueError):
            continue
        if listing_id not in listing_ids:
            listing_ids.append(listing_id)

    results = []
    attempted = 0
    skipped_aligned = 0

    for listing_id in listing_ids:
        listing = db.session.get(MarketplaceListing, listing_id)
        if listing is None or not bool(getattr(listing, "is_active", False)):
            results.append({
                "listing_id": listing_id,
                "attempted": False,
                "reason": "listing_missing_or_inactive",
            })
            continue

        stock = getattr(listing, "warehouse_stock", None)
        if stock is None:
            results.append({
                "listing_id": listing_id,
                "attempted": False,
                "reason": "warehouse_stock_missing",
            })
            continue

        try:
            warehouse_quantity = max(0, int(getattr(listing, "effective_quantity", 0) or 0))
        except (TypeError, ValueError):
            results.append({
                "listing_id": listing_id,
                "attempted": False,
                "reason": "governed_quantity_invalid",
            })
            continue

        observed = getattr(listing, "last_marketplace_qty", None)
        try:
            marketplace_quantity = int(observed) if observed is not None else None
        except (TypeError, ValueError):
            marketplace_quantity = None

        if marketplace_quantity is not None and marketplace_quantity == warehouse_quantity:
            skipped_aligned += 1
            results.append({
                "listing_id": listing_id,
                "attempted": False,
                "aligned": True,
                "warehouse_quantity": warehouse_quantity,
                "marketplace_quantity": marketplace_quantity,
            })
            continue

        attempted += 1
        push_result = push_marketplace_listing(
            listing_id=listing_id,
            actor="governed-ebay-listing-recovery",
            source="webhook_ebay_listing_recovery",
            dry_run=False,
        )
        results.append({
            "listing_id": listing_id,
            "attempted": True,
            "warehouse_quantity": warehouse_quantity,
            "marketplace_quantity": marketplace_quantity,
            "push": push_result,
        })

    return {
        "governed": True,
        "marketplace": "ebay",
        "event_type": str(event_type or ""),
        "affected_listing_ids": listing_ids,
        "attempted": attempted,
        "skipped_aligned": skipped_aligned,
        "results": results,
    }


def recover_governed_marketplace_listing(
    *,
    marketplace: str,
    store_id: int | None,
    event_type: str,
    seller_sku: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    marketplace = str(marketplace or "").strip().lower()

    if marketplace == "amazon":
        from services.governed_amazon_listing_fulfillment_refresh import (
            recover_governed_amazon_listing_from_notification,
        )

        return recover_governed_amazon_listing_from_notification(
            store_id=store_id,
            event_type=event_type,
            seller_sku=seller_sku,
        )

    if marketplace == "ebay":
        from services.governed_ebay_inventory_import import (
            recover_governed_ebay_listing_from_notification,
        )

        recovery = recover_governed_ebay_listing_from_notification(
            store_id=store_id,
            event_type=event_type,
            payload=payload,
        )

        if not bool((recovery or {}).get("success")):
            return recovery

        result = dict(recovery or {})
        correction = _push_ebay_warehouse_corrections(
            result,
            event_type=event_type,
        )
        result["warehouse_correction"] = correction
        result["correction_started"] = bool(correction.get("attempted"))
        return result

    return {
        "success": False,
        "governed": True,
        "applicable": False,
        "reason": "unsupported_marketplace_listing_recovery",
        "marketplace": marketplace,
        "event_type": str(event_type or ""),
    }
