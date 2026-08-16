"""
BT38 GOVERNED RUNTIME ENGINE

Runtime contract:
- Webhooks perform immediate targeted work through the existing governed path.
- Exact due events are process-memory only and wake the single Gunicorn process.
- The 15-minute path verifies only identities supplied by events.
- MCF uses the same exact event for lifecycle continuation/recovery.
- No recent-order, Warehouse, group, listing, or marketplace-wide scan is
  allowed during normal event processing.
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

_pending_notification_event = threading.Event()

# The historical DB runtime-job path remains retired. Exact events stay in the
# single governed process; bounded startup recovery reconstructs only exact
# MCF/FBA work that must survive a deployment/restart.
DURABLE_RUNTIME_JOB_PATH_ENABLED = False
_stop_event = threading.Event()
_pending_events = deque()
_pending_events_lock = threading.Lock()

_amazon_sqs_client = None
_amazon_sqs_queue_url = None


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


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
    raw.update(
        {
            key: value
            for key, value in identifiers.items()
            if value is not None
        }
    )

    event_type = _clean(raw.get("event_type") or raw.get("type"))
    marketplace = _clean(raw.get("marketplace") or raw.get("platform"))
    store_id = _safe_int(raw.get("store_id"))
    warehouse_stock_id = _safe_int(raw.get("warehouse_stock_id"))
    group_id = _safe_int(
        raw.get("group_id")
        or raw.get("master_product_group_id")
    )
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

    product_linking_group_push = bool(
        event_type == "product_linking_group_push"
        and group_id is not None
    )

    scope_present = bool(
        product_linking_group_push
        or (
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


def notify_governed_runtime_work(
    source: str = "webhook",
    event=None,
    **identifiers,
):
    """Queue one exact in-process governed event."""
    global _last_event_at, _last_event_source

    item = _normalise_webhook_event(
        source,
        event=event,
        **identifiers,
    )

    _last_event_at = item["received_at"]
    _last_event_source = item["source"]

    effective_verify_after = item["verify_after"]

    with _pending_events_lock:
        key = _event_key(item)
        for queued in _pending_events:
            if _event_key(queued) == key:
                # Duplicate notification: preserve the earliest deadline.
                # A repeat event may refresh payload truth, but it must never
                # postpone or create a second copy of the same pending work.
                queued["verify_after"] = min(
                    queued["verify_after"],
                    item["verify_after"],
                )
                queued["payload"] = (
                    item.get("payload")
                    or queued.get("payload")
                )
                queued["expected_quantity"] = item.get(
                    "expected_quantity"
                )
                effective_verify_after = queued["verify_after"]
                break
        else:
            _pending_events.append(item)

    _pending_notification_event.set()

    return {
        "queued": True,
        "durable": False,
        "status": "GOVERNED_MEMORY_EVENT",
        "verify_after": effective_verify_after.isoformat(),
        "scoped": item["scope_present"],
    }


def _config_on_for_explicit_work(
    key: str,
    default: bool = False,
) -> bool:
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
    if not _config_on_for_explicit_work(
        "runtime_import_enabled",
        True,
    ):
        return False
    if not _config_on_for_explicit_work(
        "marketplace_import_enabled",
        True,
    ):
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
        stores = [
            store
            for store in stores
            if int(store.id) == int(store_id)
        ]

    for store in stores:
        platform = str(store.platform or "").strip().lower()
        try:
            if "amazon" in platform:
                from services.governed_amazon_listing_fulfillment_refresh import (
                    ensure_governed_amazon_listing_notification_subscriptions,
                    run_governed_amazon_listing_fulfillment_refresh,
                )

                # Reconcile the existing Amazon listing notification topics
                # against the existing destination before recovery hydration.
                # This does not create a destination, importer or scheduler.
                # If Amazon refuses a topic, bounded Listings Items recovery
                # must still run so missed listings are recovered.
                try:
                    listing_subscriptions = (
                        ensure_governed_amazon_listing_notification_subscriptions(
                            store_id=store.id,
                        )
                    )
                except Exception as exc:
                    listing_subscriptions = {
                        "success": False,
                        "governed": True,
                        "store_id": store.id,
                        "reason": (
                            "amazon_listing_subscription_reconcile_failed"
                        ),
                        "error": str(exc),
                        "destination_created": False,
                    }

                # Amazon listing discovery is marketplace listing recovery,
                # not FBA inventory recovery. It must continue even when the
                # FBA inventory fuse is disabled.
                listing_fulfillment = (
                    run_governed_amazon_listing_fulfillment_refresh(
                        store_id=store.id,
                    )
                )

                if bool(
                    getattr(store, "fba_import_enabled", False)
                ):
                    from services.governed_amazon_inventory_import import (
                        run_governed_amazon_inventory_import,
                    )

                    result = run_governed_amazon_inventory_import(
                        store_id=store.id,
                        full_refresh=True,
                        source=source,
                    )
                    _last_fba_import = datetime.utcnow()
                else:
                    result = {
                        "success": True,
                        "governed": True,
                        "skipped": True,
                        "reason": "fba_import_disabled",
                    }

                results.append({
                    "store_id": store.id,
                    "platform": store.platform,
                    "success": bool(
                        listing_fulfillment.get("success", True)
                    ),
                    "result": result,
                    "listing_subscriptions": listing_subscriptions,
                    "listing_fulfillment": listing_fulfillment,
                })
                continue

            if "ebay" in platform:
                from services.governed_ebay_inventory_import import (
                    run_governed_ebay_inventory_import,
                )
                from services.governed_marketplace_order_import import (
                    run_governed_marketplace_order_import,
                )

                result = run_governed_ebay_inventory_import(
                    store_id=store.id
                )
                order_recovery = run_governed_marketplace_order_import(
                    store_id=store.id,
                    source=f"{source}:ebay_order_recovery",
                )
                _last_ebay_import = datetime.utcnow()
                results.append({
                    "store_id": store.id,
                    "platform": store.platform,
                    "success": bool(
                        order_recovery.get("success", True)
                    ),
                    "result": result,
                    "order_recovery": order_recovery,
                })
                continue

            results.append({
                "store_id": store.id,
                "platform": store.platform,
                "skipped": True,
                "reason": "unsupported_marketplace_import",
            })
        except Exception as exc:
            _safe_error(
                f"marketplace hydration failed store_id={store.id}",
                exc,
            )
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
        (
            "external_order_id",
            "marketplace_order_id",
            "amazon_order_id",
            "order_id",
        ),
    )
    if order_column is None or not event.get("order_id"):
        return {
            "verified": False,
            "reason": "exact_order_identity_unavailable",
        }

    query = MarketplaceOrder.query.filter(
        order_column == event["order_id"]
    )
    if hasattr(MarketplaceOrder, "store_id"):
        query = query.filter(
            MarketplaceOrder.store_id == event["store_id"]
        )

    row = query.first()
    return {
        "verified": row is not None,
        "object": "MarketplaceOrder",
        "identity_field": order_field,
        "identity": event["order_id"],
        "rows_examined_max": 1,
    }


def _verify_exact_fba(event):
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
        seller_skus=[seller_sku]
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
        response_object = (
            response[0]
            if isinstance(response, tuple)
            else response
        )
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
            query = query.filter(
                getattr(MarketplaceListing, field) == value
            )
            identity = value
            identity_field = field
            break

    if identity is None:
        return {
            "verified": False,
            "reason": "exact_listing_identity_unavailable",
        }

    row = query.first()
    return {
        "verified": row is not None,
        "object": "MarketplaceListing",
        "identity_field": identity_field,
        "identity": identity,
        "rows_examined_max": 1,
    }


def _execute_mcf_auto_release_event(event):
    """Continue one exact MCF lifecycle from current DB state."""
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

    from extensions import db
    from models import MarketplaceOrder
    from governed_mcf_routes import (
        CANCELLED_ORDER_STATUSES,
        run_governed_mcf_marketplace_dispatch,
        run_governed_mcf_submission,
    )

    order = db.session.get(
        MarketplaceOrder,
        marketplace_order_row_id,
    )
    if order is None:
        return {
            "success": False,
            "verified": True,
            "aligned": False,
            "skipped": True,
            "reason": "mcf_marketplace_order_missing",
            "database_touched": True,
        }

    order_lines = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == order.store_id,
            MarketplaceOrder.marketplace_order_id
            == order.marketplace_order_id,
        )
        .all()
    )
    if any(
        str(line.status or "").strip().lower()
        in CANCELLED_ORDER_STATUSES
        for line in order_lines
    ):
        return {
            "success": True,
            "verified": True,
            "aligned": True,
            "skipped": True,
            "reason": "source_order_cancelled",
            "database_touched": True,
        }

    mcf = next(
        (
            line.mcf_order
            for line in order_lines
            if line.mcf_order_id
        ),
        None,
    )

    if mcf is not None and str(mcf.status or "").lower() != "failed":
        result = run_governed_mcf_marketplace_dispatch(
            marketplace_order_row_id,
            actor_user=None,
            automatic=True,
        )
        return {
            **result,
            "verified": True,
            "aligned": bool(
                result.get("success")
                or result.get("skipped")
            ),
            "event_type": "mcf_auto_release",
            "mcf_phase": "dispatch",
            "database_touched": True,
            "full_scan_started": False,
            "recent_order_import_started": False,
            "warehouse_scan_started": False,
            "marketplace_hydration_started": False,
        }

    result = run_governed_mcf_submission(
        marketplace_order_row_id,
        auto_release=True,
        form_data={},
        actor_user=None,
    )

    success = bool(result.get("success"))
    skipped = bool(result.get("skipped"))
    retry_count = (
        _safe_int(payload.get("mcf_auto_retry_count"), 0)
        or 0
    )
    retry_queued = False
    retry_after = None

    overdue_recovery_dispatch = None
    if success and bool(payload.get("startup_recovered")):
        db.session.expire_all()
        refreshed = db.session.get(
            MarketplaceOrder,
            marketplace_order_row_id,
        )
        legacy_base = (
            getattr(refreshed, "marketplace_created_at", None)
            or getattr(refreshed, "created_at", None)
        )
        if (
            legacy_base is not None
            and datetime.utcnow()
            >= legacy_base + timedelta(hours=1)
        ):
            overdue_recovery_dispatch = (
                run_governed_mcf_marketplace_dispatch(
                    marketplace_order_row_id,
                    actor_user=None,
                    automatic=True,
                )
            )

    if not success and not skipped and retry_count < 3:
        retry_after = datetime.utcnow() + timedelta(minutes=15)
        retry_event = dict(event)
        retry_payload = dict(payload)
        retry_payload["mcf_auto_retry_count"] = (
            retry_count + 1
        )
        retry_event["payload"] = retry_payload
        retry_event["verify_after"] = retry_after
        notify_governed_runtime_work(
            source=(
                event.get("source")
                or "warehouse_mcf_immediate_submit"
            ),
            event=retry_event,
        )
        retry_queued = True

    aligned = success or skipped
    if overdue_recovery_dispatch is not None:
        aligned = aligned and bool(
            overdue_recovery_dispatch.get("success")
            or overdue_recovery_dispatch.get("skipped")
        )

    return {
        **result,
        "verified": True,
        "aligned": aligned,
        "event_type": "mcf_auto_release",
        "mcf_phase": "submit",
        "mcf_auto_retry_count": retry_count,
        "mcf_auto_retry_queued": retry_queued,
        "mcf_auto_retry_after": (
            retry_after.isoformat()
            if retry_after
            else None
        ),
        "overdue_recovery_dispatch": overdue_recovery_dispatch,
        "database_touched": True,
        "full_scan_started": False,
        "recent_order_import_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
    }


def _execute_product_linking_group_push_event(event):
    """Execute one already-committed Product Linking group push.

    Relationship mutation has already committed before this event is queued.
    This reuses the existing governed runtime and existing push authority.
    """
    group_id = _safe_int(event.get("group_id"))

    if group_id is None:
        return {
            "success": False,
            "verified": False,
            "aligned": False,
            "reason": "product_linking_group_id_required",
            "database_touched": False,
        }

    from services.governed_push_execution import push_group_listings

    result = push_group_listings(
        group_id=int(group_id),
        actor="product_linking_runtime",
        source=(
            event.get("source")
            or "product_linking_committed_relationship"
        ),
        actor_user=None,
    )

    success = bool(
        result.get("ok")
        or result.get("success")
    )

    return {
        **result,
        "verified": True,
        "aligned": success,
        "event_type": "product_linking_group_push",
        "group_id": int(group_id),
        "database_touched": True,
        "full_scan_started": False,
        "recent_order_import_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
    }


def _verify_webhook_event(event):
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
        event_type = str(
            event.get("event_type") or ""
        ).lower()
        marketplace = str(
            event.get("marketplace") or ""
        ).lower()
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
        elif (
            "fba" in event_type
            or "afn" in event_type
            or marketplace == "amazon_fba"
        ):
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


def _seconds_until_next_due(
    default=LIGHT_RECONCILE_SECONDS,
):
    with _pending_events_lock:
        if not _pending_events:
            return default
        due_at = min(
            event["verify_after"]
            for event in _pending_events
        )
    return max(
        0.0,
        (due_at - datetime.utcnow()).total_seconds(),
    )


def _run_light_reconcile_cycle(
    events=None,
    source="webhook_verification_15m",
):
    global _last_light_reconcile, _last_verification_result

    events = list(events or [])
    results = []
    for event in events:
        event_type = str(
            event.get("event_type") or ""
        ).strip().lower()
        if event_type == "mcf_auto_release":
            result = _execute_mcf_auto_release_event(event)
        elif event_type == "product_linking_group_push":
            result = _execute_product_linking_group_push_event(event)
        else:
            result = _verify_webhook_event(event)
        results.append(result)

    _last_light_reconcile = datetime.utcnow()
    _last_verification_result = {
        "success": True,
        "governed": True,
        "source": source,
        "events_received": len(events),
        "events_verified": sum(
            1
            for item in results
            if item.get("verified")
        ),
        "events_aligned": sum(
            1
            for item in results
            if item.get("aligned")
        ),
        "full_scan_started": False,
        "recent_order_import_started": False,
        "order_stock_bridge_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
        "results": results,
    }
    _safe_log(
        "Exact event alignment complete "
        f"events={len(events)} "
        f"verified={_last_verification_result['events_verified']}"
    )
    return _last_verification_result


def _run_full_sync_cycle():
    global _last_full_sync
    run_governed_marketplace_import_refresh(
        source="full_sync_8h_recovery"
    )
    _last_full_sync = datetime.utcnow()


def _amazon_sqs_consumer_enabled() -> bool:
    if not _truthy(
        os.getenv("ENABLE_AMAZON_SQS_CONSUMER", "true"),
        True,
    ):
        return False
    return bool(
        _clean(os.getenv("AMAZON_SQS_QUEUE_URL"))
        or _clean(os.getenv("AMAZON_SQS_QUEUE_NAME"))
    )


def _amazon_sqs_connection():
    global _amazon_sqs_client, _amazon_sqs_queue_url
    if (
        _amazon_sqs_client is not None
        and _amazon_sqs_queue_url
    ):
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
                "AMAZON_SQS_QUEUE_URL or "
                "AMAZON_SQS_QUEUE_NAME is required"
            )
        queue_url = client.get_queue_url(
            QueueName=queue_name
        )["QueueUrl"]

    _amazon_sqs_client = client
    _amazon_sqs_queue_url = queue_url
    return client, queue_url


def _amazon_sqs_message_payload(message: dict) -> dict:
    body = message.get("Body") or ""
    try:
        decoded = json.loads(body)
    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError(
            "Amazon SQS message body is not valid JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise ValueError(
            "Amazon SQS message body must decode to a JSON object"
        )
    return decoded


def _send_amazon_sqs_message_to_governed_intake(
    app,
    payload: dict,
) -> dict:
    from governed_routes import governed_marketplace_webhook_intake

    with app.test_request_context(
        "/governed/webhooks/amazon",
        method="POST",
        json=payload,
        headers={
            "X-BT38-Notification-Transport": "amazon_sqs"
        },
    ):
        result = governed_marketplace_webhook_intake(
            "amazon"
        )

    if isinstance(result, tuple):
        response = result[0]
        status_code = int(result[1])
    else:
        response = result
        status_code = int(
            getattr(response, "status_code", 200)
        )

    response_payload = (
        response.get_json(silent=True)
        if hasattr(response, "get_json")
        else None
    )
    return {
        "success": 200 <= status_code < 300,
        "status_code": status_code,
        "response": response_payload,
    }


def _poll_amazon_sqs_once(
    app,
    wait_seconds: int = 20,
) -> dict:
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
        WaitTimeSeconds=max(
            0,
            min(int(wait_seconds), 20),
        ),
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
                raise RuntimeError(
                    "Amazon SQS receipt handle is missing"
                )
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



def _recover_fba_verification_events(app) -> dict:
    """Recover only unsatisfied exact FBA event phases after restart."""
    from extensions import db
    from sqlalchemy import text

    now = datetime.utcnow()
    recovery_since = now - timedelta(hours=24)

    with app.app_context():
        rows = (
            db.session.execute(
                text(
                    """
                    SELECT
                        mo.id,
                        mo.store_id,
                        mo.marketplace_order_id,
                        mo.sku,
                        mo.warehouse_stock_id,
                        COALESCE(
                            mo.processed_at,
                            mo.created_at
                        ) AS event_at,
                        fba.last_synced_at
                    FROM marketplace_orders AS mo
                    JOIN stores AS s
                      ON s.id = mo.store_id
                    LEFT JOIN amazon_fba_inventory AS fba
                      ON fba.store_id = mo.store_id
                     AND fba.seller_sku = mo.sku
                    WHERE mo.created_at >= :recovery_since
                      AND LOWER(COALESCE(s.platform, ''))
                          LIKE '%amazon%'
                      AND UPPER(COALESCE(
                          mo.fulfillment_type, ''
                      )) IN ('FBA', 'AFN', 'AMAZON')
                      AND mo.sku IS NOT NULL
                    ORDER BY
                        mo.created_at ASC,
                        mo.id ASC
                    LIMIT 250
                    """
                ),
                {"recovery_since": recovery_since},
            )
            .mappings()
            .all()
        )

        seen = set()
        settlement_queued = 0
        light_queued = 0
        already_satisfied = 0
        duplicates_skipped = 0

        for row in rows:
            identity = (
                row["store_id"],
                row["marketplace_order_id"],
                row["sku"],
            )
            if identity in seen:
                duplicates_skipped += 1
                continue
            seen.add(identity)

            event_at = row["event_at"]
            if event_at is None:
                continue

            last_synced_at = row["last_synced_at"]
            settlement_due = event_at + timedelta(seconds=90)
            light_due = event_at + timedelta(
                seconds=LIGHT_RECONCILE_SECONDS
            )

            base_event = {
                "event_type": "order_change",
                "marketplace": "amazon",
                "store_id": row["store_id"],
                "seller_sku": row["sku"],
                "order_id": row["marketplace_order_id"],
                "warehouse_stock_id": row[
                    "warehouse_stock_id"
                ],
                "payload": {
                    "fulfillment_type": "FBA",
                    "startup_recovered": True,
                },
            }

            # Recover the settlement phase only if DB truth proves
            # no exact FBA refresh has satisfied its original deadline.
            if (
                last_synced_at is None
                or last_synced_at < settlement_due
            ):
                settlement_event = dict(base_event)
                settlement_event["verify_after"] = settlement_due
                notify_governed_runtime_work(
                    source="webhook_amazon_settlement_recheck",
                    event=settlement_event,
                )
                settlement_queued += 1
            else:
                already_satisfied += 1

            # Independently recover the normal 15-minute event phase.
            # A refresh at or after its original deadline is sufficient
            # proof, so deployment never creates duplicate verification.
            if (
                last_synced_at is None
                or last_synced_at < light_due
            ):
                light_event = dict(base_event)
                light_event["verify_after"] = light_due
                notify_governed_runtime_work(
                    source="webhook_amazon_15m_reconcile",
                    event=light_event,
                )
                light_queued += 1
            else:
                already_satisfied += 1

    return {
        "success": True,
        "governed": True,
        "bounded": True,
        "recovery_hours": 24,
        "rows_examined_max": 250,
        "rows_examined": len(rows),
        "settlement_queued": settlement_queued,
        "light_queued": light_queued,
        "already_satisfied": already_satisfied,
        "duplicates_skipped": duplicates_skipped,
        "full_scan_started": False,
        "marketplace_import_started": False,
        "warehouse_scan_started": False,
    }


def _recover_mcf_auto_release_events(app) -> dict:
    """Rebuild only exact unfinished MCF lifecycle events after restart."""
    from models import MarketplaceOrder

    now = datetime.utcnow()
    recovery_since = now - timedelta(hours=48)

    with app.app_context():
        rows = (
            MarketplaceOrder.query
            .filter(MarketplaceOrder.created_at >= recovery_since)
            .order_by(
                MarketplaceOrder.created_at.asc(),
                MarketplaceOrder.id.asc(),
            )
            .limit(250)
            .all()
        )

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
            if not platform or "amazon" in platform:
                skipped += 1
                continue

            order_key = (
                getattr(row, "store_id", None),
                getattr(row, "marketplace_order_id", None),
            )
            if order_key in seen_orders:
                continue
            seen_orders.add(order_key)

            lines = (
                MarketplaceOrder.query
                .filter(
                    MarketplaceOrder.store_id == row.store_id,
                    MarketplaceOrder.marketplace_order_id
                    == row.marketplace_order_id,
                )
                .all()
            )

            if any(
                str(line.status or "").strip().lower()
                in cancelled_statuses
                for line in lines
            ):
                skipped += 1
                continue

            if any(
                bool(getattr(line, "mcf_queue_hidden", False))
                for line in lines
            ):
                skipped += 1
                continue

            if any(
                getattr(line, "shipped_at", None)
                for line in lines
            ):
                skipped += 1
                continue

            mcf = next(
                (
                    line.mcf_order
                    for line in lines
                    if line.mcf_order_id
                ),
                None,
            )

            if (
                mcf is not None
                and str(mcf.status or "").lower()
                in {"cancelled"}
            ):
                skipped += 1
                continue

            if (
                mcf is not None
                and str(mcf.status or "").lower()
                != "failed"
            ):
                accepted_at = (
                    getattr(
                        mcf,
                        "amazon_status_updated_at",
                        None,
                    )
                    or getattr(mcf, "updated_at", None)
                    or getattr(mcf, "created_at", None)
                    or now
                )
                due_at = max(
                    accepted_at + timedelta(hours=1),
                    now,
                )
                phase = "dispatch"
            else:
                due_at = now
                phase = "submit"

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
                    "verify_after": due_at,
                    "payload": {
                        "marketplace_order_row_id": row.id,
                        "idempotency_key": getattr(
                            row,
                            "idempotency_key",
                            None,
                        ),
                        "mcf_order_id": (
                            getattr(mcf, "id", None)
                            if mcf is not None
                            else None
                        ),
                        "phase": phase,
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
    global _last_full_sync

    _safe_log("Webhook-scoped runtime loop started")
    try:
        with app.app_context():
            runtime_database_enabled = has_app_context()
    except Exception:
        runtime_database_enabled = False

    # Historical durable-job recovery remains disabled and independent from
    # MCF lifecycle recovery.
    if DURABLE_RUNTIME_JOB_PATH_ENABLED and runtime_database_enabled:
        try:
            with app.app_context():
                from services.governed_runtime_job_store import (
                    ensure_runtime_job_table,
                    load_pending_runtime_job_hints,
                )

                ensure_runtime_job_table()
                recovered_hints = load_pending_runtime_job_hints(
                    limit=250
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

    # MCF startup recovery is part of the active governed lifecycle and must
    # not depend on the retired durable-job flag.
    if runtime_database_enabled:
        try:
            recovery_result = _recover_mcf_auto_release_events(
                app
            )
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

    # Recovery is due immediately after a process restart. Run it before the
    # first event wait; otherwise an idle process delays marketplace hydration
    # by the 15-minute notification timeout even though the recovery flag is
    # enabled. Subsequent cycles remain on the existing eight-hour interval.
    if hydration_enabled and runtime_database_enabled:
        try:
            with app.app_context():
                _run_full_sync_cycle()
        except Exception as exc:
            _safe_error("Startup marketplace hydration failed", exc)

    # Deployment/restart recovery for exact Amazon FBA events runs after
    # optional startup hydration. Fresh DB truth can therefore satisfy an
    # overdue phase without creating a duplicate marketplace verification.
    if runtime_database_enabled:
        try:
            recovery_result = _recover_fba_verification_events(app)
            _safe_log(
                "FBA event startup recovery complete "
                f"settlement={recovery_result.get('settlement_queued', 0)} "
                f"light={recovery_result.get('light_queued', 0)} "
                f"satisfied={recovery_result.get('already_satisfied', 0)}"
            )
        except Exception as exc:
            _safe_error("FBA event startup recovery failed", exc)

    while not _stop_event.is_set():
        try:
            # The Event is only a wake signal. Clear the previous edge before
            # calculating the next exact deadline so a future event sleeps
            # until due instead of keeping the loop awake.
            _pending_notification_event.clear()
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
                    timeout=seconds_until_due
                )

            if _stop_event.is_set():
                break

            due_hints = _pop_due_events()
            if due_hints:
                with app.app_context():
                    _run_light_reconcile_cycle(
                        events=due_hints,
                        source="governed_exact_event",
                    )

            if hydration_enabled:
                now = datetime.utcnow()
                if (
                    _last_full_sync is None
                    or (
                        now - _last_full_sync
                    ).total_seconds()
                    >= FULL_SYNC_SECONDS
                ):
                    with app.app_context():
                        _run_full_sync_cycle()

        except Exception as exc:
            _safe_error("Engine loop error", exc)
            _stop_event.wait(timeout=30)

    _safe_log("Webhook-scoped runtime loop stopped")


def _acquire_runtime_owner_lock() -> bool:
    global _runtime_lock_handle
    if _runtime_lock_handle is not None:
        return True

    try:
        import fcntl

        handle = open(
            _RUNTIME_LOCK_PATH,
            "a+",
            encoding="utf-8",
        )
        try:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(
            f"pid={os.getpid()} "
            f"started_at={datetime.utcnow().isoformat()}Z\n"
        )
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
        if not _truthy(
            os.getenv("ENABLE_GOVERNED_RUNTIME_ENGINE", "true"),
            True,
        ):
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
        _safe_error(
            "Governed runtime engine failed to start",
            exc,
        )
        return False


def get_governed_runtime_status():
    now = datetime.utcnow()
    engine_live = bool(_started)
    with _pending_events_lock:
        pending_count = len(_pending_events)
        next_due = min(
            (
                event["verify_after"]
                for event in _pending_events
            ),
            default=None,
        )

    return {
        "engine_started": engine_live,
        "runtime_mode": (
            "EVENT-DRIVEN GOVERNED"
            if engine_live
            else "MANUAL GOVERNED"
        ),
        "execution_mode": (
            "EVENT + MANUAL GOVERNED"
            if engine_live
            else "MANUAL ONLY"
        ),
        "workers_running": engine_live,
        "schedulers_running": engine_live,
        "queue_consumers_running": engine_live,
        "started_at": (
            _started_at.isoformat()
            if _started_at
            else None
        ),
        "last_full_sync": (
            _last_full_sync.isoformat()
            if _last_full_sync
            else None
        ),
        "last_light_reconcile": (
            _last_light_reconcile.isoformat()
            if _last_light_reconcile
            else None
        ),
        "last_marketplace_import": (
            _last_marketplace_import.isoformat()
            if _last_marketplace_import
            else None
        ),
        "last_fba_import": (
            _last_fba_import.isoformat()
            if _last_fba_import
            else None
        ),
        "last_ebay_import": (
            _last_ebay_import.isoformat()
            if _last_ebay_import
            else None
        ),
        "next_full_sync_seconds": (
            max(
                0,
                FULL_SYNC_SECONDS
                - int(
                    (
                        now - _last_full_sync
                    ).total_seconds()
                ),
            )
            if _last_full_sync
            else 0
        ),
        "next_light_reconcile_seconds": (
            max(
                0,
                int((next_due - now).total_seconds()),
            )
            if next_due
            else 0
        ),
        "last_error": _last_error,
        "runtime_heartbeat": None,
        "runtime_status_source": "process_memory",
        "pending_notification": pending_count > 0,
        "pending_webhook_verifications": pending_count,
        "last_event_at": (
            _last_event_at.isoformat()
            if _last_event_at
            else None
        ),
        "last_event_source": _last_event_source,
        "last_verification_result": _last_verification_result,
        "automatic_8h_hydration_enabled": _truthy(
            os.getenv("ENABLE_GOVERNED_8H_HYDRATION", "false"),
            False,
        ),
        "idle_db_activity": False,
    }
