"""Bounded eBay missed-listing recovery for the low-DB governed runtime.

This module is discovery-only. It never writes MarketplaceListing rows itself;
missing Item IDs are handed to the existing governed eBay importer writer.

Contract:
- inspect only the newest active eBay Item IDs;
- one DB existence query per store per recovery cycle;
- skip every Item ID already present in MarketplaceListing;
- import only genuinely missing Item IDs;
- one commit for recovered rows;
- no catalogue pagination, order recovery, heartbeat or sync-log write.
"""
from __future__ import annotations

from typing import Any

from extensions import db
from models import MarketplaceListing, Store
from services.governed_ebay_inventory_import import (
    _get_active_items,
    _import_item,
    _parse_creds,
    _refresh_access_token_if_needed,
    _xml_text,
)


MAX_NEWEST_ITEMS = 100


def recover_missed_ebay_listings(
    *,
    store_id: int | None = None,
    newest_limit: int = MAX_NEWEST_ITEMS,
) -> dict[str, Any]:
    """Recover only active eBay Item IDs absent from BT38."""

    limit = max(1, min(int(newest_limit or MAX_NEWEST_ITEMS), MAX_NEWEST_ITEMS))

    query = (
        db.session.query(Store)
        .filter(Store.platform.ilike("%ebay%"))
        .filter(Store.is_active == True)  # noqa: E712
    )
    if hasattr(Store, "store_mode"):
        query = query.filter(Store.store_mode == "live")
    if store_id is not None:
        query = query.filter(Store.id == int(store_id))

    stores = query.order_by(Store.id.asc()).all()
    results: list[dict[str, Any]] = []
    total_missing = 0
    total_imported = 0
    affected_listing_ids: set[int] = set()
    affected_warehouse_stock_ids: set[int] = set()
    affected_group_ids: set[int] = set()

    for store in stores:
        creds_before = _parse_creds(store)
        prior_token = str(creds_before.get("access_token") or "")
        creds = _refresh_access_token_if_needed(store, creds_before)

        # Persist a token refresh if one was required. This is not a periodic
        # application write; it occurs only when eBay credentials need renewal.
        if str(creds.get("access_token") or "") != prior_token:
            db.session.commit()

        if not creds.get("access_token"):
            results.append({
                "store_id": int(store.id),
                "success": False,
                "reason": "missing_ebay_access_token",
                "examined": 0,
                "missing": 0,
                "imported": 0,
            })
            continue

        newest_items = _get_active_items(
            creds,
            page=1,
            entries=limit,
            sort="StartTimeDescending",
        )

        candidates: dict[str, Any] = {}
        for item in newest_items:
            item_id = _xml_text(item, "{*}ItemID")
            if item_id and item_id not in candidates:
                candidates[item_id] = item

        item_ids = list(candidates)
        if not item_ids:
            results.append({
                "store_id": int(store.id),
                "success": True,
                "examined": 0,
                "missing": 0,
                "imported": 0,
                "database_listing_writes": 0,
            })
            continue

        # One existence query. Existing Item IDs are untouched, so the recovery
        # does not refresh, rewrite or re-link already-known listings.
        existing_rows = (
            db.session.query(MarketplaceListing.external_listing_id)
            .filter(
                MarketplaceListing.store_id == int(store.id),
                MarketplaceListing.external_listing_id.in_(item_ids),
            )
            .distinct()
            .all()
        )
        existing_ids = {
            str(row[0]).strip()
            for row in existing_rows
            if row and row[0] not in (None, "")
        }
        missing_ids = [item_id for item_id in item_ids if item_id not in existing_ids]
        total_missing += len(missing_ids)

        store_imported = 0
        for item_id in missing_ids:
            # _import_item -> _upsert_listing remains the sole listing writer.
            # _upsert_listing rechecks the stable identity, protecting a race
            # where a webhook imports the same Item ID after the existence query.
            counts = _import_item(store, creds, candidates[item_id])
            store_imported += int(counts.get("items") or 0)
            affected_listing_ids.update(counts.get("affected_listing_ids") or [])
            affected_warehouse_stock_ids.update(
                counts.get("affected_warehouse_stock_ids") or []
            )
            affected_group_ids.update(counts.get("affected_group_ids") or [])

        if missing_ids:
            db.session.commit()

        total_imported += store_imported
        results.append({
            "store_id": int(store.id),
            "success": True,
            "examined": len(item_ids),
            "missing": len(missing_ids),
            "imported": store_imported,
            "database_listing_writes": store_imported,
            "full_catalogue_scan": False,
            "order_recovery_started": False,
        })

    return {
        "success": all(row.get("success", False) for row in results) if results else True,
        "governed": True,
        "marketplace": "ebay",
        "bounded_missed_listing_recovery": True,
        "newest_limit": limit,
        "stores_checked": len(results),
        "missing": total_missing,
        "imported": total_imported,
        "affected_listing_ids": sorted(affected_listing_ids),
        "affected_warehouse_stock_ids": sorted(affected_warehouse_stock_ids),
        "affected_group_ids": sorted(affected_group_ids),
        "full_catalogue_scan": False,
        "order_recovery_started": False,
    }
