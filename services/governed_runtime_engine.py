"""
BT38 GOVERNED RUNTIME ENGINE

Event-driven runtime contract:
- Webhooks perform immediate, targeted work in their own governed path.
- The runtime engine performs no recurring database heartbeat writes.
- The 15-minute safety cadence does not query or write the database when no
  in-memory notification has been raised.
- Full marketplace hydration remains available manually and can be enabled as
  an explicit recovery cycle with ENABLE_GOVERNED_8H_HYDRATION=true.
- Amazon FBA remains read-only.
- No webhook or automation path pushes directly.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime

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

FULL_SYNC_SECONDS = 8 * 60 * 60
LIGHT_RECONCILE_SECONDS = 15 * 60

# Pure in-memory signal. Waiting on this event does not touch Neon.
_pending_notification_event = threading.Event()
_stop_event = threading.Event()


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _safe_log(message: str):
    logging.info("[GOVERNED_RUNTIME_ENGINE] %s", message)


def _safe_error(message: str, exc: Exception):
    global _last_error
    _last_error = f"{message}: {exc}"
    logging.exception("[GOVERNED_RUNTIME_ENGINE] %s", _last_error)


def notify_governed_runtime_work(source: str = "webhook") -> None:
    """Raise an in-memory notification for the governed safety cycle.

    This function is intentionally database-free. Webhook handlers may call it
    after receiving an event. The next runtime wake processes the order-only
    safety path once and then returns to sleep.
    """
    global _last_event_at, _last_event_source
    _last_event_at = datetime.utcnow()
    _last_event_source = str(source or "webhook")
    _pending_notification_event.set()


def _config_on_for_explicit_work(key: str, default: bool = False) -> bool:
    """Read a fuse only when real work has already been requested.

    It must never be called from an idle polling loop. This preserves settings
    compatibility without keeping Neon awake.
    """
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
        _safe_log("marketplace import skipped: import_enabled OFF")
        return False
    if not _config_on_for_explicit_work("runtime_import_enabled", True):
        _safe_log("marketplace import skipped: runtime_import_enabled OFF")
        return False
    if not _config_on_for_explicit_work("marketplace_import_enabled", True):
        _safe_log("marketplace import skipped: marketplace_import_enabled OFF")
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
    """Run explicit marketplace hydration.

    This function is retained for manual refreshes and optional recovery work.
    It is not called by the idle 15-minute safety cadence.
    """
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
                        "store": store.name,
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

                result = run_governed_amazon_inventory_import(store_id=store.id)
                listing_fulfillment = run_governed_amazon_listing_fulfillment_refresh(
                    store_id=store.id,
                    max_pages=2,
                )
                _last_fba_import = datetime.utcnow()

                results.append({
                    "store_id": store.id,
                    "store": store.name,
                    "platform": store.platform,
                    "import_type": "amazon_fba_read_only_plus_listing_fulfillment",
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
                    "store": store.name,
                    "platform": store.platform,
                    "import_type": "ebay_variation_hydration",
                    "success": True,
                    "result": result,
                })
                continue

            results.append({
                "store_id": store.id,
                "store": store.name,
                "platform": store.platform,
                "skipped": True,
                "reason": "unsupported_marketplace_import",
            })

        except Exception as exc:
            _safe_error(
                f"marketplace hydration failed store_id={getattr(store, 'id', None)}",
                exc,
            )
            results.append({
                "store_id": getattr(store, "id", None),
                "store": getattr(store, "name", None),
                "platform": getattr(store, "platform", None),
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
        "order_import_started": False,
        "order_stock_bridge_started": False,
        "results": results,
    }


def _run_light_reconcile_cycle(source="light_reconcile_event"):
    """Run order-only verification after a real notification.

    No marketplace inventory hydration occurs here.
    """
    global _last_light_reconcile

    order_import = None
    try:
        from services.governed_marketplace_order_import import (
            run_governed_marketplace_order_import,
        )

        order_import = run_governed_marketplace_order_import(
            source=f"{source}_order_import",
        )
    except Exception as exc:
        _safe_error("event marketplace order import failed", exc)
        order_import = {
            "success": False,
            "error": str(exc),
        }

    order_stock_bridge = None
    try:
        from services.governed_order_stock_mutation import (
            mutate_recent_marketplace_order_lines,
        )

        order_stock_bridge = mutate_recent_marketplace_order_lines(
            limit=100,
            source=f"{source}_order_stock_bridge",
        )
    except Exception as exc:
        _safe_error("event order stock bridge failed", exc)
        order_stock_bridge = {
            "success": False,
            "error": str(exc),
            "source": f"{source}_order_stock_bridge",
        }

    _last_light_reconcile = datetime.utcnow()
    _safe_log(
        "event-driven light reconcile complete "
        f"order_import={order_import} order_stock_bridge={order_stock_bridge}"
    )

    return {
        "success": True,
        "governed": True,
        "source": source,
        "marketplace_import_refresh_started": False,
        "order_import": order_import,
        "order_stock_bridge": order_stock_bridge,
    }


def _run_full_sync_cycle():
    global _last_full_sync

    run_governed_marketplace_import_refresh(source="full_sync_8h_recovery")
    _last_full_sync = datetime.utcnow()
    _safe_log("8-hour recovery hydration complete")


def _engine_loop(app):
    """Wait without database activity until an event or optional recovery cycle.

    threading.Event.wait() blocks in memory. A timeout wake performs no SQL and
    no timestamp persistence when no notification exists.
    """
    global _last_full_sync

    _safe_log("Event-driven engine loop started")
    hydration_enabled = _truthy(
        os.getenv("ENABLE_GOVERNED_8H_HYDRATION", "false"),
        False,
    )
    next_hydration_at = datetime.utcnow()

    while not _stop_event.is_set():
        signalled = _pending_notification_event.wait(timeout=LIGHT_RECONCILE_SECONDS)

        if _stop_event.is_set():
            break

        try:
            with app.app_context():
                if signalled:
                    _pending_notification_event.clear()
                    _run_light_reconcile_cycle(
                        source=f"event_{_last_event_source or 'webhook'}",
                    )

                if hydration_enabled:
                    now = datetime.utcnow()
                    hydration_due = (
                        _last_full_sync is None
                        or (now - _last_full_sync).total_seconds() >= FULL_SYNC_SECONDS
                    )
                    if hydration_due and now >= next_hydration_at:
                        _run_full_sync_cycle()
                        next_hydration_at = now

                if not signalled and not hydration_enabled:
                    _safe_log("15-minute safety wake slept: no event, no database work")

        except Exception as exc:
            _safe_error("Engine loop error", exc)

    _safe_log("Event-driven engine loop stopped")


def _acquire_runtime_owner_lock() -> bool:
    """Ensure one OS process owns the governed runtime thread."""
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
            _safe_log("Governed runtime engine already owned by another process")
            return False

        handle.seek(0)
        handle.truncate()
        handle.write(
            f"pid={os.getpid()} started_at={datetime.utcnow().isoformat()}Z\n"
        )
        handle.flush()

        _runtime_lock_handle = handle
        _safe_log(
            f"Governed runtime owner lock acquired path={_RUNTIME_LOCK_PATH}"
        )
        return True

    except Exception as exc:
        _safe_error("Governed runtime owner lock failed", exc)
        return False


def start_governed_runtime_engine(app):
    """Start the event-driven governed runtime engine once."""
    global _started, _started_at

    with _status_lock:
        if _started:
            return False

        enabled = _truthy(
            os.getenv("ENABLE_GOVERNED_RUNTIME_ENGINE", "true"),
            True,
        )
        if not enabled:
            _safe_log("ENABLE_GOVERNED_RUNTIME_ENGINE is OFF")
            return False

        if not _acquire_runtime_owner_lock():
            return False

        _started = True
        _started_at = datetime.utcnow()

    try:
        _safe_log(
            "Governed runtime is event-driven; idle safety wakes do not touch DB"
        )

        thread = threading.Thread(
            target=_engine_loop,
            args=(app,),
            daemon=True,
            name="BT38GovernedRuntimeEngine",
        )
        thread.start()
        _safe_log("Governed runtime engine started")
        return True
    except Exception as exc:
        _safe_error("Governed runtime engine failed to start", exc)
        return False


def get_governed_runtime_status():
    """Return process-local runtime status without querying Neon."""
    now = datetime.utcnow()
    engine_live = bool(_started)

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
            _last_marketplace_import.isoformat()
            if _last_marketplace_import
            else None
        ),
        "last_fba_import": _last_fba_import.isoformat() if _last_fba_import else None,
        "last_ebay_import": (
            _last_ebay_import.isoformat() if _last_ebay_import else None
        ),
        "next_full_sync_seconds": (
            max(
                0,
                FULL_SYNC_SECONDS
                - int((now - _last_full_sync).total_seconds()),
            )
            if _last_full_sync
            else 0
        ),
        "next_light_reconcile_seconds": 0,
        "last_error": _last_error,
        "runtime_heartbeat": None,
        "runtime_status_source": "process_memory",
        "pending_notification": _pending_notification_event.is_set(),
        "last_event_at": _last_event_at.isoformat() if _last_event_at else None,
        "last_event_source": _last_event_source,
        "automatic_8h_hydration_enabled": _truthy(
            os.getenv("ENABLE_GOVERNED_8H_HYDRATION", "false"),
            False,
        ),
        "idle_db_activity": False,
    }
