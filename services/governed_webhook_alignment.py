"""Exact 15-minute alignment check for existing webhook pushes.

This is not a new stock or push path. It checks only the Warehouse and listing
IDs recorded by the existing governed webhook push and reuses that same governed
push path when those exact rows are not aligned.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _warehouse_quantity(stock: Any) -> int:
    for name in ("sellable_quantity", "available_quantity", "quantity"):
        if hasattr(stock, name):
            value = getattr(stock, name, None)
            if value is not None:
                return _safe_int(value)
    return 0


def _is_fba_read_only(listing: Any) -> bool:
    platform = _text(
        getattr(getattr(listing, "store", None), "platform", None)
        or getattr(listing, "platform", None)
    ).lower()
    channel = _text(
        getattr(listing, "normalized_amazon_fulfillment_channel", None)
        or getattr(listing, "amazon_fulfillment_channel", None)
    ).upper()
    return bool(
        getattr(listing, "is_fba", False)
        or channel in {"AFN", "FBA"}
        or ("amazon" in platform and channel not in {"MFN", "FBM", "MERCHANT"})
    )


def verify_existing_webhook_alignment(event: dict[str, Any]) -> dict[str, Any]:
    """Verify only the exact Warehouse row and listing IDs saved by the webhook."""
    from extensions import db
    from models import MarketplaceListing, WarehouseStock
    from services.governed_push_execution import (
        push_group_listings,
        push_marketplace_listing,
    )

    warehouse_stock_id = event.get("warehouse_stock_id")
    listing_ids = []
    for value in list(event.get("listing_ids") or []):
        try:
            listing_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    listing_ids = sorted(set(listing_ids))

    if warehouse_stock_id is None or not listing_ids:
        return {
            "verified": False,
            "aligned": False,
            "skipped": True,
            "reason": "exact_alignment_scope_required",
            "database_touched": False,
        }

    stock = db.session.get(WarehouseStock, int(warehouse_stock_id))
    if stock is None:
        return {
            "verified": False,
            "aligned": False,
            "reason": "warehouse_stock_missing",
            "warehouse_stock_id": warehouse_stock_id,
            "rows_examined_max": 1,
        }

    listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.id.in_(listing_ids))
        .order_by(MarketplaceListing.id)
        .all()
    )

    expected_quantity = _warehouse_quantity(stock)
    expected_group_id = event.get("group_id")
    aligned_ids = []
    read_only_ids = []
    misaligned_ids = []

    for listing in listings:
        if _is_fba_read_only(listing):
            read_only_ids.append(listing.id)
            continue

        relationship_ok = (
            int(getattr(listing, "warehouse_stock_id", 0) or 0) == int(stock.id)
        )
        if expected_group_id is not None:
            relationship_ok = relationship_ok and (
                int(getattr(listing, "master_product_group_id", 0) or 0)
                == int(expected_group_id)
            )

        last_push_quantity = getattr(listing, "last_push_quantity", None)
        last_marketplace_qty = getattr(listing, "last_marketplace_qty", None)
        pushed_quantity_ok = (
            last_push_quantity is not None
            and _safe_int(last_push_quantity) == expected_quantity
        )
        marketplace_quantity_ok = (
            last_marketplace_qty is not None
            and _safe_int(last_marketplace_qty) == expected_quantity
        )
        quantity_ok = pushed_quantity_ok or marketplace_quantity_ok
        status_ok = (
            marketplace_quantity_ok
            or _text(getattr(listing, "last_push_status", None)).lower() == "success"
        )

        if relationship_ok and quantity_ok and status_ok:
            aligned_ids.append(listing.id)
        else:
            misaligned_ids.append(listing.id)

    retry_result = None
    if misaligned_ids:
        if expected_group_id is not None:
            retry_result = push_group_listings(
                group_id=int(expected_group_id),
                actor="system:15m_webhook_verification",
                source="webhook_alignment_15m_retry",
                actor_user=None,
            )
        else:
            retry_results = [
                push_marketplace_listing(
                    listing_id=listing_id,
                    actor="system:15m_webhook_verification",
                    source="webhook_alignment_15m_retry",
                    actor_user=None,
                )
                for listing_id in misaligned_ids
            ]
            retry_result = {
                "success": all(
                    bool(item.get("ok") or item.get("success"))
                    for item in retry_results
                ),
                "results": retry_results,
            }

    retry_ok = bool(
        retry_result is None
        or retry_result.get("ok")
        or retry_result.get("success")
    )

    return {
        "verified": True,
        "aligned": not misaligned_ids or retry_ok,
        "warehouse_stock_id": stock.id,
        "group_id": expected_group_id,
        "expected_quantity": expected_quantity,
        "listing_ids": listing_ids,
        "aligned_listing_ids": aligned_ids,
        "read_only_listing_ids": read_only_ids,
        "misaligned_listing_ids": misaligned_ids,
        "targeted_retry_started": bool(misaligned_ids),
        "targeted_retry_result": retry_result,
        "rows_examined_max": 1 + len(listing_ids),
        "full_scan_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
    }
