"""Immediate exact recovery after a failed governed webhook.

Contract:
- Successful webhooks normally do nothing here.
- Failed webhooks recover only the durable notification that failed.
- Recovery checks canonical MarketplaceOrder first.
- Existing orders are never replayed, preventing duplicate rows and duplicate
  stock mutation.
- Missing orders replay only the captured exact notification through the
  existing governed webhook executor.
- A restart performs one bounded DB-only selector for failed/stranded webhook
  IDs, legacy completed order orphans, and Amazon FBA order notifications whose
  exact Seller-SKU settlement verification was not completed after 90 seconds.
- FBA settlement recovery re-reads only that Seller SKU from Amazon; it does
  not replay the order or scan marketplace inventory.
- No recent-order scan, Warehouse sync scan, scheduler, polling loop, or
  marketplace-wide recovery is started.
"""
from __future__ import annotations

import threading

from flask import g, request
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
                            "fba_verified=%s",
                            platform,
                            notification_record_id,
                            result.get("order_id"),
                            result.get("recovered"),
                            result.get("duplicate_skipped"),
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
    """Queue exact failures, order orphans, and missed FBA settlement checks."""
    from extensions import db

    selected: list[tuple[str, int]] = []

    # Failed/stranded exact notifications for both marketplaces.
    for platform, table_name in (
        ("amazon", "webhooks.amazon_notifications"),
        ("ebay", "webhooks.ebay_notifications"),
    ):
        rows = db.session.execute(
            text(
                f"""
                SELECT id
                FROM {table_name}
                WHERE received_at >= NOW() - INTERVAL '48 hours'
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

    # Legacy Amazon bug repair: before the completion guard existed, an AFN
    # ORDER_CHANGE could be marked COMPLETED without creating MarketplaceOrder.
    # Detect only Amazon UK customer-order notifications already held in BT38;
    # do not scan Amazon, Warehouse, listings, or MCF/internal marketplaces.
    amazon_orphans = db.session.execute(
        text(
            """
            SELECT n.id
            FROM webhooks.amazon_notifications AS n
            WHERE n.received_at >= NOW() - INTERVAL '48 hours'
              AND n.processing_status = 'COMPLETED'
              AND n.notification_type = 'ORDER_CHANGE'
              AND n.payload_json->'Payload'->'OrderChangeNotification'
                    ->'Summary'->>'MarketplaceId' = 'A1F83G8C2ARO7P'
              AND COALESCE(
                    n.payload_json->'Payload'->'OrderChangeNotification'
                      ->>'AmazonOrderId',
                    ''
                  ) <> ''
              AND NOT EXISTS (
                    SELECT 1
                    FROM marketplace_orders AS mo
                    WHERE mo.marketplace_order_id =
                        n.payload_json->'Payload'->'OrderChangeNotification'
                          ->>'AmazonOrderId'
                  )
            ORDER BY n.id ASC
            LIMIT :limit
            """
        ),
        {"limit": int(limit)},
    ).scalars().all()
    selected.extend(("amazon", int(row_id)) for row_id in amazon_orphans)

    # Exact FBA settlement durability. The normal webhook performs an immediate
    # Seller-SKU read and schedules a 90-second in-memory recheck. A deployment
    # inside that settlement window used to lose the delayed event permanently.
    # Select only completed Amazon UK FBA orders whose stored FBA truth was not
    # refreshed at/after the 90-second settlement point. Recovery sees that the
    # canonical order already exists, so it NEVER replays the order; it performs
    # one exact Seller-SKU Amazon read and publishes only a committed change.
    fba_settlement_gaps = db.session.execute(
        text(
            """
            SELECT DISTINCT n.id
            FROM webhooks.amazon_notifications AS n
            JOIN marketplace_orders AS mo
              ON mo.marketplace_order_id =
                 n.payload_json->'Payload'->'OrderChangeNotification'
                   ->>'AmazonOrderId'
            LEFT JOIN amazon_fba_inventory AS afi
              ON afi.seller_sku = mo.sku
             AND (
                  afi.store_id = mo.store_id
                  OR afi.store_id IS NULL
             )
            WHERE n.received_at >= NOW() - INTERVAL '48 hours'
              AND n.received_at <= NOW() - INTERVAL '90 seconds'
              AND n.processing_status = 'COMPLETED'
              AND n.notification_type = 'ORDER_CHANGE'
              AND n.payload_json->'Payload'->'OrderChangeNotification'
                    ->'Summary'->>'MarketplaceId' = 'A1F83G8C2ARO7P'
              AND UPPER(COALESCE(mo.fulfillment_type, '')) IN ('FBA', 'AFN', 'AMAZON')
              AND (
                    afi.id IS NULL
                    OR afi.last_synced_at IS NULL
                    OR afi.last_synced_at < n.received_at + INTERVAL '90 seconds'
                  )
            ORDER BY n.id ASC
            LIMIT :limit
            """
        ),
        {"limit": int(limit)},
    ).scalars().all()
    selected.extend(
        ("amazon", int(row_id))
        for row_id in fba_settlement_gaps
    )

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
    """One bounded DB-only safety pass after process restart."""
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
                "BT38 queued %s stranded exact webhook recoveries after restart",
                queued,
            )
    except Exception:
        from extensions import db

        db.session.rollback()
        app.logger.exception(
            "BT38 stranded exact webhook recovery selector failed"
        )

    return None


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

    if not getattr(g, "bt38_rejected_webhook_recovery_requested", False):
        scheduled = request_rejected_webhook_recovery(
            platform,
            notification_record_id,
        )
        if scheduled:
            g.bt38_rejected_webhook_recovery_requested = True

    return response
