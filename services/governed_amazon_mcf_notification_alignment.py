"""Align Amazon MCF status notifications to BT38's existing Amazon SQS path.

Scope is deliberately narrow:
- reuse the existing bt38-amazon-notifications SQS queue and consumer;
- create/reuse an SP-API SQS destination for that exact queue;
- subscribe only FULFILLMENT_ORDER_STATUS to that SQS destination;
- create no queue, EventBridge rule, consumer, importer, inventory writer or marketplace writer;
- leave LISTINGS_ITEM_* EventBridge subscriptions and rules untouched.
"""

from __future__ import annotations

import json
from typing import Any

from sp_api.api import Notifications
from sp_api.base.notifications import NotificationType

from models import Store
from services.governed_amazon_eventbridge_alignment import _queue_identity
from services.governed_amazon_listing_fulfillment_refresh import (
    _credentials,
    _marketplace_for_store,
)


MCF_NOTIFICATION_TYPE = "FULFILLMENT_ORDER_STATUS"
MCF_DESTINATION_NAME = "bt38-amazon-mcf-existing-sqs"
MCF_QUEUE_POLICY_SID = "BT38AmazonSPAPISQSSendMessage"
SPAPI_SQS_PRINCIPAL = "arn:aws:iam::437568002678:root"


def _as_rows(payload: Any) -> list[dict[str, Any]]:
    """Normalize Notifications getDestinations payloads across client versions."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("destinations", "Destinations"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]

    nested = payload.get("payload")
    if nested is not None and nested is not payload:
        return _as_rows(nested)
    return []


def _destination_queue_arn(row: dict[str, Any]) -> str:
    resource = row.get("resource") or row.get("resourceSpecification") or {}
    if not isinstance(resource, dict):
        return ""
    sqs = resource.get("sqs") or resource.get("Sqs") or {}
    if not isinstance(sqs, dict):
        return ""
    return str(sqs.get("arn") or sqs.get("Arn") or "").strip()


def _destination_id(row: dict[str, Any]) -> str:
    return str(row.get("destinationId") or row.get("destination_id") or "").strip()


def _ensure_spapi_sqs_queue_policy(
    sqs,
    *,
    queue_url: str,
    queue_arn: str,
    policy_raw: Any,
) -> None:
    """Grant SP-API direct SQS delivery without replacing existing policy entries."""
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

    # Replace only BT38's direct SP-API statement. Existing EventBridge/listing
    # permissions and any unrelated queue policy statements remain untouched.
    statements = [
        row
        for row in statements
        if not (
            isinstance(row, dict)
            and row.get("Sid") == MCF_QUEUE_POLICY_SID
        )
    ]
    statements.append(
        {
            "Sid": MCF_QUEUE_POLICY_SID,
            "Effect": "Allow",
            "Principal": {"AWS": SPAPI_SQS_PRINCIPAL},
            "Action": ["sqs:GetQueueAttributes", "sqs:SendMessage"],
            "Resource": queue_arn,
        }
    )
    policy["Statement"] = statements
    sqs.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={"Policy": json.dumps(policy, separators=(",", ":"))},
    )


def _ensure_sqs_destination(
    notifications: Notifications,
    *,
    queue_arn: str,
    account_id: str,
    region: str,
) -> dict[str, Any]:
    """Reuse an SP-API SQS destination for this queue or create exactly one."""
    existing_payload = notifications.get_destinations().payload or {}
    for row in _as_rows(existing_payload):
        if _destination_queue_arn(row) == queue_arn:
            destination_id = _destination_id(row)
            if destination_id:
                return {
                    "destination_id": destination_id,
                    "destination_created": False,
                }

    created_payload = notifications.create_destination(
        name=MCF_DESTINATION_NAME,
        arn=queue_arn,
        account_id=account_id,
        region=region,
    ).payload or {}
    destination_id = str(
        created_payload.get("destinationId")
        or created_payload.get("destination_id")
        or ""
    ).strip()
    if not destination_id:
        raise RuntimeError("amazon_mcf_sqs_destination_id_missing")

    return {
        "destination_id": destination_id,
        "destination_created": True,
    }


def align_governed_amazon_mcf_notification_to_existing_sqs(
    *,
    store_id: int,
) -> dict[str, Any]:
    """Idempotently align Amazon MCF status to BT38's existing SQS consumer."""
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
    _ensure_spapi_sqs_queue_policy(
        queue["client"],
        queue_url=queue["queue_url"],
        queue_arn=queue["queue_arn"],
        policy_raw=queue["policy"],
    )

    destination = _ensure_sqs_destination(
        notifications,
        queue_arn=queue["queue_arn"],
        account_id=queue["account_id"],
        region=queue["region"],
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
            and "doesn't exist" not in message.lower()
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
        "transport": "sp_api_sqs",
        "queue_arn": queue["queue_arn"],
        "queue_policy_sid": MCF_QUEUE_POLICY_SID,
        "listing_eventbridge_untouched": True,
        "new_queue_created": False,
        "new_consumer_created": False,
        "new_importer_created": False,
    }
