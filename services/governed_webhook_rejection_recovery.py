"""Immediate exact recovery after a failed governed webhook.

Contract:
- Successful webhooks do nothing here.
- Failed webhooks recover only the durable notification that failed.
- Recovery checks canonical MarketplaceOrder first.
- Existing orders are skipped before replay, preventing duplicate rows and
  duplicate stock mutation.
- Missing orders replay only the captured exact notification through the
  existing governed webhook executor.
- No recent-order scan, Warehouse sync scan, scheduler, polling loop, or
  marketplace-wide recovery is started.
"""
from __future__ import annotations

import threading

from flask import g, request

from app import app


_WEBHOOK_PATHS = {
    "/governed/webhooks/amazon": "amazon",
    "/governed/webhooks/ebay": "ebay",
}

_recovery_lock = threading.Lock()
_recovery_running = False
_pending_notifications: set[tuple[str, int]] = set()


def _response_failed(response) -> bool:
    if int(getattr(response, "status_code", 200) or 200) >= 400:
        return True

    payload = response.get_json(silent=True)
    if not isinstance(payload, dict):
        return False

    if payload.get("success") is False or payload.get("ok") is False:
        return True

    status = str(payload.get("status") or "").strip().lower()
    return status in {
        "processing_failed",
        "verification_failed",
        "rejected",
        "invalid",
        "failed",
        "order_import_failed",
    }


def _run_pending_recoveries():
    global _recovery_running

    try:
        while True:
            with _recovery_lock:
                if not _pending_notifications:
                    _recovery_running = False
                    return
                platform, notification_record_id = sorted(
                    _pending_notifications
                )[0]
                _pending_notifications.discard(
                    (platform, notification_record_id)
                )

            try:
                with app.app_context():
                    from services.governed_exact_webhook_recovery import (
                        recover_exact_failed_webhook,
                    )
                    from services.governed_webhook_capture import (
                        mark_notification_status,
                    )

                    result = recover_exact_failed_webhook(
                        platform,
                        notification_record_id,
                    )

                    if result.get("success"):
                        # Safe because exact recovery has already proven the
                        # canonical order exists (or existed before replay).
                        mark_notification_status(
                            platform,
                            notification_record_id,
                            processing_status="COMPLETED",
                            last_error="",
                            completed=True,
                        )
                        app.logger.warning(
                            "BT38 exact webhook recovery platform=%s "
                            "notification_record_id=%s order_id=%s "
                            "recovered=%s duplicate_skipped=%s",
                            platform,
                            notification_record_id,
                            result.get("order_id"),
                            result.get("recovered"),
                            result.get("duplicate_skipped"),
                        )
                    else:
                        app.logger.error(
                            "BT38 exact webhook recovery failed platform=%s "
                            "notification_record_id=%s result=%s",
                            platform,
                            notification_record_id,
                            result,
                        )
            except Exception:
                app.logger.exception(
                    "BT38 exact webhook recovery crashed platform=%s "
                    "notification_record_id=%s",
                    platform,
                    notification_record_id,
                )
    finally:
        with _recovery_lock:
            _recovery_running = False


def request_rejected_webhook_recovery(
    platform: str,
    notification_record_id: int | None = None,
) -> bool:
    """Schedule exact recovery for one durable failed notification."""
    global _recovery_running

    platform = str(platform or "").strip().lower()
    if platform not in {"amazon", "ebay"}:
        return False

    try:
        notification_record_id = int(notification_record_id)
    except (TypeError, ValueError):
        return False

    if notification_record_id <= 0:
        return False

    with _recovery_lock:
        _pending_notifications.add(
            (platform, notification_record_id)
        )
        if _recovery_running:
            return True
        _recovery_running = True

    thread = threading.Thread(
        target=_run_pending_recoveries,
        daemon=True,
        name="BT38ExactWebhookRecovery",
    )
    thread.start()
    return True


@app.after_request
def recover_when_marketplace_webhook_is_rejected(response):
    path = request.path.rstrip("/") or "/"
    platform = _WEBHOOK_PATHS.get(path)

    if request.method != "POST" or platform is None:
        return response

    if not _response_failed(response):
        return response

    notification_record_id = getattr(
        g,
        "bt38_notification_record_id",
        None,
    )
    if notification_record_id is None:
        payload = response.get_json(silent=True)
        if isinstance(payload, dict):
            notification_record_id = payload.get(
                "notification_record_id"
            )

    # Request-local guard prevents duplicate scheduling from multiple hooks.
    if not getattr(g, "bt38_rejected_webhook_recovery_requested", False):
        scheduled = request_rejected_webhook_recovery(
            platform,
            notification_record_id,
        )
        if scheduled:
            g.bt38_rejected_webhook_recovery_requested = True

    return response
