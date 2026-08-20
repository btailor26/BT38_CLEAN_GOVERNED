"""Keep dispatched MCF orders refreshing until Amazon tracking is fully applied.

This alignment reuses BT38's existing governed runtime queue and the existing
Amazon MCF/eBay CompleteSale paths. It does not create a new worker, scheduler,
marketplace scan, stock authority, or write path.

Commercial lifecycle:
- source marketplace is dispatched once the existing MCF release rule allows it
- the exact MCF order is then re-queried from Amazon every 15 minutes
- every unique Amazon package tracking number is durably retained
- any tracking set not yet forwarded is sent to eBay in one CompleteSale call
- while Amazon still reports a non-terminal MCF status, the exact order remains
  queued so a later second/third package cannot be missed
- a Fly sleep/restart reconstructs only recent exact dispatched MCF orders
"""
from __future__ import annotations

from datetime import datetime, timedelta


_CANCELLED = {
    "cancelled",
    "canceled",
    "cancellation",
    "cancel_requested",
}
_TERMINAL_AMAZON = {
    "COMPLETE",
    "COMPLETE_PARTIALLED",
    "CANCELLED",
    "INVALID",
}
_TRACKING_RETRY_SECONDS = 15 * 60
_TRACKING_RECOVERY_HOURS = 24 * 7


def _source_lines_for_mcf(mcf_order_id: int):
    from models import MarketplaceOrder

    return (
        MarketplaceOrder.query
        .filter(MarketplaceOrder.mcf_order_id == int(mcf_order_id))
        .order_by(MarketplaceOrder.id)
        .all()
    )


def _tracking_enrichment_complete(lines, mcf) -> bool:
    """A processing MCF stays open because Amazon may add more packages later."""
    from services.governed_mcf_tracking import has_unforwarded_tracking

    if not lines or mcf is None:
        return False

    for line in lines:
        status = str(getattr(line, "status", "") or "").strip().lower()
        tracking = str(
            getattr(line, "tracking_number", "") or ""
        ).strip()
        if status != "mcf_tracking_updated" or not tracking:
            return False

    if has_unforwarded_tracking(mcf.id):
        return False

    amazon_status = str(mcf.amazon_status or "").strip().upper()
    return amazon_status in _TERMINAL_AMAZON


def _queue_tracking_refresh(
    mcf,
    lines,
    *,
    delay_seconds: int = 0,
    source: str = "mcf_tracking_auto_refresh",
) -> dict:
    """Queue one exact MCF tracking refresh on the existing governed event loop."""
    from services.governed_runtime_engine import notify_governed_runtime_work

    if mcf is None or not lines:
        return {"queued": False, "reason": "mcf_or_source_lines_missing"}

    anchor = lines[0]
    seller_id = str(mcf.seller_fulfillment_order_id or "").strip()
    if not seller_id:
        return {"queued": False, "reason": "seller_fulfillment_order_id_missing"}

    verify_after = datetime.utcnow() + timedelta(
        seconds=max(0, int(delay_seconds or 0))
    )
    return notify_governed_runtime_work(
        source=source,
        event={
            "event_type": "mcf_tracking_refresh",
            "marketplace": str(
                getattr(getattr(anchor, "store", None), "platform", "")
                or "ebay"
            ).strip().lower(),
            "store_id": anchor.store_id,
            "order_id": anchor.marketplace_order_id,
            "warehouse_stock_id": getattr(
                anchor,
                "warehouse_stock_id",
                None,
            ),
            "verify_after": verify_after,
            "payload": {
                "marketplace_order_row_id": anchor.id,
                "mcf_order_id": mcf.id,
                "seller_fulfillment_order_id": seller_id,
                "tracking_auto_refresh": True,
            },
        },
    )


def _refresh_and_enrich_exact_mcf(mcf_order_id: int) -> dict:
    """Refresh one dispatched MCF and forward the complete current tracking set."""
    from extensions import db
    from models import MCFOrder
    from services.governed_ebay_dispatch import complete_sale
    from services.governed_mcf_execution import refresh_mcf_status
    from services.governed_mcf_tracking import (
        has_unforwarded_tracking,
        load_tracking_details,
        mark_tracking_forwarded,
    )
    from services.runtime_action_guard import is_runtime_action_allowed

    mcf = db.session.get(MCFOrder, int(mcf_order_id))
    if mcf is None:
        return {
            "success": False,
            "verified": False,
            "retry": False,
            "reason": "mcf_order_missing",
            "mcf_order_id": int(mcf_order_id),
        }

    created_at = getattr(mcf, "created_at", None)
    if (
        created_at is not None
        and created_at < datetime.utcnow() - timedelta(hours=_TRACKING_RECOVERY_HOURS)
    ):
        return {
            "success": True,
            "verified": True,
            "retry": False,
            "reason": "mcf_tracking_recovery_horizon_expired",
            "mcf_order_id": mcf.id,
        }

    local_status = str(mcf.status or "").strip().lower()
    amazon_before = str(mcf.amazon_status or "").strip().upper()
    if local_status in {"cancelled", "failed"} or amazon_before in {
        "CANCELLED",
        "INVALID",
    }:
        return {
            "success": True,
            "verified": True,
            "retry": False,
            "reason": "mcf_not_trackable",
            "mcf_order_id": mcf.id,
        }

    lines = _source_lines_for_mcf(mcf.id)
    if not lines:
        return {
            "success": False,
            "verified": False,
            "retry": False,
            "reason": "mcf_source_marketplace_order_missing",
            "mcf_order_id": mcf.id,
        }

    if any(
        str(line.status or "").strip().lower() in _CANCELLED
        for line in lines
    ):
        return {
            "success": True,
            "verified": True,
            "retry": False,
            "reason": "source_order_cancelled",
            "mcf_order_id": mcf.id,
        }

    # Tracking enrichment only starts after the source marketplace has already
    # been dispatched. At that point the one-hour cancellation gate is over and
    # must never be restarted by a later Amazon status timestamp refresh.
    if not any(getattr(line, "shipped_at", None) for line in lines):
        return {
            "success": True,
            "verified": True,
            "retry": True,
            "reason": "source_marketplace_dispatch_pending",
            "mcf_order_id": mcf.id,
        }

    refreshed, refresh_result = refresh_mcf_status(mcf)
    if not refreshed:
        return {
            "success": False,
            "verified": True,
            "retry": True,
            "reason": "amazon_mcf_status_refresh_failed",
            "mcf_order_id": mcf.id,
            "error": refresh_result.get("error"),
        }

    tracking_details = (
        refresh_result.get("tracking_details")
        or load_tracking_details(mcf.id)
    )
    tracking_numbers = [
        str(item.get("tracking_number") or "").strip()
        for item in tracking_details
        if str(item.get("tracking_number") or "").strip()
    ]

    enriched = False
    if tracking_details and has_unforwarded_tracking(mcf.id):
        anchor = lines[0]
        store = getattr(anchor, "store", None)
        if store is None or "ebay" not in str(store.platform or "").lower():
            return {
                "success": True,
                "verified": True,
                "retry": False,
                "reason": "mcf_source_marketplace_tracking_not_supported",
                "mcf_order_id": mcf.id,
                "tracking_numbers": tracking_numbers,
            }

        guard = is_runtime_action_allowed(
            store,
            "push",
            manual=False,
            context={
                "actor_user": None,
                "context": "mcf_tracking_auto_refresh_enrichment",
            },
        )
        if not guard.get("allowed"):
            return {
                "success": False,
                "verified": True,
                "retry": True,
                "reason": "mcf_tracking_ebay_enrichment_blocked",
                "mcf_order_id": mcf.id,
                "tracking_numbers": tracking_numbers,
                "error": guard.get("reason"),
            }

        primary = tracking_details[0]
        dispatch = complete_sale(
            anchor,
            carrier=(
                primary.get("carrier")
                or mcf.carrier
                or "Other"
            ),
            tracking_number=(
                primary.get("tracking_number")
                or mcf.tracking_number
            ),
            tracking_details=tracking_details,
        )
        if not dispatch.get("success"):
            now = datetime.utcnow()
            for line in lines:
                line.status = "mcf_tracking_update_failed"
                line.error_message = dispatch.get("error")
                line.updated_at = now
            db.session.commit()
            return {
                "success": False,
                "verified": True,
                "retry": True,
                "reason": "mcf_tracking_ebay_update_failed",
                "mcf_order_id": mcf.id,
                "tracking_numbers": tracking_numbers,
                "error": dispatch.get("error"),
            }

        now = datetime.utcnow()
        for line in lines:
            line.carrier = primary.get("carrier") or mcf.carrier
            line.tracking_number = (
                primary.get("tracking_number")
                or mcf.tracking_number
            )
            line.shipped_at = line.shipped_at or anchor.shipped_at or now
            line.status = "mcf_tracking_updated"
            line.error_message = None
            line.updated_at = now

        mark_tracking_forwarded(mcf.id)
        db.session.commit()
        enriched = True

    amazon_status = str(mcf.amazon_status or "").strip().upper()
    terminal = amazon_status in _TERMINAL_AMAZON

    # Even after one tracking number is forwarded, keep refreshing while Amazon
    # is still Processing/Planning/Received. Split MCF packages can receive a
    # second tracking number later without Amazon emitting another notification.
    retry = not terminal

    return {
        "success": True,
        "verified": True,
        "aligned": enriched,
        "retry": retry,
        "reason": (
            "mcf_tracking_enriched_all_packages"
            if enriched
            else "amazon_mcf_tracking_not_available_yet"
            if not tracking_details
            else "mcf_tracking_current_set_already_forwarded"
        ),
        "mcf_order_id": mcf.id,
        "amazon_status": mcf.amazon_status,
        "carrier": mcf.carrier,
        "tracking_number": mcf.tracking_number,
        "tracking_numbers": tracking_numbers,
        "tracking_count": len(tracking_numbers),
        "terminal": terminal,
    }


def _execute_tracking_refresh_event(event) -> dict:
    payload = dict(event.get("payload") or {})
    mcf_order_id = payload.get("mcf_order_id")
    try:
        mcf_order_id = int(mcf_order_id)
    except (TypeError, ValueError):
        return {
            "success": False,
            "verified": False,
            "aligned": False,
            "reason": "mcf_tracking_event_identity_missing",
            "retry": False,
        }

    result = _refresh_and_enrich_exact_mcf(mcf_order_id)
    result["event_type"] = "mcf_tracking_refresh"
    result["source"] = event.get("source")

    if result.get("retry"):
        from extensions import db
        from models import MCFOrder

        mcf = db.session.get(MCFOrder, mcf_order_id)
        lines = _source_lines_for_mcf(mcf_order_id)
        if mcf is not None and lines:
            result["next_refresh"] = _queue_tracking_refresh(
                mcf,
                lines,
                delay_seconds=_TRACKING_RETRY_SECONDS,
            )

    return result


def _recover_dispatched_tracking_pending(app) -> dict:
    """Rebuild exact recurring tracking events after a Fly restart."""
    from models import MarketplaceOrder
    from services.governed_mcf_tracking import has_unforwarded_tracking

    recovery_since = datetime.utcnow() - timedelta(
        hours=_TRACKING_RECOVERY_HOURS
    )

    queued = 0
    completed = 0
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
            mcf_id = int(row.mcf_order_id)
            if mcf_id in seen:
                continue
            seen.add(mcf_id)
            examined_orders += 1

            lines = _source_lines_for_mcf(mcf_id)
            mcf = next(
                (
                    line.mcf_order
                    for line in lines
                    if line.mcf_order is not None
                ),
                None,
            )
            if mcf is None:
                skipped += 1
                continue

            if any(
                str(line.status or "").strip().lower() in _CANCELLED
                for line in lines
            ):
                skipped += 1
                continue

            local_status = str(mcf.status or "").strip().lower()
            amazon_status = str(mcf.amazon_status or "").strip().upper()
            if local_status in {"cancelled", "failed"} or amazon_status in {
                "CANCELLED",
                "INVALID",
            }:
                skipped += 1
                continue

            # A scalar source tracking value does not prove the full Amazon
            # package set has been forwarded. Durable tracking state is the
            # authority for whether a newly discovered second package is pending.
            if _tracking_enrichment_complete(lines, mcf):
                completed += 1
                continue

            # Processing MCF orders remain live even when the currently known
            # tracking set has already been forwarded, because Amazon may split
            # the order and publish another package later.
            if (
                amazon_status not in _TERMINAL_AMAZON
                or has_unforwarded_tracking(mcf.id)
            ):
                _queue_tracking_refresh(
                    mcf,
                    lines,
                    delay_seconds=0,
                    source="mcf_tracking_startup_recovery",
                )
                queued += 1
            else:
                completed += 1

    return {
        "success": True,
        "governed": True,
        "bounded": True,
        "recovery_hours": _TRACKING_RECOVERY_HOURS,
        "rows_examined_max": 250,
        "orders_examined": examined_orders,
        "tracking_refreshes_queued": queued,
        "already_complete": completed,
        "skipped": skipped,
        "full_scan_started": False,
        "marketplace_import_started": False,
        "warehouse_scan_started": False,
        "new_worker_started": False,
        "new_scheduler_started": False,
    }


def install_mcf_tracking_startup_alignment() -> bool:
    """Attach recurring exact MCF tracking to the existing governed runtime."""
    import services.governed_runtime_engine as runtime
    import services.governed_mcf_execution as execution

    installed = False

    current_recovery = runtime._recover_mcf_auto_release_events
    if not getattr(
        current_recovery,
        "_bt38_mcf_tracking_startup_aligned",
        False,
    ):
        def aligned_recovery(app):
            result = current_recovery(app)
            tracking_result = _recover_dispatched_tracking_pending(app)
            result["tracking_recovery"] = tracking_result
            result["tracking_refreshes_queued"] = tracking_result.get(
                "tracking_refreshes_queued", 0
            )
            return result

        aligned_recovery._bt38_mcf_tracking_startup_aligned = True
        runtime._recover_mcf_auto_release_events = aligned_recovery
        installed = True

    current_cycle = runtime._run_light_reconcile_cycle
    if not getattr(
        current_cycle,
        "_bt38_mcf_tracking_cycle_aligned",
        False,
    ):
        def aligned_cycle(events=None, source="webhook_verification_15m"):
            events = list(events or [])
            tracking_events = [
                event
                for event in events
                if str(event.get("event_type") or "").strip().lower()
                == "mcf_tracking_refresh"
            ]
            other_events = [
                event
                for event in events
                if str(event.get("event_type") or "").strip().lower()
                != "mcf_tracking_refresh"
            ]

            result = current_cycle(
                events=other_events,
                source=source,
            )
            tracking_results = [
                _execute_tracking_refresh_event(event)
                for event in tracking_events
            ]

            # When the existing one-hour MCF auto-release event dispatches the
            # source marketplace, immediately start the recurring tracking phase.
            for event in other_events:
                if str(event.get("event_type") or "").strip().lower() != "mcf_auto_release":
                    continue
                payload = dict(event.get("payload") or {})
                mcf_id = payload.get("mcf_order_id")
                try:
                    mcf_id = int(mcf_id)
                except (TypeError, ValueError):
                    continue
                lines = _source_lines_for_mcf(mcf_id)
                mcf = next(
                    (
                        line.mcf_order
                        for line in lines
                        if line.mcf_order is not None
                    ),
                    None,
                )
                if (
                    mcf is not None
                    and any(getattr(line, "shipped_at", None) for line in lines)
                ):
                    _queue_tracking_refresh(
                        mcf,
                        lines,
                        delay_seconds=_TRACKING_RETRY_SECONDS,
                    )

            combined = list(result.get("results") or []) + tracking_results
            result["results"] = combined
            result["events_received"] = len(events)
            result["events_verified"] = sum(
                1 for item in combined if item.get("verified")
            )
            result["events_aligned"] = sum(
                1 for item in combined if item.get("aligned")
            )
            result["mcf_tracking_events"] = len(tracking_events)
            runtime._last_verification_result = result
            return result

        aligned_cycle._bt38_mcf_tracking_cycle_aligned = True
        runtime._run_light_reconcile_cycle = aligned_cycle
        installed = True

    current_signal = execution.refresh_mcf_from_amazon_signal
    if not getattr(
        current_signal,
        "_bt38_mcf_tracking_signal_aligned",
        False,
    ):
        def aligned_signal(payload: dict):
            result = current_signal(payload)
            mcf_id = result.get("mcf_order_id")
            try:
                mcf_id = int(mcf_id)
            except (TypeError, ValueError):
                return result

            lines = _source_lines_for_mcf(mcf_id)
            mcf = next(
                (
                    line.mcf_order
                    for line in lines
                    if line.mcf_order is not None
                ),
                None,
            )
            if (
                mcf is not None
                and any(getattr(line, "shipped_at", None) for line in lines)
            ):
                # If the original signal path was blocked by its refreshed
                # one-hour timestamp, immediately use the dispatched-order path;
                # shipped_at proves that cancellation window has already ended.
                if result.get("reason") == "tracking_received_inside_one_hour_cancellation_window":
                    result = _refresh_and_enrich_exact_mcf(mcf_id)

                amazon_status = str(mcf.amazon_status or "").strip().upper()
                if amazon_status not in _TERMINAL_AMAZON:
                    result["tracking_auto_refresh"] = _queue_tracking_refresh(
                        mcf,
                        lines,
                        delay_seconds=_TRACKING_RETRY_SECONDS,
                    )

            return result

        aligned_signal._bt38_mcf_tracking_signal_aligned = True
        execution.refresh_mcf_from_amazon_signal = aligned_signal
        installed = True

    return installed


install_mcf_tracking_startup_alignment()
