"""Global Amazon FBA settlement -> DB -> UI alignment.

One rule for every FBA listing:
- Amazon is the inventory authority.
- Available, reserved and inbound come from the exact Amazon Seller-SKU read.
- Order quantity never mutates FBA inventory locally.
- Immediate webhook verification may observe pre-settlement Amazon truth.
- The existing delayed exact-SKU verification is the settlement check.
- If that delayed check commits a changed FBA record, publish the same exact
  affected identity through the existing sleeping-browser UI event channel.
- No marketplace-wide inventory scan, Warehouse scan, or full-page refresh.
"""
from __future__ import annotations

from sqlalchemy import text


def _clean(value):
    value = str(value or "").strip()
    return value or None


def _amazon_notification_id_for_event(event: dict) -> int | None:
    """Resolve only the durable notification for this exact Amazon order."""
    notification_record_id = event.get("notification_record_id")
    try:
        if notification_record_id is not None:
            return int(notification_record_id)
    except (TypeError, ValueError):
        pass

    order_id = _clean(event.get("order_id"))
    if not order_id:
        return None

    from extensions import db

    row = db.session.execute(
        text(
            """
            SELECT id
            FROM webhooks.amazon_notifications
            WHERE notification_type = 'ORDER_CHANGE'
              AND payload_json->'Payload'->'OrderChangeNotification'
                    ->>'AmazonOrderId' = :order_id
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {"order_id": order_id},
    ).scalar()
    return int(row) if row is not None else None


def _is_committed_fba_change(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("object") or "") != "AmazonFBAInventory":
        return False
    return bool(
        result.get("stock_changed")
        or result.get("fba_inventory_changed")
        or result.get("rows_updated")
        or result.get("changed")
    )


def _publish_delayed_fba_change(event: dict, result: dict) -> bool:
    """Wake the browser only when this exact FBA settlement changed Neon."""
    if not _is_committed_fba_change(result):
        return False

    notification_record_id = _amazon_notification_id_for_event(event)
    if notification_record_id is None:
        return False

    from services.governed_ui_event_signal import publish_webhook_ui_event

    scope = dict(result)
    for key in (
        "event_type",
        "seller_sku",
        "listing_id",
        "order_id",
        "warehouse_stock_id",
        "group_id",
        "store_id",
    ):
        if scope.get(key) in (None, "") and event.get(key) not in (None, ""):
            scope[key] = event.get(key)

    publish_webhook_ui_event(
        platform="amazon",
        notification_record_id=notification_record_id,
        scope=scope,
    )
    return True


def install_fba_settlement_ui_alignment() -> bool:
    """Attach one global UI handoff to the existing exact-event runtime."""
    import services.governed_runtime_engine as runtime

    current = runtime._run_light_reconcile_cycle
    if getattr(current, "_bt38_fba_settlement_ui_aligned", False):
        return False

    def aligned_cycle(events=None, source="webhook_verification_15m"):
        event_list = list(events or [])
        summary = current(events=event_list, source=source)
        results = list(summary.get("results") or [])

        published = 0
        for event, result in zip(event_list, results):
            try:
                if _publish_delayed_fba_change(event, result):
                    published += 1
            except Exception:
                # UI signalling is downstream of the committed Amazon truth.
                # Never roll back or repeat the inventory mutation because a
                # browser wake-up failed; the next page request still reads DB.
                import logging
                logging.exception(
                    "Delayed FBA DB-to-UI handoff failed order_id=%s sku=%s",
                    event.get("order_id"),
                    event.get("seller_sku"),
                )

        summary["fba_ui_events_published"] = published
        return summary

    aligned_cycle._bt38_fba_settlement_ui_aligned = True
    runtime._run_light_reconcile_cycle = aligned_cycle
    return True


install_fba_settlement_ui_alignment()
