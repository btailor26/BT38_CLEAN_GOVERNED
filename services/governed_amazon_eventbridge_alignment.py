"""Align Amazon SP-API EventBridge notifications to BT38's existing SQS queue.

This module is deliberately infrastructure-only. It does not create a second
queue, importer, canonical writer, scheduler, stock path, or marketplace push.
It reuses the Amazon SQS queue already consumed by governed_runtime_engine.
"""

from __future__ import annotations

import json
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError
from sp_api.api import Notifications

from models import Store
from services.governed_amazon_listing_fulfillment_refresh import (
    _credentials,
    _marketplace_for_store,
)


RULE_NAME = "bt38-amazon-listing-notifications"
TARGET_ID = "bt38-existing-amazon-sqs"
DESTINATION_NAME = "BT38 Amazon EventBridge"
LISTING_NOTIFICATION_TYPES = [
    "LISTINGS_ITEM_STATUS_CHANGE",
    "LISTINGS_ITEM_MFN_QUANTITY_CHANGE",
]


def _destination_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("destinations") or []
        return [row for row in rows if isinstance(row, dict)]
    return []


def _eventbridge_spec(destination: dict[str, Any] | None) -> dict[str, Any]:
    return dict(
        ((destination or {}).get("resourceSpecification") or {}).get(
            "eventBridge"
        )
        or {}
    )


def _existing_eventbridge_destination(client: Notifications):
    rows = _destination_rows(client.get_destinations().payload or {})
    return next((row for row in rows if _eventbridge_spec(row)), None)


def _queue_identity():
    # Reuse the exact SQS connection and queue already used by the governed
    # Amazon consumer. This prevents a second transport path from being born.
    from services.governed_runtime_engine import _amazon_sqs_connection

    sqs, queue_url = _amazon_sqs_connection()
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["QueueArn", "Policy"],
    ).get("Attributes") or {}
    queue_arn = str(attrs.get("QueueArn") or "").strip()
    if not queue_arn:
        raise RuntimeError("existing_amazon_sqs_queue_arn_missing")

    parts = queue_arn.split(":", 5)
    if len(parts) != 6 or parts[2] != "sqs":
        raise RuntimeError("existing_amazon_sqs_queue_arn_invalid")

    return {
        "client": sqs,
        "queue_url": queue_url,
        "queue_arn": queue_arn,
        "policy": attrs.get("Policy"),
        "region": parts[3],
        "account_id": parts[4],
    }


def _ensure_destination(client: Notifications, *, account_id: str, region: str):
    destination = _existing_eventbridge_destination(client)
    created = False

    if destination is None:
        response = client.create_destination(
            name=DESTINATION_NAME,
            account_id=account_id,
            region=region,
        )
        payload = response.payload or {}
        destination = payload if isinstance(payload, dict) else None
        created = True

        # Amazon creates the partner event source as part of createDestination.
        # Re-read once if the client response does not include the full resource.
        if not _eventbridge_spec(destination):
            destination = _existing_eventbridge_destination(client)

    destination_id = str(
        (destination or {}).get("destinationId") or ""
    ).strip()
    spec = _eventbridge_spec(destination)
    event_source_name = str(spec.get("name") or "").strip()

    if not destination_id:
        raise RuntimeError("amazon_eventbridge_destination_id_missing")
    if not event_source_name:
        raise RuntimeError("amazon_eventbridge_partner_source_name_missing")

    return {
        "destination_id": destination_id,
        "event_source_name": event_source_name,
        "destination_created": created,
    }


def _ensure_partner_bus(events, event_source_name: str):
    try:
        events.describe_event_bus(Name=event_source_name)
        return False
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"ResourceNotFoundException", "NotFoundException"}:
            raise

    # A newly-created SP-API destination can take a few seconds before the
    # partner source is visible to CreateEventBus. Retry only this bounded
    # association step; no background worker or new scheduler is introduced.
    last_error = None
    for attempt in range(5):
        try:
            events.create_event_bus(
                Name=event_source_name,
                EventSourceName=event_source_name,
            )
            return True
        except ClientError as exc:
            last_error = exc
            code = str((exc.response.get("Error") or {}).get("Code") or "")
            if code not in {
                "ResourceNotFoundException",
                "NotFoundException",
                "InvalidStateException",
            }:
                raise
            if attempt < 4:
                time.sleep(2)

    raise last_error or RuntimeError("amazon_partner_event_bus_create_failed")


def _ensure_queue_policy(sqs, *, queue_url: str, queue_arn: str, policy_raw: Any, rule_arn: str):
    try:
        policy = json.loads(policy_raw) if policy_raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        policy = {}

    if not isinstance(policy, dict):
        policy = {}
    policy.setdefault("Version", "2012-10-17")
    statements = policy.get("Statement") or []
    if isinstance(statements, dict):
        statements = [statements]
    statements = [
        row
        for row in statements
        if not (
            isinstance(row, dict)
            and row.get("Sid") == "BT38AmazonEventBridgeSendMessage"
        )
    ]
    statements.append(
        {
            "Sid": "BT38AmazonEventBridgeSendMessage",
            "Effect": "Allow",
            "Principal": {"Service": "events.amazonaws.com"},
            "Action": "sqs:SendMessage",
            "Resource": queue_arn,
            "Condition": {"ArnEquals": {"aws:SourceArn": rule_arn}},
        }
    )
    policy["Statement"] = statements
    sqs.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={"Policy": json.dumps(policy, separators=(",", ":"))},
    )


def align_governed_amazon_eventbridge_to_existing_sqs(*, store_id: int) -> dict[str, Any]:
    """Idempotently align SP-API EventBridge to the existing BT38 SQS queue."""
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
            "detail-type": LISTING_NOTIFICATION_TYPES,
        },
        separators=(",", ":"),
    )
    rule = events.put_rule(
        Name=RULE_NAME,
        EventBusName=destination["event_source_name"],
        EventPattern=event_pattern,
        State="ENABLED",
        Description=(
            "BT38 listing notifications to the existing governed Amazon SQS queue"
        ),
    )
    rule_arn = str(rule.get("RuleArn") or "").strip()
    if not rule_arn:
        raise RuntimeError("amazon_eventbridge_rule_arn_missing")

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
                # Strip the EventBridge envelope. The existing governed SQS
                # consumer therefore receives the direct SP-API notification
                # body it already knows how to process.
                "InputPath": "$.detail",
            }
        ],
    )
    if int(targets.get("FailedEntryCount") or 0):
        raise RuntimeError(
            "amazon_eventbridge_target_alignment_failed: "
            + json.dumps(targets.get("FailedEntries") or [], default=str)
        )

    return {
        "success": True,
        "governed": True,
        "store_id": int(store_id),
        "destination_id": destination["destination_id"],
        "destination_created": destination["destination_created"],
        "event_source_name": destination["event_source_name"],
        "event_bus_created": bus_created,
        "rule_name": RULE_NAME,
        "rule_arn": rule_arn,
        "target": "existing_amazon_sqs",
        "input_path": "$.detail",
        "queue_arn": queue["queue_arn"],
        "region": queue["region"],
        "account_id": queue["account_id"],
        "new_queue_created": False,
        "new_consumer_created": False,
        "new_importer_created": False,
    }
