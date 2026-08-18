"""Strict event-only bootstrap for the existing governed runtime engine.

This module does not create a second execution authority. It reuses the
existing governed event queue, Amazon SQS intake, exact-event verification and
runtime status/lock. The only change is the engine loop policy:

- no startup MarketplaceOrder/FBA/MCF recovery scans;
- no automatic full marketplace hydration;
- no periodic DB heartbeat or broad reconcile;
- SQS long-polling is allowed because it does not query Neon;
- an exact queued event may wake at its exact due time;
- after the exact event finishes the runtime returns to waiting/sleep.

Manual/explicit recovery and hydration functions remain available through their
existing governed command paths.
"""
from __future__ import annotations

import os
from datetime import datetime


def _event_only_engine_loop(app):
    import services.governed_runtime_engine as runtime

    runtime._safe_log("Strict event-only runtime loop started")

    while not runtime._stop_event.is_set():
        try:
            # The process is idle unless Amazon SQS has an event, an HTTP
            # webhook/request has queued an exact event, or a previously queued
            # exact event reaches its due time. No DB work happens here merely
            # because time has passed.
            runtime._pending_notification_event.clear()
            seconds_until_due = runtime._seconds_until_next_due(
                default=60 * 60,
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

            # No startup recovery, no 8-hour hydration and no broad timed work.
            # When there is no event, control returns directly to the wait above.
        except Exception as exc:
            runtime._safe_error("Event-only engine loop error", exc)
            runtime._stop_event.wait(timeout=30)

    runtime._safe_log("Strict event-only runtime loop stopped")


def start_event_only_runtime(app) -> bool:
    """Start the existing runtime engine with strict event-only loop policy."""
    import services.governed_runtime_engine as runtime

    if runtime.get_governed_runtime_status().get("engine_started"):
        return False

    # Replace only the loop policy before invoking the existing owner-lock,
    # status and thread bootstrap. All exact execution helpers remain unchanged.
    runtime._engine_loop = _event_only_engine_loop

    # app.py is intentionally imported with the engine disabled so it cannot
    # race ahead and perform startup recovery. Enable it only now, after the
    # event-only loop has been installed.
    os.environ["ENABLE_GOVERNED_RUNTIME_ENGINE"] = "true"
    os.environ["ENABLE_GOVERNED_8H_HYDRATION"] = "false"

    started = runtime.start_governed_runtime_engine(app)
    if started:
        runtime._safe_log(
            "Runtime policy active: webhook/SQS + exact queued events only; "
            "startup recovery and automatic hydration disabled"
        )
    return started
