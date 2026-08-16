from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from extensions import db
from models import SyncLog
from services.governed_amazon_eventbridge_alignment import (
    align_governed_amazon_eventbridge_to_existing_sqs,
)
from services.governed_amazon_listing_fulfillment_refresh import (
    ensure_governed_amazon_listing_notification_subscriptions,
)


def record_governed_amazon_listing_subscription_reconciliation(
    *,
    store_id: int,
    source: str = "amazon_listing_subscription_visibility",
) -> dict[str, Any]:
    """Align the missing EventBridge hop, then reconcile existing subscriptions.

    This remains upstream infrastructure alignment only. It reuses BT38's
    existing Amazon SQS queue/consumer and existing canonical listing path.
    It creates no second queue, importer, scheduler, writer, stock path, or push.
    """
    infrastructure = None
    try:
        infrastructure = align_governed_amazon_eventbridge_to_existing_sqs(
            store_id=int(store_id),
        )
        if not infrastructure.get("success"):
            raise RuntimeError(
                infrastructure.get("reason")
                or "amazon_eventbridge_alignment_failed"
            )

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
            "destination_created": bool(
                (infrastructure or {}).get("destination_created", False)
            ),
        }

    subscriptions = []
    for row in list(result.get("subscriptions") or []):
        subscriptions.append({
            "notification_type": row.get("notification_type"),
            "subscription_id": row.get("subscription_id"),
            "created": bool(row.get("created")),
        })

    infrastructure_visible = {
        key: (infrastructure or {}).get(key)
        for key in (
            "success",
            "destination_id",
            "destination_created",
            "event_source_name",
            "event_bus_created",
            "rule_name",
            "rule_arn",
            "target",
            "input_path",
            "queue_arn",
            "region",
            "account_id",
            "new_queue_created",
            "new_consumer_created",
            "new_importer_created",
        )
        if key in (infrastructure or {})
    }

    visible = {
        "success": bool(result.get("success")),
        "governed": True,
        "store_id": int(store_id),
        "source": source,
        "reason": result.get("reason"),
        "error": result.get("error"),
        "destination_id": (
            result.get("destination_id")
            or infrastructure_visible.get("destination_id")
        ),
        "destination_created": bool(
            result.get(
                "destination_created",
                infrastructure_visible.get("destination_created", False),
            )
        ),
        "infrastructure": infrastructure_visible,
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