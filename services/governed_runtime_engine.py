"""
BT38 GOVERNED RUNTIME ENGINE

Runtime contract:
- Webhooks perform immediate targeted work through the existing governed path.
- The existing webhook push arms one 15-minute verification for its exact
  Warehouse and marketplace-listing identities.
- Verification checks only those rows and reuses the existing governed push path
  when those exact rows are not aligned.
- No recent-order, Warehouse, group, listing, or marketplace-wide scan is allowed.
- No webhook means no database access.
- Full marketplace hydration is manual/recovery only.
- Amazon FBA remains read-only.
"""

from __future__ import annotations

import json

import logging
import os
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from flask import has_app_context

_started = False
_started_at = None
_status_lock = threading.Lock()

_runtime_lock_handle = None
_RUNTIME_LOCK_PATH = os.getenv(
    "BT38_GOVERNED_RUNTIME_LOCK",
    "/tmp/bt38_governed_runtime_engine.lock",
)

_last_full_sync = None
_last_light_reconcile = None
_last_marketplace_import = None
_last_fba_import = None
_last_ebay_import = None
_last_error = None
_last_event_at = None
_last_event_source = None
_last_verification_result = None

FULL_SYNC_SECONDS = 8 * 60 * 60
LIGHT_RECONCILE_SECONDS = 15 * 60

# Process-memory only. These objects never touch Neon while idle.
_pending_notification_event = threading.Event()

# RETIRED GOVERNED PATH
#
# The governed runtime uses one exact in-process event path.
# services/governed_runtime_job_store.py remains in the repository only for
# historical compatibility. It must never initialise, enqueue, claim or
# complete runtime jobs.
DURABLE_RUNTIME_JOB_PATH_ENABLED = False
_stop_event = threading.Event()
_pending_events = deque()
_pending_events_lock = threading.Lock()

# Amazon SQS is transport only. Each message is handed to the existing
# governed Amazon webhook intake and does not create another execution path.
_amazon_sqs_client = None
_amazon_sqs_queue_url = None


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _clean(value):
    value = str(value or "").strip()
    return value or None


def _safe_int(value, default=None):
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _safe_log(message: str):
    logging.info("[GOVERNED_RUNTIME_ENGINE] %s", message)


def _safe_error(message: str, exc: Exception):
    global _last_error
    _last_error = f"{message}: {exc}"
    logging.exception("[GOVERNED_RUNTIME_ENGINE] %s", _last_error)


def _normalise_webhook_event(source, event=None, **identifiers):
    raw = dict(event or {})
    raw.update({key: value for key, value in identifiers.items() if value is not None})

    event_type = _clean(raw.get("event_type") or raw.get("type"))
    marketplace = _clean(raw.get("marketplace") or raw.get("platform"))
    store_id = _safe_int(raw.get("store_id"))
    warehouse_stock_id = _safe_int(raw.get("warehouse_stock_id"))
    group_id = _safe_int(raw.get("group_id") or raw.get("master_product_group_id"))
    expected_quantity = _safe_int(raw.get("expected_quantity"))

    seller_sku = _clean(raw.get("seller_sku") or raw.get("sku"))
    listing_id = _clean(
        raw.get("listing_id")
        or raw.get("external_listing_id")
        or raw.get("item_id")
    )
    order_id = _clean(
        raw.get("order_id")
        or raw.get("external_order_id")
        or raw.get("amazon_order_id")
    )
    asin = _clean(raw.get("asin"))
    fnsku = _clean(raw.get("fnsku") or raw.get("fnSku"))

    listing_ids = []
    for value in list(raw.get("listing_ids") or []):
        parsed = _safe_int(value)
        if parsed is not None:
            listing_ids.append(parsed)
    listing_ids = sorted(set(listing_ids))

    scope_present = bool(
        store_id is not None
        and any(
            (
                seller_sku,
                listing_id,
                order_id,
                asin,
                fnsku,
                warehouse_stock_id,
                listing_ids,
            )
        )
    )

    requested_verify_after = raw.get("verify_after")

    if isinstance(requested_verify_after, str):
        try:
            requested_verify_after = datetime.fromisoformat(
                requested_verify_after.replace("Z", "+00:00")
            )
            if requested_verify_after.tzinfo is not None:
                requested_verify_after = (
                    requested_verify_after
                    .astimezone(timezone.utc)
                    .replace(tzinfo=None)
                )
        except (TypeError, ValueError):
            requested_verify_after = None

    if not isinstance(requested_verify_after, datetime):
        requested_verify_after = (
            datetime.utcnow()
            + timedelta(seconds=LIGHT_RECONCILE_SECONDS)
        )

    return {
        "source": _clean(source) or "webhook",
        "event_type": event_type,
        "marketplace": marketplace,
        "store_id": store_id,
        "seller_sku": seller_sku,
        "listing_id": listing_id,
        "listing_ids": listing_ids,
        "order_id": order_id,
        "asin": asin,
        "fnsku": fnsku,
        "warehouse_stock_id": warehouse_stock_id,
        "group_id": group_id,
        "expected_quantity": expected_quantity,
        "payload": raw.get("payload"),
        "received_at": datetime.utcnow(),
        "verify_after": requested_verify_after,
        "scope_present": scope_present,
    }


def _event_key(event):
    return (
        event.get("source"),
        event.get("event_type"),
        event.get("marketplace"),
        event.get("store_id"),
        event.get("seller_sku"),
        event.get("listing_id"),
        tuple(event.get("listing_ids") or []),
        event.get("order_id"),
        event.get("asin"),
        event.get("fnsku"),
        event.get("warehouse_stock_id"),
        event.get("group_id"),
    )


def notify_governed_runtime_work(source: str = "webhook", event=None, **identifiers):
    """Persist one exact webhook verification as a durable DB job."""
    global _last_event_at, _last_event_source

    item = _normalise_webhook_event(
        source,
        event=event,
        **identifiers,
    )

    _last_event_at = item["received_at"]
    _last_event_source = item["source"]

    # One governed event path only.
    # The retired database-backed runtime job layer is never invoked.
    result = {
        "queued": True,
        "durable": False,
        "status": "GOVERNED_MEMORY_EVENT",
        "verify_after": item["verify_after"].isoformat(),
    }

    # This exact process-memory event is the only active governed runtime path.
    # The retired database-backed job layer remains disabled for compatibility.
    with _pending_events_lock:
        key = _event_key(item)

        for queued in _pending_events:
            if _event_key(queued) == key:
                queued["verify_after"] = item["verify_after"]
                queued["payload"] = (
                    item.get("payload")
                    or queued.get("payload")
                )
                queued["expected_quantity"] = (
                    item.get("expected_quantity")
                )
                break
        else:
            _pending_events.append(item)

    _pending_notification_event.set()

    return {
        **result,
        "scoped": item["scope_present"],
    }


def _config_on_for_explicit_work(key: str, default: bool = False) -> bool:
    try:
        from models import SystemConfig

        row = SystemConfig.query.filter_by(key=key).first()
        if not row:
            return default
        return _truthy(row.value, default)
    except Exception:
        return default


def _import_fuses_on() -> bool:
    if not _config_on_for_explicit_work("import_enabled", True):
        return False
    if not _config_on_for_explicit_work("runtime_import_enabled", True):
        return False
    if not _config_on_for_explicit_work("marketplace_import_enabled", True):
        return False
    return True


def _stores_for_marketplace_import():
    from models import Store

    return (
        Store.query
        .filter(Store.is_active == True)  # noqa: E712
        .filter(Store.store_mode == "live")
        .order_by(Store.id)
        .all()
    )


def run_governed_marketplace_import_refresh(
    store_id=None,
    source="governed_runtime_engine",
):
    """Explicit full hydration for initial connection/manual recovery only."""
    global _last_marketplace_import, _last_fba_import, _last_ebay_import

    if not _import_fuses_on():
        return {
            "success": False,
            "governed": True,
            "source": source,
            "reason": "import_fuses_blocked",
            "results": [],
        }

    results = []
    stores = _stores_for_marketplace_import()
    if store_id:
        stores = [store for store in stores if int(store.id) == int(store_id)]

    for store in stores:
        platform = str(store.platform or "").strip().lower()
        try:
            if "amazon" in platform:
                if not bool(getattr(store, "fba_import_enabled", False)):
                    results.append({
                        "store_id": store.id,
                        "platform": store.platform,
                        "skipped": True,
                        "reason": "fba_import_disabled",
                    })
                    continue

                from services.governed_amazon_inventory_import import (
                    run_governed_amazon_inventory_import,
                )
                from services.governed_amazon_listing_fulfillment_refresh import (
                    run_governed_amazon_listing_fulfillment_refresh,
                )

                result = run_governed_amazon_inventory_import(
                    store_id=store.id,
                    full_refresh=True,
                    source=source,
                )
                listing_fulfillment = run_governed_amazon_listing_fulfillment_refresh(
                    store_id=store.id,
                )
                _last_fba_import = datetime.utcnow()
                results.append({
                    "store_id": store.id,
                    "platform": store.platform,
                    "success": True,
                    "result": result,
                    "listing_fulfillment": listing_fulfillment,
                })
                continue

            if "ebay" in platform:
                from services.governed_ebay_inventory_import import (
                    run_governed_ebay_inventory_import,
                )

                result = run_governed_ebay_inventory_import(store_id=store.id)
                _last_ebay_import = datetime.utcnow()
                results.append({
                    "store_id": store.id,
                    "platform": store.platform,
                    "success": True,
                    "result": result,
                })
                continue

            results.append({
                "store_id": store.id,
                "platform": store.platform,
                "skipped": True,
                "reason": "unsupported_marketplace_import",
            })
        except Exception as exc:
            _safe_error(f"marketplace hydration failed store_id={store.id}", exc)
            results.append({
                "store_id": store.id,
                "platform": store.platform,
                "success": False,
                "error": str(exc),
            })

    _last_marketplace_import = datetime.utcnow()
    return {
        "success": True,
        "governed": True,
        "source": source,
        "import_only": True,
        "push_started": False,
        "sync_started": False,
        "results": results,
    }


def _first_existing_attribute(model, names):
    for name in names:
        if hasattr(model, name):
            return getattr(model, name), name
    return None, None


def _verify_exact_order(event):
    from models import MarketplaceOrder

    order_column, order_field = _first_existing_attribute(
        MarketplaceOrder,
        ("external_order_id", "marketplace_order_id", "amazon_order_id", "order_id"),
    )
    if order_column is None or not event.get("order_id"):
        return {"verified": False, "reason": "exact_order_identity_unavailable"}

    query = MarketplaceOrder.query.filter(order_column == event["order_id"])
    if hasattr(MarketplaceOrder, "store_id"):
        query = query.filter(MarketplaceOrder.store_id == event["store_id"])

    row = query.first()
    return {
        "verified": row is not None,
        "object": "MarketplaceOrder",
        "identity_field": order_field,
        "identity": event["order_id"],
        "rows_examined_max": 1,
    }


def _verify_exact_fba(event):
    """Refresh and verify one exact Amazon FBA seller SKU only."""
    from extensions import db
    from models import Store
    from backend.adapters.amazon_sp_api_adapter import AmazonSPAPIAdapter
    from services.governed_amazon_inventory_import import (
        apply_governed_amazon_fba_event,
    )

    seller_sku = _clean(event.get("seller_sku"))
    store_id = _safe_int(event.get("store_id"))

    if not seller_sku or store_id is None:
        return {
            "verified": False,
            "reason": "exact_fba_store_and_seller_sku_required",
            "full_scan_started": False,
        }

    store = (
        Store.query
        .filter(
            Store.id == store_id,
            Store.platform.ilike("%amazon%"),
            Store.is_active == True,  # noqa: E712
        )
        .first()
    )

    if store is None:
        return {
            "verified": False,
            "reason": "amazon_store_not_found",
            "store_id": store_id,
            "seller_sku": seller_sku,
            "full_scan_started": False,
        }

    rows = AmazonSPAPIAdapter(store).get_inventory(
        seller_skus=[seller_sku],
    )

    matching_rows = [
        row
        for row in rows
        if _clean(row.get("seller_sku")) == seller_sku
    ]

    if not matching_rows:
        return {
            "verified": False,
            "reason": "exact_fba_inventory_not_returned",
            "store_id": store_id,
            "seller_sku": seller_sku,
            "rows_received": len(rows),
            "full_scan_started": False,
        }

    fba_result = apply_governed_amazon_fba_event(
        store_id=store_id,
        payload=matching_rows[0],
        source="amazon_webhook_exact_sku_verification",
    )

    group_result = None
    group_id = fba_result.get("group_id")
    warehouse_stock_id = fba_result.get("warehouse_stock_id")

    # Resolve the existing Product Group from the persisted Warehouse link
    # when the FBA listing row itself has no group ID.
    if group_id is None and warehouse_stock_id is not None:
        from models import WarehouseStock

        linked_stock = db.session.get(
            WarehouseStock,
            int(warehouse_stock_id),
        )

        if linked_stock is not None:
            group_id = getattr(
                linked_stock,
                "master_product_group_id",
                None,
            )

            if group_id is not None:
                group_id = int(group_id)
                fba_result["group_id"] = group_id

                refresh_scope = fba_result.get("refresh_scope")
                if isinstance(refresh_scope, dict):
                    refresh_scope["group_id"] = group_id

                nested_result = fba_result.get("result")
                if isinstance(nested_result, dict):
                    nested_result["group_id"] = group_id

    if (
        fba_result.get("success")
        and fba_result.get("stock_changed")
        and group_id is not None
        and warehouse_stock_id is not None
    ):
        from governed_group_propagation_routes import (
            run_governed_group_propagation,
        )

        response = run_governed_group_propagation(
            int(group_id),
            payload={
                "warehouse_stock_id": int(warehouse_stock_id),
                "source": "amazon_webhook_exact_fba_handoff",
            },
        )

        response_object = response[0] if isinstance(response, tuple) else response

        if hasattr(response_object, "get_json"):
            group_result = response_object.get_json(silent=True)
        elif isinstance(response_object, dict):
            group_result = response_object
        else:
            group_result = {"result": str(response_object)}

    db.session.expire_all()

    return {
        **fba_result,
        "verified": bool(fba_result.get("success")),
        "aligned": bool(fba_result.get("success")),
        "object": "AmazonFBAInventory",
        "identity_field": "seller_sku",
        "identity": seller_sku,
        "rows_examined_max": 1,
        "rows_received_from_amazon": len(rows),
        "group_propagation": group_result,
        "full_scan_started": False,
        "recent_order_import_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
    }


def _verify_exact_listing(event):
    from models import MarketplaceListing

    query = MarketplaceListing.query.filter(
        MarketplaceListing.store_id == event["store_id"]
    )
    identity = None
    identity_field = None

    candidates = (
        ("external_sku", event.get("seller_sku")),
        ("external_listing_id", event.get("listing_id")),
        ("fnsku", event.get("fnsku")),
        ("asin", event.get("asin")),
    )
    for field, value in candidates:
        if value and hasattr(MarketplaceListing, field):
            query = query.filter(getattr(MarketplaceListing, field) == value)
            identity = value
            identity_field = field
            break

    if identity is None:
        return {"verified": False, "reason": "exact_listing_identity_unavailable"}

    row = query.first()
    return {
        "verified": row is not None,
        "object": "MarketplaceListing",
        "identity_field": identity_field,
        "identity": identity,
        "rows_examined_max": 1,
    }


def _execute_mcf_auto_release_event(event):
    """Submit one exact due MCF order through the shared governed process."""
    payload = dict(event.get("payload") or {})

    marketplace_order_row_id = _safe_int(
        payload.get("marketplace_order_row_id")
    )

    if marketplace_order_row_id is None:
        return {
            "success": False,
            "verified": False,
            "aligned": False,
            "skipped": True,
            "reason": "mcf_marketplace_order_row_id_required",
            "database_touched": False,
        }

    from governed_mcf_routes import (
        run_governed_mcf_submission,
    )

    result = run_governed_mcf_submission(
        marketplace_order_row_id,
        auto_release=True,
        form_data={},
        actor_user=None,
    )

    success = bool(result.get("success"))
    skipped = bool(result.get("skipped"))

    retry_count = _safe_int(
        payload.get("mcf_auto_retry_count"),
        0,
    ) or 0

    retry_queued = False
    retry_after = None

    if not success and not skipped and retry_count < 3:
        retry_after = datetime.utcnow() + timedelta(minutes=15)

        retry_event = dict(event)
        retry_payload = dict(payload)
        retry_payload["mcf_auto_retry_count"] = retry_count + 1

        retry_event["payload"] = retry_payload
        retry_event["verify_after"] = retry_after

        notify_governed_runtime_work(
            source=(
                event.get("source")
                or "warehouse_mcf_one_hour_release"
            ),
            event=retry_event,
        )

        retry_queued = True

    return {
        **result,
        "verified": True,
        "aligned": success or skipped,
        "event_type": "mcf_auto_release",
        "mcf_auto_retry_count": retry_count,
        "mcf_auto_retry_queued": retry_queued,
        "mcf_auto_retry_after": (
            retry_after.isoformat()
            if retry_after
            else None
        ),
        "database_touched": True,
        "full_scan_started": False,
        "recent_order_import_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
    }


def _verify_webhook_event(event):
    """Verify only the exact identity and alignment supplied by the webhook."""
    if not event.get("scope_present"):
        return {
            "verified": False,
            "skipped": True,
            "reason": "webhook_scope_required",
            "database_touched": False,
        }

    if event.get("warehouse_stock_id") and event.get("listing_ids"):
        from services.governed_webhook_alignment import (
            verify_existing_webhook_alignment,
        )

        result = verify_existing_webhook_alignment(event)
    else:
        event_type = str(event.get("event_type") or "").lower()
        marketplace = str(event.get("marketplace") or "").lower()

        payload = event.get("payload") or {}
        notification = (
            payload.get("Payload", {})
            .get("OrderChangeNotification", {})
        )
        summary = notification.get("Summary", {})

        fulfillment_type = str(
            summary.get("FulfillmentType")
            or summary.get("fulfillmentType")
            or payload.get("fulfillment_type")
            or payload.get("fulfillmentType")
            or ""
        ).strip().upper()

        amazon_fba_event = bool(
            marketplace in {"amazon", "amazon_fba"}
            and event.get("seller_sku")
            and fulfillment_type in {"AFN", "FBA", "AMAZON"}
        )

        if amazon_fba_event:
            result = _verify_exact_fba(event)
        elif event.get("order_id") or "order" in event_type:
            result = _verify_exact_order(event)
        elif "fba" in event_type or "afn" in event_type or marketplace == "amazon_fba":
            result = _verify_exact_fba(event)
        else:
            result = _verify_exact_listing(event)

    return {
        **result,
        "source": event.get("source"),
        "store_id": event.get("store_id"),
        "full_scan_started": False,
        "recent_order_import_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
    }


def _pop_due_events(now=None):
    now = now or datetime.utcnow()
    due = []
    with _pending_events_lock:
        remaining = deque()
        while _pending_events:
            event = _pending_events.popleft()
            if event["verify_after"] <= now:
                due.append(event)
            else:
                remaining.append(event)
        _pending_events.extend(remaining)
        if not _pending_events:
            _pending_notification_event.clear()
    return due


def _seconds_until_next_due(default=LIGHT_RECONCILE_SECONDS):
    with _pending_events_lock:
        if not _pending_events:
            return default
        due_at = min(event["verify_after"] for event in _pending_events)
    return max(0.0, (due_at - datetime.utcnow()).total_seconds())


def _run_light_reconcile_cycle(events=None, source="webhook_verification_15m"):
    """Verify only webhook-provided identities; never perform broad imports."""
    global _last_light_reconcile, _last_verification_result

    events = list(events or [])
    results = []

    for event in events:
        event_type = str(
            event.get("event_type") or ""
        ).strip().lower()

        if event_type == "mcf_auto_release":
            result = _execute_mcf_auto_release_event(event)
        else:
            result = _verify_webhook_event(event)

        results.append(result)
    _last_light_reconcile = datetime.utcnow()
    _last_verification_result = {
        "success": True,
        "governed": True,
        "source": source,
        "events_received": len(events),
        "events_verified": sum(1 for item in results if item.get("verified")),
        "events_aligned": sum(1 for item in results if item.get("aligned")),
        "full_scan_started": False,
        "recent_order_import_started": False,
        "order_stock_bridge_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
        "results": results,
    }
    _safe_log(
        "15-minute webhook alignment verification complete "
        f"events={len(events)} verified={_last_verification_result['events_verified']}"
    )
    return _last_verification_result


def _run_full_sync_cycle():
    global _last_full_sync
    run_governed_marketplace_import_refresh(source="full_sync_8h_recovery")
    _last_full_sync = datetime.utcnow()


def _amazon_sqs_consumer_enabled() -> bool:
    if not _truthy(os.getenv("ENABLE_AMAZON_SQS_CONSUMER", "true"), True):
        return False

    return bool(
        _clean(os.getenv("AMAZON_SQS_QUEUE_URL"))
        or _clean(os.getenv("AMAZON_SQS_QUEUE_NAME"))
    )

def _amazon_sqs_connection():
    """Resolve and cache the configured Amazon notification SQS queue."""
    global _amazon_sqs_client, _amazon_sqs_queue_url

    if _amazon_sqs_client is not None and _amazon_sqs_queue_url:
        return _amazon_sqs_client, _amazon_sqs_queue_url

    import boto3

    region = (
        _clean(os.getenv("AWS_REGION"))
        or _clean(os.getenv("AWS_DEFAULT_REGION"))
        or "eu-west-2"
    )

    client = boto3.client("sqs", region_name=region)
    queue_url = _clean(os.getenv("AMAZON_SQS_QUEUE_URL"))

    if not queue_url:
        queue_name = _clean(os.getenv("AMAZON_SQS_QUEUE_NAME"))
        if not queue_name:
            raise RuntimeError(
                "AMAZON_SQS_QUEUE_URL or AMAZON_SQS_QUEUE_NAME is required"
            )

        queue_url = client.get_queue_url(
            QueueName=queue_name,
        )["QueueUrl"]

    _amazon_sqs_client = client
    _amazon_sqs_queue_url = queue_url
    return client, queue_url

def _amazon_sqs_message_payload(message: dict) -> dict:
    body = message.get("Body") or ""

    try:
        decoded = json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Amazon SQS message body is not valid JSON") from exc

    if not isinstance(decoded, dict):
        raise ValueError("Amazon SQS message body must decode to a JSON object")

    return decoded

def _send_amazon_sqs_message_to_governed_intake(app, payload: dict) -> dict:
    """
    Transport bridge only:

    SQS -> existing governed_marketplace_webhook_intake("amazon")

    The existing route remains the single authority for capture, fuse checks,
    processing, stock mutation and correction.
    """
    from governed_routes import governed_marketplace_webhook_intake

    with app.test_request_context(
        "/governed/webhooks/amazon",
        method="POST",
        json=payload,
        headers={
            "X-BT38-Notification-Transport": "amazon_sqs",
        },
    ):
        result = governed_marketplace_webhook_intake("amazon")

    if isinstance(result, tuple):
        response = result[0]
        status_code = int(result[1])
    else:
        response = result
        status_code = int(getattr(response, "status_code", 200))

    response_payload = None
    if hasattr(response, "get_json"):
        response_payload = response.get_json(silent=True)

    return {
        "success": 200 <= status_code < 300,
        "status_code": status_code,
        "response": response_payload,
    }

def _poll_amazon_sqs_once(app, wait_seconds: int = 20) -> dict:
    """
    Long-poll Amazon SQS without touching Neon while the queue is empty.

    A message is deleted only after the existing governed intake returns a
    successful HTTP status. Failed messages remain in SQS for retry/DLQ policy.
    """
    if not _amazon_sqs_consumer_enabled():
        return {
            "enabled": False,
            "received": 0,
            "deleted": 0,
            "failed": 0,
        }

    client, queue_url = _amazon_sqs_connection()

    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=max(0, min(int(wait_seconds), 20)),
        VisibilityTimeout=120,
        MessageAttributeNames=["All"],
        AttributeNames=["All"],
    )

    messages = list(response.get("Messages") or [])
    deleted = 0
    failed = 0

    for message in messages:
        receipt_handle = message.get("ReceiptHandle")

        try:
            payload = _amazon_sqs_message_payload(message)
            result = _send_amazon_sqs_message_to_governed_intake(
                app,
                payload,
            )

            if not result.get("success"):
                raise RuntimeError(
                    "Governed Amazon intake returned "
                    f"HTTP {result.get('status_code')}: "
                    f"{result.get('response')}"
                )

            if not receipt_handle:
                raise RuntimeError("Amazon SQS receipt handle is missing")

            client.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
            )
            deleted += 1

        except Exception as exc:
            failed += 1
            _safe_error(
                "Amazon SQS notification processing failed "
                f"message_id={message.get('MessageId')}",
                exc,
            )

    return {
        "enabled": True,
        "received": len(messages),
        "deleted": deleted,
        "failed": failed,
    }


def _recover_mcf_auto_release_events(app) -> dict:
    """Re-arm recent exact MCF release events after a process restart.

    MarketplaceOrder remains the durable authority. This performs one bounded
    startup recovery query and queues exact order identities only. It does not
    submit MCF directly and does not create a second MCF workflow.
    """
    from models import MarketplaceOrder

    now = datetime.utcnow()
    recovery_since = now - timedelta(hours=48)

    with app.app_context():
        query = (
            MarketplaceOrder.query
            .filter(MarketplaceOrder.created_at >= recovery_since)
            .order_by(
                MarketplaceOrder.created_at.asc(),
                MarketplaceOrder.id.asc(),
            )
            .limit(250)
        )

        if hasattr(MarketplaceOrder, "mcf_order_id"):
            query = query.filter(
                MarketplaceOrder.mcf_order_id.is_(None)
            )

        if hasattr(MarketplaceOrder, "mcf_queue_hidden"):
            query = query.filter(
                MarketplaceOrder.mcf_queue_hidden == False  # noqa: E712
            )

        rows = query.all()

        cancelled_statuses = {
            "cancelled",
            "canceled",
            "cancellation",
            "cancel_requested",
        }

        seen_orders = set()
        queued = 0
        skipped = 0

        for row in rows:
            store = getattr(row, "store", None)
            platform = str(
                getattr(store, "platform", "") or ""
            ).strip().lower()

            # Amazon-origin orders must never be sent back through Amazon MCF.
            if not platform or "amazon" in platform:
                skipped += 1
                continue

            status = str(
                getattr(row, "status", "") or ""
            ).strip().lower()

            if status in cancelled_statuses:
                skipped += 1
                continue

            if getattr(row, "created_at", None) is None:
                skipped += 1
                continue

            order_key = (
                getattr(row, "store_id", None),
                getattr(row, "marketplace_order_id", None),
            )

            # Multi-line orders use one anchor event only.
            if order_key in seen_orders:
                continue
            seen_orders.add(order_key)

            # Do not re-arm an order when any line already belongs to an MCF
            # record. The initial bounded query filters individual rows, but
            # completion authority belongs to the complete marketplace order.
            completed_line = (
                MarketplaceOrder.query
                .filter(
                    MarketplaceOrder.store_id == row.store_id,
                    MarketplaceOrder.marketplace_order_id
                    == row.marketplace_order_id,
                    MarketplaceOrder.mcf_order_id.isnot(None),
                )
                .first()
            )

            if completed_line is not None:
                skipped += 1
                continue

            release_at = row.created_at + timedelta(hours=1)

            notify_governed_runtime_work(
                source="warehouse_mcf_startup_recovery",
                event={
                    "event_type": "mcf_auto_release",
                    "marketplace": platform,
                    "store_id": row.store_id,
                    "order_id": row.marketplace_order_id,
                    "warehouse_stock_id": getattr(
                        row,
                        "warehouse_stock_id",
                        None,
                    ),
                    # Overdue orders execute immediately. Orders still inside
                    # the cancellation window retain their original due time.
                    "verify_after": max(release_at, now),
                    "payload": {
                        "marketplace_order_row_id": row.id,
                        "idempotency_key": getattr(
                            row,
                            "idempotency_key",
                            None,
                        ),
                        "startup_recovered": True,
                    },
                },
            )
            queued += 1

    return {
        "success": True,
        "governed": True,
        "bounded": True,
        "recovery_hours": 48,
        "rows_examined_max": 250,
        "orders_queued": queued,
        "orders_skipped": skipped,
        "full_scan_started": False,
        "marketplace_import_started": False,
        "warehouse_scan_started": False,
    }

def _engine_loop(app):
    """
    One governed runtime thread:

    1. Long-poll Amazon SQS as transport only.
    2. Send received messages through the existing governed webhook intake.
    3. Run only due 15-minute exact-scope verification events.
    4. Never scan Neon while idle.
    """
    global _last_full_sync

    _safe_log("Webhook-scoped runtime loop started")

    # Production Flask app creates a real application context.
    # Deployment-contract FakeApp uses nullcontext and must never touch DB.
    try:
        with app.app_context():
            runtime_database_enabled = has_app_context()
    except Exception:
        runtime_database_enabled = False

    if DURABLE_RUNTIME_JOB_PATH_ENABLED and runtime_database_enabled:
        # RETIRED: database runtime jobs remain unreachable.
        # Initialise the durable exact-event table once per runtime-owner
        # production process. Never execute DDL during idle polling.
        try:
            with app.app_context():
                from services.governed_runtime_job_store import (
                    ensure_runtime_job_table,
                )

                ensure_runtime_job_table()

                from services.governed_runtime_job_store import (
                    load_pending_runtime_job_hints,
                )

                recovered_hints = load_pending_runtime_job_hints(
                    limit=250,
                )

                with _pending_events_lock:
                    for recovered in recovered_hints:
                        key = _event_key(recovered)

                        if not any(
                            _event_key(existing) == key
                            for existing in _pending_events
                        ):
                            _pending_events.append(recovered)

                    if _pending_events:
                        _pending_notification_event.set()

                _safe_log(
                    "Durable runtime job recovery complete "
                    f"pending={len(recovered_hints)}"
                )
        except Exception as exc:
            _safe_error(
                "Durable runtime job table initialisation failed",
                exc,
            )

        # Production-only bounded MCF startup recovery.
        try:
            recovery_result = _recover_mcf_auto_release_events(app)
            _safe_log(
                "MCF startup recovery complete "
                f"queued={recovery_result.get('orders_queued', 0)} "
                f"skipped={recovery_result.get('orders_skipped', 0)}"
            )
        except Exception as exc:
            _safe_error("MCF startup recovery failed", exc)

    hydration_enabled = _truthy(
        os.getenv("ENABLE_GOVERNED_8H_HYDRATION", "false"),
        False,
    )

    while not _stop_event.is_set():
        try:
            seconds_until_due = _seconds_until_next_due()

            if _amazon_sqs_consumer_enabled():
                sqs_wait = max(
                    0,
                    min(20, int(seconds_until_due)),
                )
                _poll_amazon_sqs_once(
                    app,
                    wait_seconds=sqs_wait,
                )
            else:
                _pending_notification_event.wait(
                    timeout=seconds_until_due,
                )

            if _stop_event.is_set():
                break

            # Check Neon only when an exact in-memory due hint exists.
            # The DB remains the durable source; the deque is only a wake timer.
            due_hints = _pop_due_events()

            if due_hints:
                # One governed execution path. The exact event already held in
                # the runtime queue is passed directly to reconciliation.
                _run_light_reconcile_cycle(
                    events=due_hints,
                    source="governed_exact_event",
                )

            if hydration_enabled:
                now = datetime.utcnow()
                if (
                    _last_full_sync is None
                    or (now - _last_full_sync).total_seconds()
                    >= FULL_SYNC_SECONDS
                ):
                    with app.app_context():
                        _run_full_sync_cycle()

        except Exception as exc:
            _safe_error("Engine loop error", exc)

            # Prevent a failed AWS connection from creating a tight loop.
            _stop_event.wait(timeout=30)

    _safe_log("Webhook-scoped runtime loop stopped")


def _acquire_runtime_owner_lock() -> bool:
    global _runtime_lock_handle
    if _runtime_lock_handle is not None:
        return True

    try:
        import fcntl

        handle = open(_RUNTIME_LOCK_PATH, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} started_at={datetime.utcnow().isoformat()}Z\n")
        handle.flush()
        _runtime_lock_handle = handle
        return True
    except Exception as exc:
        _safe_error("Governed runtime owner lock failed", exc)
        return False


def start_governed_runtime_engine(app):
    global _started, _started_at

    with _status_lock:
        if _started:
            return False
        if not _truthy(os.getenv("ENABLE_GOVERNED_RUNTIME_ENGINE", "true"), True):
            return False
        if not _acquire_runtime_owner_lock():
            return False
        _started = True
        _started_at = datetime.utcnow()

    try:
        thread = threading.Thread(
            target=_engine_loop,
            args=(app,),
            daemon=True,
            name="BT38GovernedRuntimeEngine",
        )
        thread.start()
        return True
    except Exception as exc:
        _safe_error("Governed runtime engine failed to start", exc)
        return False


def get_governed_runtime_status():
    now = datetime.utcnow()
    engine_live = bool(_started)
    with _pending_events_lock:
        pending_count = len(_pending_events)
        next_due = min(
            (event["verify_after"] for event in _pending_events),
            default=None,
        )

    return {
        "engine_started": engine_live,
        "runtime_mode": "EVENT-DRIVEN GOVERNED" if engine_live else "MANUAL GOVERNED",
        "execution_mode": "EVENT + MANUAL GOVERNED" if engine_live else "MANUAL ONLY",
        "workers_running": engine_live,
        "schedulers_running": engine_live,
        "queue_consumers_running": engine_live,
        "started_at": _started_at.isoformat() if _started_at else None,
        "last_full_sync": _last_full_sync.isoformat() if _last_full_sync else None,
        "last_light_reconcile": (
            _last_light_reconcile.isoformat() if _last_light_reconcile else None
        ),
        "last_marketplace_import": (
            _last_marketplace_import.isoformat() if _last_marketplace_import else None
        ),
        "last_fba_import": _last_fba_import.isoformat() if _last_fba_import else None,
        "last_ebay_import": _last_ebay_import.isoformat() if _last_ebay_import else None,
        "next_full_sync_seconds": (
            max(0, FULL_SYNC_SECONDS - int((now - _last_full_sync).total_seconds()))
            if _last_full_sync
            else 0
        ),
        "next_light_reconcile_seconds": (
            max(0, int((next_due - now).total_seconds())) if next_due else 0
        ),
        "last_error": _last_error,
        "runtime_heartbeat": None,
        "runtime_status_source": "process_memory",
        "pending_notification": pending_count > 0,
        "pending_webhook_verifications": pending_count,
        "last_event_at": _last_event_at.isoformat() if _last_event_at else None,
        "last_event_source": _last_event_source,
        "last_verification_result": _last_verification_result,
        "automatic_8h_hydration_enabled": _truthy(
            os.getenv("ENABLE_GOVERNED_8H_HYDRATION", "false"),
            False,
        ),
        "idle_db_activity": False,
    }
