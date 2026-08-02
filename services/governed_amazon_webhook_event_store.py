"""Canonical Amazon webhook-event persistence.

One route only:

raw amazon_notifications
    -> amazon_webhook_events
    -> governed business processing

The raw notification remains an immutable transport record.
The parsed event row is the canonical incoming Amazon event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def persist_amazon_webhook_events(
    *,
    notification_record_id: int,
    payload: dict,
) -> dict:
    """Persist one canonical event per Amazon order item.

    Returns a normalized payload which downstream governed processing uses.
    """
    from extensions import db

    envelope = _dict(payload)
    change = _dict(
        _dict(envelope.get("Payload")).get(
            "OrderChangeNotification"
        )
    )
    summary = _dict(change.get("Summary"))
    metadata = _dict(
        envelope.get("NotificationMetadata")
    )

    notification_type = str(
        envelope.get("NotificationType") or ""
    ).strip()

    amazon_order_id = str(
        change.get("AmazonOrderId") or ""
    ).strip()

    seller_id = str(
        change.get("SellerId") or ""
    ).strip()

    marketplace_id = str(
        summary.get("MarketplaceId") or ""
    ).strip()

    fulfilment_channel = str(
        summary.get("FulfillmentType") or ""
    ).strip()

    order_status = str(
        summary.get("OrderStatus") or ""
    ).strip()

    notification_id = str(
        metadata.get("NotificationId") or ""
    ).strip()

    items = summary.get("OrderItems")

    if not isinstance(items, list):
        items = []

    if not notification_type:
        raise ValueError(
            "amazon_notification_type_missing"
        )

    if notification_type == "ORDER_CHANGE" and not amazon_order_id:
        raise ValueError(
            "amazon_order_id_missing"
        )

    if notification_type == "ORDER_CHANGE" and not items:
        raise ValueError(
            "amazon_order_items_missing"
        )

    event_ids: list[int] = []
    normalized_items: list[dict] = []

    for item in items:
        item = _dict(item)

        order_item_id = str(
            item.get("OrderItemId") or ""
        ).strip()

        seller_sku = str(
            item.get("SellerSKU") or ""
        ).strip()

        fnsku = str(
            item.get("FulfillmentNetworkSKU") or ""
        ).strip()

        quantity = int(item.get("Quantity") or 0)

        if not order_item_id:
            raise ValueError(
                "amazon_order_item_id_missing"
            )

        if not seller_sku:
            raise ValueError(
                "amazon_seller_sku_missing"
            )

        normalized = dict(envelope)
        normalized.update({
            "marketplace": "amazon",
            "event_type": notification_type,
            "notification_type": notification_type,
            "notification_id": notification_id,
            "marketplace_order_id": amazon_order_id,
            "amazonOrderId": amazon_order_id,
            "order_id": amazon_order_id,
            "marketplace_order_item_id": order_item_id,
            "order_item_id": order_item_id,
            "orderItemId": order_item_id,
            "seller_sku": seller_sku,
            "sellerSku": seller_sku,
            "sku": seller_sku,
            "fnsku": fnsku or None,
            "quantity": quantity,
            "status": order_status,
            "order_status": order_status,
            "fulfillment_type": fulfilment_channel,
            "fulfilment_channel": fulfilment_channel,
            "_bt38_notification_record_id": int(
                notification_record_id
            ),
        })

        existing_id = db.session.execute(
            text("""
                SELECT id
                FROM webhooks.amazon_webhook_events
                WHERE notification_record_id = :record_id
                  AND amazon_order_id = :order_id
                  AND order_item_id = :item_id
                  AND seller_sku = :seller_sku
                ORDER BY id DESC
                LIMIT 1
            """),
            {
                "record_id": int(notification_record_id),
                "order_id": amazon_order_id,
                "item_id": order_item_id,
                "seller_sku": seller_sku,
            },
        ).scalar_one_or_none()

        if existing_id is None:
            event_id = db.session.execute(
                text("""
                    INSERT INTO webhooks.amazon_webhook_events (
                        notification_id,
                        notification_type,
                        seller_id,
                        marketplace_id,
                        amazon_order_id,
                        order_item_id,
                        asin,
                        fnsku,
                        seller_sku,
                        fulfilment_channel,
                        status,
                        received_at,
                        verify_after,
                        retry_count,
                        headers_json,
                        payload_json,
                        notification_record_id
                    )
                    VALUES (
                        :notification_id,
                        :notification_type,
                        :seller_id,
                        :marketplace_id,
                        :amazon_order_id,
                        :order_item_id,
                        :asin,
                        :fnsku,
                        :seller_sku,
                        :fulfilment_channel,
                        'RECEIVED',
                        NOW(),
                        NOW(),
                        0,
                        CAST(:headers_json AS jsonb),
                        CAST(:payload_json AS jsonb),
                        :notification_record_id
                    )
                    RETURNING id
                """),
                {
                    "notification_id": notification_id or None,
                    "notification_type": notification_type,
                    "seller_id": seller_id or None,
                    "marketplace_id": marketplace_id or None,
                    "amazon_order_id": amazon_order_id,
                    "order_item_id": order_item_id,
                    "asin": item.get("ASIN"),
                    "fnsku": fnsku or None,
                    "seller_sku": seller_sku,
                    "fulfilment_channel": (
                        fulfilment_channel or None
                    ),
                    "headers_json": "{}",
                    "payload_json": __import__(
                        "json"
                    ).dumps(
                        normalized,
                        default=str,
                    ),
                    "notification_record_id": int(
                        notification_record_id
                    ),
                },
            ).scalar_one()

            event_ids.append(int(event_id))
        else:
            event_ids.append(int(existing_id))

        normalized_items.append(normalized)

    db.session.commit()

    return {
        "success": True,
        "event_ids": event_ids,
        "event_count": len(event_ids),
        "payloads": normalized_items,
    }


def mark_amazon_webhook_events(
    event_ids: list[int],
    *,
    status: str,
    error: str | None = None,
) -> None:
    from extensions import db

    if not event_ids:
        return

    processed = status.upper() == "PROCESSED"

    db.session.execute(
        text("""
            UPDATE webhooks.amazon_webhook_events
            SET
                status = :status,
                processed_at = CASE
                    WHEN :processed THEN NOW()
                    ELSE processed_at
                END,
                verified_at = CASE
                    WHEN :processed THEN NOW()
                    ELSE verified_at
                END,
                last_error = :error
            WHERE id = ANY(:event_ids)
        """),
        {
            "status": status.upper(),
            "processed": processed,
            "error": error,
            "event_ids": list(event_ids),
        },
    )

    db.session.commit()
