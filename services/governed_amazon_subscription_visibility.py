from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from extensions import db
from models import SyncLog
from services.governed_amazon_listing_fulfillment_refresh import (
    ensure_governed_amazon_listing_notification_subscriptions,
)


def record_governed_amazon_listing_subscription_reconciliation(
    *,
    store_id: int,
    source: str = "amazon_listing_subscription_visibility",
) -> dict[str, Any]:
    """Run the existing listing subscription reconciliation and persist its result.

    This is diagnostic visibility only. It creates no destination, queue, event bus,
    rule, importer, scheduler, canonical writer, or marketplace write path.
    """
    try:
        result = ensure_governed_amazon_listing_notification_subscriptions(
            store_id=int(store_id),
        )
    except Exception as exc:
        result = {
            "success": False,
            "governed": True,
            "store_id": int(store_id),
            "reason": "amazon_listing_subscription_reconcile_failed",
            "error": str(exc),
            "destination_created": False,
        }

    subscriptions = []
    for row in list(result.get("subscriptions") or []):
        subscriptions.append({
            "notification_type": row.get("notification_type"),
            "subscription_id": row.get("subscription_id"),
            "created": bool(row.get("created")),
        })

    visible = {
        "success": bool(result.get("success")),
        "governed": True,
        "store_id": int(store_id),
        "source": source,
        "reason": result.get("reason"),
        "error": result.get("error"),
        "destination_id": result.get("destination_id"),
        "destination_created": bool(result.get("destination_created", False)),
        "subscriptions": subscriptions,
    }

    status = "success" if visible["success"] else "error"
    message = (
        "event_type=amazon_listing_subscription_reconcile "
        f"source={source} "
        f"store_id={int(store_id)} "
        f"success={visible['success']} "
        f"reason={visible.get('reason') or ''} "
        f"destination_id={visible.get('destination_id') or ''} "
        f"subscriptions={json.dumps(subscriptions, sort_keys=True)}"
    )[:500]

    db.session.add(SyncLog(
        store_id=int(store_id),
        status=status,
        message=message,
        items_synced=sum(
            1 for row in subscriptions if row.get("subscription_id")
        ),
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    return visible
