"""Low-DB event bootstrap for the existing governed runtime engine.

This module does not create a second execution authority. It reuses the
existing governed event queue, Amazon SQS intake, exact-event verification and
runtime status/lock. The loop remains event-driven, with one narrow safety net
for eBay listings that were missed because no usable listing webhook arrived:

- one bounded startup recovery re-arms unfinished exact MCF release events;
- no automatic full marketplace hydration;
- no periodic DB heartbeat or broad reconcile;
- SQS long-polling is allowed because it does not query Neon;
- an exact queued event may wake at its exact due time;
- once at worker start and then at most every eight hours, inspect only eBay's
  newest Item IDs and import only Item IDs absent from MarketplaceListing;
- after exact work or the bounded missed-listing check, return to sleep.

Manual/explicit recovery and hydration functions remain available through their
existing governed command paths.
"""
from __future__ import annotations

import os
import time


MISSED_EBAY_LISTING_RECOVERY_SECONDS = 8 * 60 * 60


def _install_mcf_dispatch_tracking_handoff() -> bool:
    """Forward already-known Amazon tracking immediately after MCF dispatch."""
    import governed_mcf_routes as mcf_routes

    current_dispatch = mcf_routes.run_governed_mcf_marketplace_dispatch
    if getattr(current_dispatch, "_bt38_mcf_tracking_handoff", False):
        return False

    def aligned_dispatch(*args, **kwargs):
        result = current_dispatch(*args, **kwargs)
        if not result.get("success") or result.get("skipped"):
            return result

        # The source marketplace row has just committed. Wake the existing
        # signal-only browser channel once so Orders / MCF can reread current
        # committed state. This is in-memory only: no browser polling, no Neon
        # polling, no timer and no additional marketplace call.
        from services.governed_ui_event_signal import publish_governed_ui_event

        publish_governed_ui_event(
            source="mcf_source_dispatch",
            scope={
                "event_type": "mcf_source_dispatched",
            },
        )

        mcf_order_id = result.get("mcf_order_id")
        if not mcf_order_id:
            return result

        from services.governed_mcf_tracking import has_unforwarded_tracking

        if not has_unforwarded_tracking(int(mcf_order_id)):
            return result

        # Tracking may have arrived while the one-hour source-marketplace
        # dispatch was still pending. Reuse the existing exact enrichment path
        # immediately once dispatch succeeds; do not poll or schedule a retry.
        from services.governed_mcf_tracking_startup_alignment import (
            _forward_current_tracking_set,
        )

        enrichment = _forward_current_tracking_set(
            int(mcf_order_id),
            source="mcf_source_dispatch_tracking_handoff",
        )
        result["tracking_enrichment"] = enrichment
        if enrichment.get("success") and not enrichment.get("skipped"):
            result["status"] = "mcf_tracking_updated"
            result["tracking_pending"] = False
        return result

    aligned_dispatch._bt38_mcf_tracking_handoff = True
    mcf_routes.run_governed_mcf_marketplace_dispatch = aligned_dispatch
    return True


def _event_only_engine_loop(app):
    import services.governed_runtime_engine as runtime
    from services.governed_ebay_missed_listing_recovery import (
        recover_missed_ebay_listings,
    )

    runtime._safe_log(
        "Low-DB event runtime started with bounded eBay missed-listing recovery"
    )

    # Restore the existing restart-safe MCF lifecycle contract. The exact
    # recovery function is owned by governed_runtime_engine and only re-arms
    # unfinished MCF release events from persisted MarketplaceOrder state.
    try:
        recovery_result = runtime._recover_mcf_auto_release_events(app)
        runtime._safe_log(
            "MCF startup recovery complete "
            f"queued={recovery_result.get('orders_queued', 0)} "
            f"skipped={recovery_result.get('orders_skipped', 0)}"
        )
    except Exception as exc:
        runtime._safe_error("MCF startup recovery failed", exc)

    # Run one bounded check after each worker start so a listing missed while
    # the process was down is recovered promptly. Thereafter the in-memory
    # deadline is eight hours; no DB heartbeat or persistent scheduler row is
    # used merely to remember this deadline.
    next_missed_listing_recovery = time.monotonic()

    while not runtime._stop_event.is_set():
        try:
            runtime._pending_notification_event.clear()

            seconds_until_event = runtime._seconds_until_next_due(
                default=60 * 60,
            )
            seconds_until_recovery = max(
                0.0,
                next_missed_listing_recovery - time.monotonic(),
            )
            seconds_until_due = min(
                float(seconds_until_event),
                seconds_until_recovery,
            )

            if runtime._amazon_sqs_consumer_enabled():
                # Amazon SQS long polling is the sleeping notification transport;
                # it does not touch Neon when the queue is empty.
                sqs_wait = max(
                    0,
                    min(20, int(seconds_until_due)),
                )
                runtime._poll_amazon_sqs_once(
                    app,
                    wait_seconds=sqs_wait,
                )
            else:
                runtime._pending_notification_event.wait(
                    timeout=seconds_until_due,
                )

            if runtime._stop_event.is_set():
                break

            due_events = runtime._pop_due_events()
            if due_events:
                with app.app_context():
                    runtime._run_light_reconcile_cycle(
                        events=due_events,
                        source="governed_exact_event",
                    )

            if time.monotonic() >= next_missed_listing_recovery:
                # Advance the deadline before calling the marketplace so a
                # failure cannot turn into a tight retry loop and increase DB/API
                # usage. The next attempt remains eight hours away.
                next_missed_listing_recovery = (
                    time.monotonic()
                    + MISSED_EBAY_LISTING_RECOVERY_SECONDS
                )
                with app.app_context():
                    recovery = recover_missed_ebay_listings()
                    runtime._safe_log(
                        "Bounded eBay missed-listing recovery "
                        f"stores={recovery.get('stores_checked')} "
                        f"missing={recovery.get('missing')} "
                        f"imported={recovery.get('imported')}"
                    )

            # No broad timed work. When there is no exact event and no due
            # eight-hour missed-listing check, control returns directly to sleep.
        except Exception as exc:
            runtime._safe_error("Low-DB event runtime loop error", exc)
            runtime._stop_event.wait(timeout=30)

    runtime._safe_log("Low-DB event runtime stopped")


def start_event_only_runtime(app) -> bool:
    """Start the existing runtime with exact events plus bounded eBay recovery."""
    import services.governed_runtime_engine as runtime

    if runtime.get_governed_runtime_status().get("engine_started"):
        return False

    # Keep the source-dispatch and tracking phases event-driven while closing
    # the race where Amazon tracking arrives before the one-hour dispatch.
    _install_mcf_dispatch_tracking_handoff()

    # Replace only the loop policy before invoking the existing owner-lock,
    # status and thread bootstrap. All exact execution helpers remain unchanged.
    runtime._engine_loop = _event_only_engine_loop

    # Keep the old broad 8-hour hydration disabled. The loop above performs only
    # the narrow eBay missing-Item-ID safety check and never invokes the broad
    # marketplace hydration path.
    os.environ["ENABLE_GOVERNED_RUNTIME_ENGINE"] = "true"
    os.environ["ENABLE_GOVERNED_8H_HYDRATION"] = "false"

    started = runtime.start_governed_runtime_engine(app)
    if started:
        runtime._safe_log(
            "Runtime policy active: webhook/SQS + exact queued events; "
            "broad hydration disabled; bounded eBay missed-listing recovery enabled"
        )
    return started
