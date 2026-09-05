"""Event-only bootstrap for the existing governed runtime engine.

This module reuses the existing governed event queue, Amazon SQS intake,
exact-event verification and runtime owner/lock. It does not run startup,
periodic, or deployment-style recovery scans.

Runtime rule:
- marketplace/webhook/SQS event arrives;
- exact governed work runs;
- any exact queued due event runs at its due time;
- otherwise the runtime sleeps;
- no Neon heartbeat, no startup DB recovery, no periodic DB recovery, no broad
  marketplace hydration.

Manual/explicit recovery helpers remain available through their existing
governed command paths but are not invoked automatically by this runtime loop.
"""
from __future__ import annotations

import os


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

        from services.governed_ui_event_signal import publish_governed_ui_event

        publish_governed_ui_event(
            source="mcf_source_dispatch",
            scope={"event_type": "mcf_source_dispatched"},
        )

        mcf_order_id = result.get("mcf_order_id")
        if not mcf_order_id:
            return result

        from services.governed_mcf_tracking import has_unforwarded_tracking

        if not has_unforwarded_tracking(int(mcf_order_id)):
            return result

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

    runtime._safe_log(
        "Event-only runtime started: webhook/SQS/exact due events only; automatic recovery disabled"
    )

    while not runtime._stop_event.is_set():
        try:
            runtime._pending_notification_event.clear()
            seconds_until_due = float(
                runtime._seconds_until_next_due(default=60 * 60)
            )

            if runtime._amazon_sqs_consumer_enabled():
                # Amazon SQS long-polling is the sleeping transport and does not
                # query Neon while the queue is empty.
                sqs_wait = max(0, min(20, int(seconds_until_due)))
                runtime._poll_amazon_sqs_once(app, wait_seconds=sqs_wait)
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

            # No timed recovery work. No event means return directly to sleep.
        except Exception as exc:
            runtime._safe_error("Event-only runtime loop error", exc)
            runtime._stop_event.wait(timeout=30)

    runtime._safe_log("Event-only runtime stopped")


def start_event_only_runtime(app) -> bool:
    """Start the existing runtime with webhook/SQS and exact queued events only."""
    import services.governed_runtime_engine as runtime

    if runtime.get_governed_runtime_status().get("engine_started"):
        return False

    _install_mcf_dispatch_tracking_handoff()
    runtime._engine_loop = _event_only_engine_loop

    os.environ["ENABLE_GOVERNED_RUNTIME_ENGINE"] = "true"
    os.environ["ENABLE_GOVERNED_8H_HYDRATION"] = "false"

    started = runtime.start_governed_runtime_engine(app)
    if started:
        runtime._safe_log(
            "Runtime policy active: webhook/SQS + exact queued events only; automatic startup/periodic recovery disabled"
        )
    return started
