"""Align Amazon MCF status notifications to BT38's existing Amazon SQS path.

Scope is deliberately narrow:
- reuse the existing SP-API EventBridge destination, partner bus and SQS queue;
- add only FULFILLMENT_ORDER_STATUS;
- create no queue, consumer, importer, inventory writer or marketplace writer;
- leave LISTINGS_ITEM_* subscriptions and rules untouched.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from sp_api.api import Notifications
from sp_api.base.notifications import NotificationType

from models import Store
from services.governed_amazon_eventbridge_alignment import (
    _ensure_destination,
    _ensure_partner_bus,
    _ensure_queue_policy,
    _queue_identity,
)
from services.governed_amazon_listing_fulfillment_refresh import (
    _credentials,
    _marketplace_for_store,
)


RULE_NAME = "bt38-amazon-mcf-notifications"
TARGET_ID = "bt38-existing-amazon-sqs-mcf"
MCF_NOTIFICATION_TYPE = "FULFILLMENT_ORDER_STATUS"


def align_governed_amazon_mcf_notification_to_existing_sqs(
    *,
    store_id: int,
) -> dict[str, Any]:
    """Idempotently add Amazon MCF status to the existing governed transport."""
    store = (
        Store.query
        .filter(
            Store.id == int(store_id),
            Store.platform.ilike("%amazon%"),
            Store.is_active == True,  # noqa: E712
        )
        .first()
    )
    if store is None:
        return {
            "success": False,
            "governed": True,
            "reason": "amazon_store_not_found",
            "store_id": int(store_id),
        }

    marketplace, _marketplace_id, raw = _marketplace_for_store(store)
    notifications = Notifications(
        marketplace=marketplace,
        credentials=_credentials(raw),
    )

    queue = _queue_identity()
    destination = _ensure_destination(
        notifications,
        account_id=queue["account_id"],
        region=queue["region"],
    )

    events = boto3.client("events", region_name=queue["region"])
    bus_created = _ensure_partner_bus(
        events,
        destination["event_source_name"],
    )

    event_pattern = json.dumps(
        {
            "source": [
                {"prefix": "aws.partner/sellingpartnerapi.amazon.com"}
            ],
            "detail-type": [MCF_NOTIFICATION_TYPE],
        },
        separators=(",", ":"),
    )
    rule = events.put_rule(
        Name=RULE_NAME,
        EventBusName=destination["event_source_name"],
        EventPattern=event_pattern,
        State="ENABLED",
        Description=(
            "BT38 Amazon MCF status notifications to the existing governed SQS queue"
        ),
    )
    rule_arn = str(rule.get("RuleArn") or "").strip()
    if not rule_arn:
        raise RuntimeError("amazon_mcf_eventbridge_rule_arn_missing")

    _ensure_queue_policy(
        queue["client"],
        queue_url=queue["queue_url"],
        queue_arn=queue["queue_arn"],
        policy_raw=queue["policy"],
        rule_arn=rule_arn,
    )

    targets = events.put_targets(
        Rule=RULE_NAME,
        EventBusName=destination["event_source_name"],
        Targets=[
            {
                "Id": TARGET_ID,
                "Arn": queue["queue_arn"],
                "InputPath": "$.detail",
            }
        ],
    )
    if int(targets.get("FailedEntryCount") or 0):
        raise RuntimeError(
            "amazon_mcf_eventbridge_target_alignment_failed: "
            + json.dumps(targets.get("FailedEntries") or [], default=str)
        )

    notification_type = NotificationType[MCF_NOTIFICATION_TYPE]
    existing = None
    try:
        existing = notifications.get_subscription(notification_type).payload or {}
    except Exception as exc:
        message = str(exc)
        if (
            "404" not in message
            and "NotFound" not in message
            and "not found" not in message.lower()
        ):
            raise

    subscription_id = str((existing or {}).get("subscriptionId") or "").strip()
    created = False
    if not subscription_id:
        created_payload = notifications.create_subscription(
            notification_type,
            destination_id=destination["destination_id"],
        ).payload or {}
        subscription_id = str(created_payload.get("subscriptionId") or "").strip()
        created = True

    return {
        "success": bool(subscription_id),
        "governed": True,
        "store_id": int(store_id),
        "notification_type": MCF_NOTIFICATION_TYPE,
        "subscription_id": subscription_id or None,
        "subscription_created": created,
        "destination_id": destination["destination_id"],
        "destination_created": destination["destination_created"],
        "event_source_name": destination["event_source_name"],
        "event_bus_created": bus_created,
        "rule_name": RULE_NAME,
        "rule_arn": rule_arn,
        "target": "existing_amazon_sqs",
        "queue_arn": queue["queue_arn"],
        "new_queue_created": False,
        "new_consumer_created": False,
        "new_importer_created": False,
    }
