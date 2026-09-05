"""Immediate exact recovery after a governed webhook.

Contract:
- Failed webhooks recover only the durable notification that failed.
- Recovery checks canonical MarketplaceOrder first.
- Existing orders are never replayed, preventing duplicate rows and duplicate
  stock mutation.
- Missing orders replay only the captured exact notification except shipment
  lifecycle events, which never recreate a sale/order.
- A restart performs one bounded DB-only selector for FAILED or stranded
  PROCESSING webhook IDs received within the last 24 hours.
- Successful/completed marketplace lifecycle notifications are never replayed
  by startup recovery and never handed to recovery merely because they carry a
  shipped/delivered state.
- eBay ORDER_CONFIRMATION failures that were durably captured are handed to
  this exact recovery path before BT38 returns HTTP 200 to eBay.
- No recent-order scan, completed-order repair, FBA settlement replay,
  Warehouse sync scan, scheduler, polling loop, or marketplace-wide recovery
  is started.
"""
from __future__ import annotations

import threading
from typing import Any

from flask import g, jsonify, request
from sqlalchemy import text

from app import app


_WEBHOOK_PATHS = {
    "/governed/webhooks/amazon": "amazon",
    "/governed/webhooks/ebay": "ebay",
}

_recovery_lock = threading.Lock()
_recovery_running = False
_pending_notifications: set[tuple[str, int]] = set()
_startup_recovery_checked = False


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


def _deep_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        for current_key, nested in value.items():
            if str(current_key).lower() == str(key).lower() and nested not in (None, ""):
                return nested
            found = _deep_get(nested, key)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _deep_get(nested, key)
            if found not in (None, ""):
                return found
    return None


def _status_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _ebay_request_topic() -> str:
    """Return the exact eBay topic without normalising one topic into another."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return ""

    topic = payload.get("topic")
    if topic in (None, ""):
        notification = payload.get("notification")
        if isinstance(notification, dict):
            topic = notification.get("topic")
    if topic in (None, ""):
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            topic = metadata.get("topic")

    return str(topic or "").strip().upper()


def _notification_record_id_from_response(response) -> int | None:
    notification_record_id = getattr(g, "bt38_notification_record_id", None)
    if notification_record_id is None:
        payload = response.get_json(silent=True)
        if isinstance(payload, dict):
            notification_record_id = payload.get("notification_record_id")
    try:
        value = int(notification_record_id)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


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
                            "recovered=%s duplicate_skipped=%s "
                            "dispatch_lifecycle=%s fba_verified=%s",
                            platform,
                            notification_record_id,
                            result.get("order_id"),
                            result.get("recovered"),
                            result.get("duplicate_skipped"),
                            result.get("dispatch_lifecycle"),
                            bool(result.get("fba_verification")),
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


def _queue_stranded_durable_notifications(limit: int = 25) -> int:
    """Queue only exact failed/stranded notifications from the last 24 hours."""
    from extensions import db

    selected: list[tuple[str, int]] = []

    for platform, table_name in (
        ("amazon", "webhooks.amazon_notifications"),
        ("ebay", "webhooks.ebay_notifications"),
    ):
        rows = db.session.execute(
            text(
                f"""
                SELECT id
                FROM {table_name}
                WHERE received_at >= NOW() - INTERVAL '24 hours'
                  AND (
                        processing_status = 'FAILED'
                        OR (
                            processing_status = 'PROCESSING'
                            AND received_at <= NOW() - INTERVAL '2 minutes'
                        )
                  )
                ORDER BY id ASC
                LIMIT :limit
                """
            ),
            {"limit": int(limit)},
        ).scalars().all()
        selected.extend((platform, int(row_id)) for row_id in rows)

    queued = 0
    for platform, notification_record_id in sorted(set(selected)):
        if request_rejected_webhook_recovery(
            platform,
            notification_record_id,
        ):
            queued += 1
    return queued


@app.before_request
def recover_stranded_webhooks_once_after_restart():
    """One bounded 24-hour failed-only DB safety pass after process restart."""
    global _startup_recovery_checked

    if _startup_recovery_checked:
        return None

    with _recovery_lock:
        if _startup_recovery_checked:
            return None
        _startup_recovery_checked = True

    try:
        queued = _queue_stranded_durable_notifications(limit=25)
        if queued:
            app.logger.warning(
                "BT38 queued %s failed/stranded exact webhook recoveries after restart",
                queued,
            )
    except Exception:
        from extensions import db

        db.session.rollback()
        app.logger.exception(
            "BT38 failed/stranded exact webhook recovery selector failed"
        )

    return None


@app.post("/governed/actions/ebay/exact-order-recovery")
def recover_exact_ebay_order_manually():
    """Refresh marketplace-owned truth for one existing eBay order only."""
    import hmac
    import os
    import re

    from flask_login import current_user
    from extensions import db
    from models import MarketplaceOrder, Store
    from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

    configured_task_key = str(os.environ.get("TASK_API_KEY") or "")
    supplied_task_key = str(request.headers.get("X-Task-Key") or "")
    session_authorized = bool(getattr(current_user, "is_authenticated", False))
    task_authorized = bool(
        configured_task_key
        and supplied_task_key
        and hmac.compare_digest(configured_task_key, supplied_task_key)
    )
    if not (session_authorized or task_authorized):
        return jsonify({
            "success": False,
            "ok": False,
            "governed": True,
            "reason": "authentication_required",
            "marketplace_write_started": False,
        }), 401

    payload = request.get_json(silent=True) or {}
    try:
        store_id = int(payload.get("store_id"))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "ok": False,
            "governed": True,
            "reason": "invalid_store_id",
            "marketplace_write_started": False,
        }), 400

    order_id = str(payload.get("marketplace_order_id") or "").strip()
    if store_id <= 0 or not re.fullmatch(r"\d+-\d+-\d+", order_id):
        return jsonify({
            "success": False,
            "ok": False,
            "governed": True,
            "reason": "invalid_exact_ebay_order_identity",
            "marketplace_write_started": False,
        }), 400

    store = db.session.get(Store, store_id)
    if (
        store is None
        or not bool(getattr(store, "is_active", False))
        or "ebay" not in str(getattr(store, "platform", "") or "").lower()
    ):
        return jsonify({
            "success": False,
            "ok": False,
            "governed": True,
            "reason": "active_ebay_store_not_found",
            "store_id": store_id,
            "marketplace_write_started": False,
        }), 404

    existing = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == store_id,
            MarketplaceOrder.marketplace_order_id == order_id,
        )
        .first()
    )
    if existing is None:
        return jsonify({
            "success": False,
            "ok": False,
            "governed": True,
            "reason": "existing_marketplace_order_missing",
            "store_id": store_id,
            "order_id": order_id,
            "marketplace_write_started": False,
        }), 404

    try:
        result = hydrate_exact_ebay_order(
            store=store,
            marketplace_order_id=order_id,
            source="manual_exact_ebay_recovery",
        )
    except Exception as exc:
        db.session.rollback()
        app.logger.exception(
            "BT38 manual exact eBay recovery failed store_id=%s order_id=%s",
            store_id,
            order_id,
        )
        return jsonify({
            "success": False,
            "ok": False,
            "governed": True,
            "reason": "exact_ebay_recovery_exception",
            "error": str(exc)[:500],
            "store_id": store_id,
            "order_id": order_id,
            "exact_order_only": True,
            "broad_scan_started": False,
            "order_replayed": False,
            "stock_mutation_started": False,
            "marketplace_write_started": False,
        }), 502

    db.session.expire_all()
    rows = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == store_id,
            MarketplaceOrder.marketplace_order_id == order_id,
        )
        .order_by(MarketplaceOrder.id)
        .all()
    )
    readback = [
        {
            "id": int(row.id),
            "status": row.status,
            "carrier": row.carrier,
            "tracking_number": row.tracking_number,
            "shipped_at": row.shipped_at.isoformat() if row.shipped_at else None,
            "import_source": getattr(row, "import_source", None),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in rows
    ]

    return jsonify({
        "success": bool(result.get("success")),
        "ok": bool(result.get("success")),
        "governed": True,
        "exact_order_only": True,
        "broad_scan_started": False,
        "order_replayed": False,
        "stock_mutation_started": False,
        "marketplace_write_started": False,
        "store_id": store_id,
        "order_id": order_id,
        "hydration": result,
        "database_readback": readback,
    }), 200


@app.after_request
def recover_when_marketplace_webhook_is_rejected(response):
    path = request.path.rstrip("/") or "/"
    platform = _WEBHOOK_PATHS.get(path)

    if request.method != "POST" or platform is None:
        return response

    notification_record_id = _notification_record_id_from_response(response)
    failed = _response_failed(response)

    # Recovery is failure-only. Successful shipment/lifecycle notifications are
    # normal governed events and must never be reprocessed as recovery work.
    if not failed:
        return response

    scheduled = False
    if not getattr(g, "bt38_rejected_webhook_recovery_requested", False):
        scheduled = request_rejected_webhook_recovery(
            platform,
            notification_record_id,
        )
        if scheduled:
            g.bt38_rejected_webhook_recovery_requested = True
    else:
        scheduled = True

    # eBay must not retry/mark down an ORDER_CONFIRMATION that BT38 has already
    # captured durably and handed to its existing exact recovery path. This is
    # deliberately topic-specific: LISTING and every other webhook retain their
    # existing response semantics. If capture failed or recovery could not be
    # scheduled, preserve the original failure so eBay can retry it.
    if (
        scheduled
        and platform == "ebay"
        and _ebay_request_topic() == "ORDER_CONFIRMATION"
    ):
        response.status_code = 200
        response.headers["X-BT38-Exact-Recovery"] = "scheduled"

    return response
