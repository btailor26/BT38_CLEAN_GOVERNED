"""Governed marketplace listing-structure reconciliation.

This module refreshes marketplace listing metadata/variation identity only.
Warehouse quantity remains authoritative and this module never starts a
marketplace quantity push.
"""

from __future__ import annotations

from datetime import datetime


def _retire_stale_ebay_siblings(store_id: int, import_result: dict) -> dict:
    """Retire stale DB rows only for eBay ItemIDs refreshed in this run.

    The existing eBay importer fetches the complete current variation set for
    every ItemID it touches. Its affected_listing_ids therefore identify the
    current children for those exact parents. Any other active row for the same
    store + ItemID is an obsolete marketplace identity and must not remain
    pushable after a seller changes variation SKUs/dimensions.

    Product Linking/Warehouse relationships are deliberately left intact on
    retired rows for audit/history; only marketplace activity is disabled.
    """
    from extensions import db
    from models import MarketplaceListing

    affected_ids = {
        int(value)
        for value in (import_result or {}).get("affected_listing_ids", [])
        if value not in (None, "")
    }

    if not affected_ids:
        return {
            "success": True,
            "governed": True,
            "store_id": int(store_id),
            "parents_checked": 0,
            "retired_listing_ids": [],
        }

    current_rows = (
        db.session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.store_id == int(store_id),
            MarketplaceListing.id.in_(sorted(affected_ids)),
        )
        .all()
    )

    current_by_parent = {}
    for row in current_rows:
        parent_id = str(
            getattr(row, "external_listing_id", None)
            or getattr(row, "parent_item_id", None)
            or getattr(row, "external_parent_id", None)
            or ""
        ).strip()
        if not parent_id:
            continue
        current_by_parent.setdefault(parent_id, set()).add(int(row.id))

    retired_ids = []
    now = datetime.utcnow()

    for parent_id, current_ids in current_by_parent.items():
        siblings = (
            db.session.query(MarketplaceListing)
            .filter(
                MarketplaceListing.store_id == int(store_id),
                MarketplaceListing.external_listing_id == parent_id,
                MarketplaceListing.is_active == True,  # noqa: E712
            )
            .all()
        )

        for sibling in siblings:
            if int(sibling.id) in current_ids:
                continue

            sibling.is_active = False
            if hasattr(sibling, "updated_at"):
                sibling.updated_at = now
            if hasattr(sibling, "last_synced_at"):
                sibling.last_synced_at = now
            if hasattr(sibling, "last_push_status"):
                sibling.last_push_status = "retired_marketplace_identity"
            retired_ids.append(int(sibling.id))

    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "store_id": int(store_id),
        "parents_checked": len(current_by_parent),
        "retired_listing_ids": sorted(set(retired_ids)),
    }


def run_governed_listing_structure_reconcile(
    *,
    store_id: int,
    source: str = "governed-listing-structure-reconcile",
) -> dict:
    """Refresh current marketplace listing identities without stock mutation."""
    from extensions import db
    from models import Store

    store = db.session.get(Store, int(store_id))
    if store is None:
        return {
            "success": False,
            "governed": True,
            "store_id": int(store_id),
            "reason": "store_not_found",
            "push_started": False,
            "warehouse_quantity_changed": False,
        }

    platform = str(store.platform or "").strip().lower()

    if "ebay" in platform:
        from services.governed_ebay_inventory_import import (
            run_governed_ebay_inventory_import,
        )

        imported = run_governed_ebay_inventory_import(store_id=store.id)
        retired = _retire_stale_ebay_siblings(store.id, imported)

        return {
            "success": bool(imported.get("success", False)) and bool(retired.get("success", False)),
            "governed": True,
            "marketplace": "ebay",
            "store_id": store.id,
            "source": source,
            "listing_refresh": imported,
            "stale_identity_reconcile": retired,
            "push_started": False,
            "warehouse_quantity_changed": False,
        }

    if "amazon" in platform:
        from services.governed_amazon_listing_fulfillment_refresh import (
            ensure_governed_amazon_listing_notification_subscriptions,
            run_governed_amazon_listing_fulfillment_refresh,
        )

        try:
            subscriptions = ensure_governed_amazon_listing_notification_subscriptions(
                store_id=store.id,
            )
        except Exception as exc:
            subscriptions = {
                "success": False,
                "governed": True,
                "reason": "amazon_listing_subscription_reconcile_failed",
                "error": str(exc),
            }

        refreshed = run_governed_amazon_listing_fulfillment_refresh(
            store_id=store.id,
        )

        return {
            "success": bool(refreshed.get("success", False)),
            "governed": True,
            "marketplace": "amazon",
            "store_id": store.id,
            "source": source,
            "listing_subscriptions": subscriptions,
            "listing_refresh": refreshed,
            "push_started": False,
            "warehouse_quantity_changed": False,
        }

    return {
        "success": True,
        "governed": True,
        "store_id": store.id,
        "platform": store.platform,
        "source": source,
        "skipped": True,
        "reason": "unsupported_marketplace_listing_reconcile",
        "push_started": False,
        "warehouse_quantity_changed": False,
    }
