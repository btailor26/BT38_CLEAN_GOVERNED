"""Bounded eBay missed-listing recovery for the low-DB governed runtime.

This module is discovery-only. It never writes MarketplaceListing rows itself;
missing or structurally changed Item IDs are handed to the existing governed
eBay importer writer.

Contract:
- inspect only the newest active eBay Item IDs;
- one DB identity query per store per recovery cycle;
- import genuinely missing Item IDs;
- refresh an existing Item ID only when its seller-SKU structure differs from
  the current BT38 MarketplaceListing rows (for example a newly added variation);
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


def _candidate_skus(item: Any) -> set[str]:
    """Read the seller-SKU structure already present in GetMyeBaySelling."""
    variation_skus = {
        _xml_text(variation, "{*}SKU")
        for variation in item.findall(".//{*}Variations/{*}Variation")
        if _xml_text(variation, "{*}SKU")
    }
    if variation_skus:
        return variation_skus

    parent_sku = _xml_text(item, "{*}SKU")
    return {parent_sku} if parent_sku else set()


def recover_missed_ebay_listings(
    *,
    store_id: int | None = None,
    newest_limit: int = MAX_NEWEST_ITEMS,
) -> dict[str, Any]:
    """Recover missing Item IDs and changed variation-SKU structures."""

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
    total_changed = 0
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
                "changed": 0,
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
        candidate_skus: dict[str, set[str]] = {}
        for item in newest_items:
            item_id = _xml_text(item, "{*}ItemID")
            if item_id and item_id not in candidates:
                candidates[item_id] = item
                candidate_skus[item_id] = _candidate_skus(item)

        item_ids = list(candidates)
        if not item_ids:
            results.append({
                "store_id": int(store.id),
                "success": True,
                "examined": 0,
                "missing": 0,
                "changed": 0,
                "imported": 0,
                "database_listing_writes": 0,
            })
            continue

        # One identity query for the bounded set. This lets recovery distinguish
        # an existing parent Item ID from an unchanged variation structure.
        existing_rows = (
            db.session.query(
                MarketplaceListing.external_listing_id,
                MarketplaceListing.external_sku,
            )
            .filter(
                MarketplaceListing.store_id == int(store.id),
                MarketplaceListing.external_listing_id.in_(item_ids),
                MarketplaceListing.is_active == True,  # noqa: E712
            )
            .all()
        )
        existing_skus: dict[str, set[str]] = {}
        for external_listing_id, external_sku in existing_rows:
            item_id = str(external_listing_id or "").strip()
            sku = str(external_sku or "").strip()
            if not item_id:
                continue
            existing_skus.setdefault(item_id, set())
            if sku:
                existing_skus[item_id].add(sku)

        missing_ids = [item_id for item_id in item_ids if item_id not in existing_skus]
        changed_ids = [
            item_id
            for item_id in item_ids
            if item_id in existing_skus
            and candidate_skus.get(item_id)
            and candidate_skus.get(item_id) != existing_skus.get(item_id, set())
        ]
        recovery_ids = list(dict.fromkeys([*missing_ids, *changed_ids]))

        total_missing += len(missing_ids)
        total_changed += len(changed_ids)

        store_imported = 0
        for item_id in recovery_ids:
            # _import_item -> _upsert_listing remains the sole listing writer.
            # It fetches exact item detail before writing, so a changed parent
            # hydrates its complete current variation set through the canonical path.
            counts = _import_item(store, creds, candidates[item_id])
            store_imported += int(counts.get("items") or 0)
            affected_listing_ids.update(counts.get("affected_listing_ids") or [])
            affected_warehouse_stock_ids.update(
                counts.get("affected_warehouse_stock_ids") or []
            )
            affected_group_ids.update(counts.get("affected_group_ids") or [])

        if recovery_ids:
            db.session.commit()

        total_imported += store_imported
        results.append({
            "store_id": int(store.id),
            "success": True,
            "examined": len(item_ids),
            "missing": len(missing_ids),
            "changed": len(changed_ids),
            "changed_item_ids": changed_ids,
            "recovered_item_ids": recovery_ids,
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
        "changed": total_changed,
        "imported": total_imported,
        "affected_listing_ids": sorted(affected_listing_ids),
        "affected_warehouse_stock_ids": sorted(affected_warehouse_stock_ids),
        "affected_group_ids": sorted(affected_group_ids),
        "full_catalogue_scan": False,
        "order_recovery_started": False,
    }
