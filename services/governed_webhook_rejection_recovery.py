"""One-shot governed recovery after a rejected marketplace webhook.

Contract:
- Successful webhooks do not scan.
- Rejected/failed webhooks trigger one bounded recent-order recovery pass.
- The pass reuses services.governed_warehouse_sync and its existing importer.
- No scheduler, polling loop, persistent worker, or full listing hydration is
  created here.
- Once the one-shot pass finishes, the task exits and the system sleeps again.
"""
from __future__ import annotations

import threading

from flask import g, request

from app import app


_WEBHOOK_PATHS = {
    "/governed/webhooks/amazon": "amazon",
    "/governed/webhooks/ebay": "ebay",
}

_scan_lock = threading.Lock()
_scan_running = False
_pending_platforms: set[str] = set()


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
    }


def _stores_for_platform(platform: str):
    from models import Store

    query = Store.query.filter(
        Store.is_active == True,  # noqa: E712
        Store.store_mode == "live",
    )
    if platform == "amazon":
        query = query.filter(Store.platform.ilike("%amazon%"))
    elif platform == "ebay":
        query = query.filter(Store.platform.ilike("%ebay%"))
    return query.order_by(Store.id).all()


def _run_pending_scans():
    global _scan_running

    try:
        while True:
            with _scan_lock:
                if not _pending_platforms:
                    _scan_running = False
                    return
                platform = sorted(_pending_platforms)[0]
                _pending_platforms.discard(platform)

            try:
                with app.app_context():
                    from services.governed_warehouse_sync import (
                        run_governed_warehouse_sync,
                    )
                    from services.governed_ui_event_signal import (
                        publish_webhook_ui_event,
                    )

                    stores = _stores_for_platform(platform)
                    results = []
                    for store in stores:
                        results.append(
                            run_governed_warehouse_sync(
                                store_id=store.id,
                                actor=f"{platform}_webhook_rejected_recovery",
                                manual=False,
                            )
                        )

                    app.logger.warning(
                        "BT38 rejected webhook recovery scan platform=%s stores=%s results=%s",
                        platform,
                        len(stores),
                        results,
                    )

                    # The recovery importer may have found activity that the
                    # rejected webhook could not deliver. Wake open governed
                    # pages once so they reread BT38 truth after the scan.
                    publish_webhook_ui_event(
                        platform=platform,
                        notification_record_id=0,
                    )
            except Exception:
                app.logger.exception(
                    "BT38 rejected webhook recovery scan failed platform=%s",
                    platform,
                )
    finally:
        with _scan_lock:
            _scan_running = False


def request_rejected_webhook_recovery(platform: str) -> bool:
    """Schedule one coalesced recovery pass and return immediately."""
    global _scan_running

    platform = str(platform or "").strip().lower()
    if platform not in {"amazon", "ebay"}:
        return False

    with _scan_lock:
        _pending_platforms.add(platform)
        if _scan_running:
            return True
        _scan_running = True

    thread = threading.Thread(
        target=_run_pending_scans,
        daemon=True,
        name="BT38RejectedWebhookRecovery",
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

    # Preserve evidence for audit; this flag is request-local only and prevents
    # another after-request hook from scheduling the same recovery twice.
    if not getattr(g, "bt38_rejected_webhook_recovery_requested", False):
        g.bt38_rejected_webhook_recovery_requested = True
        request_rejected_webhook_recovery(platform)

    return response
