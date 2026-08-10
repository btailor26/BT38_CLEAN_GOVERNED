"""Bounded startup retry for already-linked failed Amazon MCF submissions.

This selector exists only to resume MCF rows that were staged and linked to an
eBay MarketplaceOrder but never accepted by Amazon. It does not construct a
second MCF order or dispatch a marketplace directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from extensions import db
from models import MCFOrder, MarketplaceOrder, Store


def retry_failed_linked_mcf(
    *,
    limit: int = 10,
    max_age_hours: int = 72,
) -> dict[str, Any]:
    from governed_mcf_routes import run_governed_mcf_submission
    from services.governed_exact_ebay_order_hydration import (
        hydrate_exact_ebay_order,
    )

    cutoff = datetime.utcnow() - timedelta(hours=int(max_age_hours))
    rows = (
        db.session.query(MarketplaceOrder)
        .join(Store, Store.id == MarketplaceOrder.store_id)
        .join(MCFOrder, MCFOrder.id == MarketplaceOrder.mcf_order_id)
        .filter(
            MarketplaceOrder.created_at >= cutoff,
            MarketplaceOrder.shipped_at.is_(None),
            MCFOrder.status == "failed",
            MCFOrder.amazon_status.is_(None),
            Store.is_active == True,  # noqa: E712
            Store.platform.ilike("%ebay%"),
        )
        .order_by(MarketplaceOrder.id.asc())
        .limit(max(int(limit) * 3, int(limit)))
        .all()
    )

    selected = []
    seen = set()
    for row in rows:
        key = (int(row.store_id), str(row.marketplace_order_id or "").strip())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= int(limit):
            break

    retried = 0
    failed = 0
    skipped = 0
    results = []

    for anchor in selected:
        try:
            hydration = hydrate_exact_ebay_order(
                store=anchor.store,
                marketplace_order_id=anchor.marketplace_order_id,
                source="startup_retry_failed_linked_mcf_hydration",
            )
            if not hydration.get("success"):
                failed += 1
                results.append({
                    "marketplace_order_row_id": anchor.id,
                    "success": False,
                    "reason": "exact_ebay_order_hydration_failed",
                    "hydration": hydration,
                })
                continue

            result = run_governed_mcf_submission(
                int(anchor.id),
                auto_release=True,
                form_data={},
                actor_user=None,
            )
            if result.get("success"):
                retried += 1
            elif result.get("skipped"):
                skipped += 1
            else:
                failed += 1

            results.append({
                "marketplace_order_row_id": anchor.id,
                "marketplace_order_id": anchor.marketplace_order_id,
                "mcf_order_id": anchor.mcf_order_id,
                "success": bool(result.get("success")),
                "submission": result,
            })
        except Exception as exc:
            db.session.rollback()
            failed += 1
            results.append({
                "marketplace_order_row_id": anchor.id,
                "success": False,
                "reason": "exception",
                "error": str(exc),
            })

    return {
        "success": failed == 0,
        "checked": len(selected),
        "retried": retried,
        "skipped": skipped,
        "failed": failed,
        "results": results,
        "full_scan_started": False,
        "new_worker_started": False,
        "new_queue_created": False,
    }
