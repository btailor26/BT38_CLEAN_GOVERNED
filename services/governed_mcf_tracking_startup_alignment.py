"""Recover MCF tracking enrichment after Fly sleep/restart.

The existing runtime already rebuilds unfinished MCF lifecycle work at startup,
but it historically treated ``MarketplaceOrder.shipped_at`` as terminal. That is
not valid for the governed eBay -> Amazon MCF flow because eBay is deliberately
marked dispatched before Amazon later publishes carrier/tracking details.

This narrow alignment keeps the existing recovery and marketplace execution
paths. After the normal bounded MCF startup recovery runs, it checks only recent
external-marketplace MCF orders that have already been dispatched but whose
tracking enrichment is not complete. Each exact MCF order is then refreshed
through ``refresh_mcf_from_amazon_signal`` -- the same Amazon-authoritative
multi-tracking and eBay CompleteSale enrichment path used by live notifications.

No new table, worker, scheduler, marketplace scan, dispatch path, or stock rule is
introduced.
"""
from __future__ import annotations

from datetime import datetime, timedelta


_CANCELLED = {
    "cancelled",
    "canceled",
    "cancellation",
    "cancel_requested",
}


def _tracking_enrichment_complete(lines) -> bool:
    """Return True only when every source line has confirmed tracking applied."""
    if not lines:
        return False

    for line in lines:
        status = str(getattr(line, "status", "") or "").strip().lower()
        tracking = str(
            getattr(line, "tracking_number", "") or ""
        ).strip()
        if status != "mcf_tracking_updated" or not tracking:
            return False

    return True


def _recover_dispatched_tracking_pending(app) -> dict:
    """Refresh exact recently-dispatched MCF orders still waiting for tracking."""
    from models import MarketplaceOrder
    from services.governed_mcf_execution import (
        refresh_mcf_from_amazon_signal,
    )

    now = datetime.utcnow()
    recovery_since = now - timedelta(hours=48)

    recovered = 0
    completed = 0
    no_tracking_yet = 0
    failed = 0
    skipped = 0
    examined_orders = 0

    with app.app_context():
        rows = (
            MarketplaceOrder.query
            .filter(
                MarketplaceOrder.created_at >= recovery_since,
                MarketplaceOrder.mcf_order_id.isnot(None),
                MarketplaceOrder.shipped_at.isnot(None),
            )
            .order_by(
                MarketplaceOrder.created_at.asc(),
                MarketplaceOrder.id.asc(),
            )
            .limit(250)
            .all()
        )

        seen = set()
        for row in rows:
            key = (row.store_id, row.marketplace_order_id)
            if key in seen:
                continue
            seen.add(key)
            examined_orders += 1

            store = getattr(row, "store", None)
            platform = str(
                getattr(store, "platform", "") or ""
            ).strip().lower()
            if not platform or "amazon" in platform:
                skipped += 1
                continue

            lines = (
                MarketplaceOrder.query
                .filter(
                    MarketplaceOrder.store_id == row.store_id,
                    MarketplaceOrder.marketplace_order_id
                    == row.marketplace_order_id,
                )
                .order_by(MarketplaceOrder.id)
                .all()
            )

            if any(
                str(line.status or "").strip().lower() in _CANCELLED
                for line in lines
            ):
                skipped += 1
                continue

            mcf = next(
                (
                    line.mcf_order
                    for line in lines
                    if line.mcf_order_id and line.mcf_order is not None
                ),
                None,
            )
            if mcf is None:
                skipped += 1
                continue

            mcf_status = str(mcf.status or "").strip().lower()
            if mcf_status in {"cancelled", "failed"}:
                skipped += 1
                continue

            # ``shipped_at`` proves only that the initial source-marketplace
            # dispatch happened. Tracking enrichment is a later lifecycle
            # phase and remains unfinished until every line carries the
            # explicit mcf_tracking_updated state and a tracking number.
            if _tracking_enrichment_complete(lines):
                completed += 1
                continue

            seller_id = str(
                mcf.seller_fulfillment_order_id or ""
            ).strip()
            if not seller_id:
                skipped += 1
                continue

            result = refresh_mcf_from_amazon_signal({
                "sellerFulfillmentOrderId": seller_id,
                "startup_recovered": True,
                "source": "mcf_tracking_startup_recovery",
            })
            recovered += 1

            if not result.get("success"):
                failed += 1
                continue

            reason = str(result.get("reason") or "")
            if reason == "amazon_mcf_tracking_not_available_yet":
                no_tracking_yet += 1

    return {
        "success": failed == 0,
        "governed": True,
        "bounded": True,
        "recovery_hours": 48,
        "rows_examined_max": 250,
        "orders_examined": examined_orders,
        "tracking_refreshes": recovered,
        "already_enriched": completed,
        "tracking_not_available_yet": no_tracking_yet,
        "failed": failed,
        "skipped": skipped,
        "full_scan_started": False,
        "marketplace_import_started": False,
        "warehouse_scan_started": False,
        "new_worker_started": False,
        "new_scheduler_started": False,
    }


def install_mcf_tracking_startup_alignment() -> bool:
    """Extend existing bounded MCF startup recovery with tracking recovery."""
    import services.governed_runtime_engine as runtime

    current = runtime._recover_mcf_auto_release_events
    if getattr(current, "_bt38_mcf_tracking_startup_aligned", False):
        return False

    def aligned_recovery(app):
        result = current(app)
        tracking_result = _recover_dispatched_tracking_pending(app)
        result["tracking_recovery"] = tracking_result
        result["tracking_refreshes"] = tracking_result.get(
            "tracking_refreshes", 0
        )
        result["tracking_recovery_failed"] = tracking_result.get(
            "failed", 0
        )
        return result

    aligned_recovery._bt38_mcf_tracking_startup_aligned = True
    runtime._recover_mcf_auto_release_events = aligned_recovery
    return True


install_mcf_tracking_startup_alignment()
