"""
BT38 GOVERNED RUNTIME ENGINE

Runtime contract:
- Webhooks perform immediate targeted work in their governed route.
- A webhook may arm one 15-minute verification for its exact identifiers.
- The verification never imports recent orders, scans warehouse rows, lists all
  marketplace records, or starts marketplace hydration.
- No webhook means no database access.
- Full marketplace hydration is manual/recovery only.
- Amazon FBA remains read-only.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from datetime import datetime, timedelta

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
_stop_event = threading.Event()
_pending_events = deque()
_pending_events_lock = threading.Lock()


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _clean(value):
    value = str(value or "").strip()
    return value or None


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
    store_id = raw.get("store_id")
    try:
        store_id = int(store_id) if store_id is not None else None
    except (TypeError, ValueError):
        store_id = None

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

    scope_present = bool(
        store_id is not None
        and any((seller_sku, listing_id, order_id, asin, fnsku))
    )

    return {
        "source": _clean(source) or "webhook",
        "event_type": event_type,
        "marketplace": marketplace,
        "store_id": store_id,
        "seller_sku": seller_sku,
        "listing_id": listing_id,
        "order_id": order_id,
        "asin": asin,
        "fnsku": fnsku,
        "payload": raw.get("payload"),
        "received_at": datetime.utcnow(),
        "verify_after": datetime.utcnow() + timedelta(seconds=LIGHT_RECONCILE_SECONDS),
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
        event.get("order_id"),
        event.get("asin"),
        event.get("fnsku"),
    )


def notify_governed_runtime_work(source: str = "webhook", event=None, **identifiers):
    """Arm a 15-minute verification for one exact webhook scope.

    Backward-compatible source-only calls are accepted, but they are recorded as
    unscoped and will be skipped without SQL. Webhook handlers should provide at
    least store_id plus order_id, seller_sku, listing_id, asin, or fnsku.
    """
    global _last_event_at, _last_event_source

    item = _normalise_webhook_event(source, event=event, **identifiers)
    _last_event_at = item["received_at"]
    _last_event_source = item["source"]

    with _pending_events_lock:
        key = _event_key(item)
        for queued in _pending_events:
            if _event_key(queued) == key:
                # Keep one verification per affected identity and move its due
                # time forward when duplicate notifications arrive.
                queued["verify_after"] = item["verify_after"]
                queued["payload"] = item.get("payload") or queued.get("payload")
                break
        else:
            _pending_events.append(item)

    _pending_notification_event.set()
    return {
        "queued": True,
        "scoped": item["scope_present"],
        "verify_after": item["verify_after"].isoformat(),
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
                    max_pages=2,
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
    from models import AmazonFBAInventory

    query = AmazonFBAInventory.query
    identity = None
    identity_field = None

    for field, value in (
        ("seller_sku", event.get("seller_sku")),
        ("fnsku", event.get("fnsku")),
        ("asin", event.get("asin")),
    ):
        if value and hasattr(AmazonFBAInventory, field):
            query = query.filter(getattr(AmazonFBAInventory, field) == value)
            identity = value
            identity_field = field
            break

    if identity is None:
        return {"verified": False, "reason": "exact_fba_identity_unavailable"}
    if hasattr(AmazonFBAInventory, "store_id"):
        query = query.filter(AmazonFBAInventory.store_id == event["store_id"])

    row = query.first()
    return {
        "verified": row is not None,
        "object": "AmazonFBAInventory",
        "identity_field": identity_field,
        "identity": identity,
        "rows_examined_max": 1,
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


def _verify_webhook_event(event):
    """Verify only the exact identity supplied by the webhook."""
    if not event.get("scope_present"):
        return {
            "verified": False,
            "skipped": True,
            "reason": "webhook_scope_required",
            "database_touched": False,
        }

    event_type = str(event.get("event_type") or "").lower()
    marketplace = str(event.get("marketplace") or "").lower()

    if event.get("order_id") or "order" in event_type:
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
    results = [_verify_webhook_event(event) for event in events]
    _last_light_reconcile = datetime.utcnow()
    _last_verification_result = {
        "success": True,
        "governed": True,
        "source": source,
        "events_received": len(events),
        "events_verified": sum(1 for item in results if item.get("verified")),
        "full_scan_started": False,
        "recent_order_import_started": False,
        "order_stock_bridge_started": False,
        "warehouse_scan_started": False,
        "marketplace_hydration_started": False,
        "results": results,
    }
    _safe_log(
        "15-minute webhook verification complete "
        f"events={len(events)} verified={_last_verification_result['events_verified']}"
    )
    return _last_verification_result


def _run_full_sync_cycle():
    global _last_full_sync
    run_governed_marketplace_import_refresh(source="full_sync_8h_recovery")
    _last_full_sync = datetime.utcnow()


def _engine_loop(app):
    """Sleep in memory; verify queued webhook identities when their 15m timer is due."""
    global _last_full_sync

    _safe_log("Webhook-scoped runtime loop started")
    hydration_enabled = _truthy(
        os.getenv("ENABLE_GOVERNED_8H_HYDRATION", "false"),
        False,
    )

    while not _stop_event.is_set():
        timeout = _seconds_until_next_due()
        _pending_notification_event.wait(timeout=timeout)

        if _stop_event.is_set():
            break

        due_events = _pop_due_events()
        try:
            if due_events:
                with app.app_context():
                    _run_light_reconcile_cycle(events=due_events)

            if hydration_enabled:
                now = datetime.utcnow()
                if (
                    _last_full_sync is None
                    or (now - _last_full_sync).total_seconds() >= FULL_SYNC_SECONDS
                ):
                    with app.app_context():
                        _run_full_sync_cycle()
        except Exception as exc:
            _safe_error("Engine loop error", exc)

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
