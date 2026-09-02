"""Bounded recovery of stale marketplace-owned FBM dispatch truth.

This is a one-shot recovery orchestrator, not a scheduler or lifecycle authority.
It selects existing FBM MarketplaceOrder identities whose persisted routine status
has not yet reached marketplace dispatch truth, then delegates each exact order
to the existing marketplace-owned readback implementation.

Rules:
- Marketplace lifecycle is authority for Ready vs Dispatched.
- Carrier/tracking are optional enrichment only.
- Existing orders only; never create/replay an order or mutate Warehouse stock.
- Exact marketplace reads only; never write to a marketplace.
- Historical age is not an authority or exclusion rule.
- Bounded by candidate count and returns immediately when complete.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func

from extensions import db
from models import MarketplaceOrder, Store


_DISPATCHED_STATUSES = {
    "shipped",
    "dispatched",
    "picked_up",
    "accepted",
    "carrier_accepted",
    "collected",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "fulfilled",
    "completed",
}
_PROTECTED_ISSUE_STATUSES = {
    "cancel_requested",
    "cancelled",
    "return_requested",
    "returned",
    "refund_requested",
    "refunded",
    "replacement_requested",
    "replacement",
    "case_open",
    "dispute",
    "chargeback",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _candidate_order_ids(*, store: Store, max_days: int, limit: int) -> list[str]:
    """Select existing stale FBM order identities without age or tracking authority."""
    # max_days is retained in the public recovery signature for compatibility with
    # existing governed callers, but historical age must not exclude an existing
    # FBM order from exact marketplace lifecycle recovery.
    _ = max_days
    effective_limit = max(1, min(int(limit), 250))

    excluded = sorted(_DISPATCHED_STATUSES | _PROTECTED_ISSUE_STATUSES)
    rows = (
        db.session.query(MarketplaceOrder.marketplace_order_id)
        .filter(
            MarketplaceOrder.store_id == int(store.id),
            MarketplaceOrder.fulfillment_type == "FBM",
            MarketplaceOrder.marketplace_order_id.isnot(None),
            MarketplaceOrder.marketplace_order_id != "",
            ~func.lower(func.coalesce(MarketplaceOrder.status, "")).in_(excluded),
        )
        .distinct()
        .order_by(MarketplaceOrder.marketplace_order_id)
        .limit(effective_limit)
        .all()
    )
    return [
        _text(order_id)
        for (order_id,) in rows
        if _text(order_id)
    ]


def _recover_store(*, store: Store, max_days: int, limit: int) -> dict[str, Any]:
    platform = _text(getattr(store, "platform", None)).lower()
    order_ids = _candidate_order_ids(store=store, max_days=max_days, limit=limit)
    results: list[dict[str, Any]] = []
    lifecycle_updates = 0
    exceptions = 0

    if "ebay" in platform:
        from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

        def hydrate(order_id: str) -> dict[str, Any]:
            return hydrate_exact_ebay_order(
                store=store,
                marketplace_order_id=order_id,
                source="bounded_marketplace_dispatch_truth_recovery",
            )

    elif "amazon" in platform:
        from services.governed_amazon_tracking_readback import hydrate_amazon_tracking_for_order

        def hydrate(order_id: str) -> dict[str, Any]:
            return hydrate_amazon_tracking_for_order(
                store=store,
                marketplace_order_id=order_id,
                source="bounded_marketplace_dispatch_truth_recovery",
            )

    else:
        return {
            "success": False,
            "reason": "unsupported_marketplace",
            "store_id": int(store.id),
            "platform": platform,
            "candidate_orders": len(order_ids),
            "marketplace_write_started": False,
            "stock_mutation_started": False,
        }

    for order_id in order_ids:
        try:
            result = hydrate(order_id)
        except Exception as exc:
            db.session.rollback()
            exceptions += 1
            result = {
                "success": False,
                "order_id": order_id,
                "reason": "exact_marketplace_dispatch_truth_recovery_exception",
                "error": str(exc),
                "marketplace_write_started": False,
            }
        lifecycle_updates += int(result.get("lifecycle_updates") or 0)
        results.append(result)

    return {
        "success": exceptions == 0,
        "bounded": True,
        "store_id": int(store.id),
        "platform": platform,
        "historical_age_restricted": False,
        "candidate_limit": max(1, min(int(limit), 250)),
        "candidate_orders": len(order_ids),
        "lifecycle_updates": lifecycle_updates,
        "exceptions": exceptions,
        "results": results,
        "marketplace_write_started": False,
        "stock_mutation_started": False,
        "order_replayed": False,
        "polling_started": False,
        "scheduler_started": False,
    }


def recover_bounded_marketplace_dispatch_truth(
    *,
    store_ids: tuple[int, ...] = (22, 23),
    max_days: int = 90,
    limit_per_store: int = 150,
) -> dict[str, Any]:
    """Recover stale existing FBM lifecycle truth for configured marketplaces once."""
    stores: list[dict[str, Any]] = []
    for store_id in store_ids:
        store = db.session.get(Store, int(store_id))
        if store is None:
            stores.append({
                "success": False,
                "reason": "store_missing",
                "store_id": int(store_id),
                "marketplace_write_started": False,
                "stock_mutation_started": False,
            })
            continue
        if not bool(getattr(store, "is_active", False)):
            stores.append({
                "success": False,
                "reason": "store_inactive",
                "store_id": int(store_id),
                "marketplace_write_started": False,
                "stock_mutation_started": False,
            })
            continue
        stores.append(_recover_store(
            store=store,
            max_days=max_days,
            limit=limit_per_store,
        ))

    return {
        "success": all(bool(row.get("success")) for row in stores),
        "bounded": True,
        "historical_age_restricted": False,
        "limit_per_store": max(1, min(int(limit_per_store), 250)),
        "stores": stores,
        "candidate_orders": sum(int(row.get("candidate_orders") or 0) for row in stores),
        "lifecycle_updates": sum(int(row.get("lifecycle_updates") or 0) for row in stores),
        "marketplace_write_started": False,
        "stock_mutation_started": False,
        "order_replayed": False,
        "polling_started": False,
        "scheduler_started": False,
    }
