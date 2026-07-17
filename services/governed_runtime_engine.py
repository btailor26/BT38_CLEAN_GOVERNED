"""
BT38 GOVERNED RUNTIME ENGINE

One governed automation starter:
- Light reconcile cadence: 15 minutes
- Full refresh cadence: 8 hours
- Import/hydration runs before sync/push decisions
- Amazon FBA remains read-only
- eBay variations are imported into DB as searchable marketplace rows
- Webhooks may trigger import refresh only
- No webhook or automation path pushes directly
"""

from __future__ import annotations

import logging
import os
import threading
import time
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

FULL_SYNC_SECONDS = 8 * 60 * 60
LIGHT_RECONCILE_SECONDS = 15 * 60


def _truthy(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _config_on(key: str, default: bool = False) -> bool:
    try:
        from models import SystemConfig

        row = SystemConfig.query.filter_by(key=key).first()
        if not row:
            return default
        return _truthy(row.value, default)
    except Exception:
        return default


def _runtime_status_set(key: str, value) -> None:
    try:
        from extensions import db
        from models import SystemConfig

        full_key = f"runtime_{key}"
        row = SystemConfig.query.filter_by(key=full_key).first()
        if row is None:
            row = SystemConfig(key=full_key, value=str(value))
            db.session.add(row)
        else:
            row.value = str(value)
        db.session.commit()
    except Exception as exc:
        _safe_error(f"runtime status persist failed key={key}", exc)


# Cost guard:
# Heartbeat is NOT a business event and must not wake Neon every loop.
# Only real runtime events such as light_reconcile/full_sync should persist.
_runtime_memory_heartbeat = None

def _runtime_status_stamp(key: str) -> None:
    global _runtime_memory_heartbeat
    value = datetime.utcnow().isoformat() + "Z"

    if key == "heartbeat":
        _runtime_memory_heartbeat = value
        return

    _runtime_status_set(key, value)


def _safe_log(message: str):
    logging.info("[GOVERNED_RUNTIME_ENGINE] %s", message)


def _safe_error(message: str, exc: Exception):
    global _last_error
    _last_error = f"{message}: {exc}"
    logging.exception("[GOVERNED_RUNTIME_ENGINE] %s", _last_error)


def _import_fuses_on() -> bool:
    if not _config_on("import_enabled", True):
        _safe_log("marketplace import skipped: import_enabled OFF")
        return False
    if not _config_on("runtime_import_enabled", True):
        _safe_log("marketplace import skipped: runtime_import_enabled OFF")
        return False
    if not _config_on("marketplace_import_enabled", True):
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


def run_governed_marketplace_import_refresh(store_id=None, source="governed_runtime_engine"):
    """
    Hydration/reconciliation path only.

    This path is for the 8-hour full sync:
    - Amazon FBA/AFN inventory import
    - Amazon listing fulfilment refresh
    - eBay inventory / variation hydration

    It must not import orders.
    It must not mutate warehouse stock from orders.
    It must not run the order stock bridge.

    Order verification belongs to the 15-minute light reconcile path.
    Immediate event processing belongs to webhook execution.
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
        stores = [s for s in stores if int(s.id) == int(store_id)]

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

                from services.governed_amazon_inventory_import import run_governed_amazon_inventory_import
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
                from services.governed_ebay_inventory_import import run_governed_ebay_inventory_import

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
            _safe_error(f"marketplace hydration failed store_id={getattr(store, 'id', None)}", exc)
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



def _run_light_reconcile_cycle():
    global _last_light_reconcile

    source = "light_reconcile_15m"

    order_import = None
    try:
        from services.governed_marketplace_order_import import (
            run_governed_marketplace_order_import,
        )

        order_import = run_governed_marketplace_order_import(
            source=f"{source}_order_import",
        )

    except Exception as exc:
        _safe_error("15-minute marketplace order import failed", exc)
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
        _safe_error("15-minute order stock bridge failed", exc)
        order_stock_bridge = {
            "success": False,
            "error": str(exc),
            "source": f"{source}_order_stock_bridge",
        }

    _last_light_reconcile = datetime.utcnow()
    _runtime_status_stamp("last_light_reconcile")
    _safe_log(
        f"15-minute light reconcile order-only complete "
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

    run_governed_marketplace_import_refresh(source="full_sync_8h_import_first")
    _last_full_sync = datetime.utcnow()
    _runtime_status_stamp("last_full_sync")
    _safe_log("8-hour full cycle import refresh complete")


def _has_pending_notification_work() -> bool:
    """
    15-minute reconcile should not hammer marketplace/order import when there is
    no webhook/notification work to verify.
    """
    try:
        from datetime import timedelta
        from models import SystemLog

        cutoff = datetime.utcnow() - timedelta(minutes=20)

        notification_count = (
            SystemLog.query
            .filter(SystemLog.created_at >= cutoff)
            .filter(SystemLog.log_type.in_([
                "marketplace_webhook",
                "governed_webhook_execution",
            ]))
            .count()
        )

        if notification_count > 0:
            return True

        from models import MarketplaceOrder

        pending_fbm_count = (
            MarketplaceOrder.query
            .filter(MarketplaceOrder.status == "pending")
            .filter(MarketplaceOrder.fulfillment_type == "FBM")
            .filter(MarketplaceOrder.warehouse_stock_id.isnot(None))
            .count()
        )

        return pending_fbm_count > 0
    except Exception as exc:
        _safe_error("notification work check failed", exc)
        return False


def _engine_loop(app):
    global _last_full_sync, _last_light_reconcile

    _safe_log("Engine loop started")

    while True:
        sleep_seconds = 30

        try:
            with app.app_context():
                from extensions import db

                try:
                    if not _config_on("runtime_engine_started", False):
                        _runtime_status_set("engine_started", "true")
                        _runtime_status_stamp("engine_started_at")

                    _runtime_status_stamp("heartbeat")

                    if _config_on("read_only_mode", False):
                        _safe_log("Runtime paused: read_only_mode ON")
                        sleep_seconds = 60
                    else:
                        now = datetime.utcnow()

                        if (
                            _last_light_reconcile is None
                            or (
                                now - _last_light_reconcile
                            ).total_seconds() >= LIGHT_RECONCILE_SECONDS
                        ):
                            if (
                                _config_on("scheduler_enabled", True)
                                and _config_on("reconcile_15m_enabled", True)
                            ):
                                if _has_pending_notification_work():
                                    _run_light_reconcile_cycle()
                                else:
                                    _last_light_reconcile = now
                                    _runtime_status_stamp(
                                        "last_light_reconcile"
                                    )
                                    _safe_log(
                                        "15-minute reconcile slept: "
                                        "no pending webhook/notification work"
                                    )
                            else:
                                _safe_log(
                                    "15-minute reconcile skipped by fuse box"
                                )
                                _last_light_reconcile = now

                        if (
                            _last_full_sync is None
                            or (
                                now - _last_full_sync
                            ).total_seconds() >= FULL_SYNC_SECONDS
                        ):
                            if (
                                _config_on("sync_enabled", True)
                                and _config_on("sync_worker_enabled", True)
                            ):
                                _run_full_sync_cycle()
                            else:
                                _safe_log(
                                    "8-hour full cycle skipped by fuse box"
                                )
                                _last_full_sync = now

                except Exception:
                    db.session.rollback()
                    raise
                finally:
                    # A permanent background thread must release its
                    # scoped SQLAlchemy session after every iteration.
                    db.session.remove()

        except Exception as exc:
            _safe_error("Engine loop error", exc)

        # Never sleep while holding Flask app or database context.
        time.sleep(sleep_seconds)



def _acquire_runtime_owner_lock() -> bool:
    """
    Ensures only one OS process owns the governed runtime engine.

    Gunicorn may run multiple web workers.
    One-off audit scripts may import app.py.
    Neither should create duplicate governed runtime engines.
    """
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
        handle.write(f"pid={os.getpid()} started_at={datetime.utcnow().isoformat()}Z\n")
        handle.flush()

        _runtime_lock_handle = handle
        _safe_log(f"Governed runtime owner lock acquired path={_RUNTIME_LOCK_PATH}")
        return True

    except Exception as exc:
        _safe_error("Governed runtime owner lock failed", exc)
        return False


def start_governed_runtime_engine(app):
    """
    Starts the governed runtime engine once per process.

    This does not bypass fuse settings.
    This does not create a second marketplace authority.
    """
    global _started, _started_at

    with _status_lock:
        if _started:
            return False

        enabled = _truthy(os.getenv("ENABLE_GOVERNED_RUNTIME_ENGINE", "true"), True)
        if not enabled:
            _safe_log("ENABLE_GOVERNED_RUNTIME_ENGINE is OFF")
            return False

        if not _acquire_runtime_owner_lock():
            return False

        _started = True
        _started_at = datetime.utcnow()

    try:
        _safe_log("Governed runtime owns 15-minute reconcile and 8-hour hydration; legacy dispatcher not started")

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


def _persisted_runtime_truth():
    """
    Multi-worker safe runtime truth.

    Gunicorn workers have separate memory, so _started only tells the truth
    for the worker that owns the runtime thread. Persisted heartbeat is the
    shared production truth.
    """
    try:
        from datetime import datetime
        from models import SystemConfig

        def get_value(key):
            row = SystemConfig.query.filter_by(key=key).first()
            return str(row.value).strip() if row and row.value is not None else None

        started = _truthy(get_value("runtime_engine_started"), False)
        heartbeat_raw = get_value("runtime_heartbeat")
        started_at = get_value("runtime_engine_started_at")

        heartbeat_fresh = False
        if heartbeat_raw:
            hb = heartbeat_raw.replace("Z", "")
            heartbeat_at = datetime.fromisoformat(hb)
            heartbeat_fresh = (datetime.utcnow() - heartbeat_at).total_seconds() <= 120

        return {
            "persisted_engine_started": started,
            "persisted_heartbeat": heartbeat_raw,
            "persisted_started_at": started_at,
            "persisted_runtime_live": bool(started and heartbeat_fresh),
        }
    except Exception as exc:
        _safe_error("persisted runtime truth read failed", exc)
        return {
            "persisted_engine_started": False,
            "persisted_heartbeat": None,
            "persisted_started_at": None,
            "persisted_runtime_live": False,
        }


def get_governed_runtime_status():
    now = datetime.utcnow()
    persisted = _persisted_runtime_truth()
    engine_live = bool(_started or persisted.get("persisted_runtime_live"))

    return {
        "engine_started": engine_live,
        "runtime_mode": "AUTOMATED GOVERNED" if engine_live else "MANUAL GOVERNED",
        "execution_mode": "AUTOMATED + MANUAL GOVERNED" if engine_live else "MANUAL ONLY",
        "workers_running": engine_live,
        "schedulers_running": engine_live,
        "queue_consumers_running": engine_live,
        "started_at": _started_at.isoformat() if _started_at else persisted.get("persisted_started_at"),
        "last_full_sync": _last_full_sync.isoformat() if _last_full_sync else None,
        "last_light_reconcile": _last_light_reconcile.isoformat() if _last_light_reconcile else None,
        "last_marketplace_import": _last_marketplace_import.isoformat() if _last_marketplace_import else None,
        "last_fba_import": _last_fba_import.isoformat() if _last_fba_import else None,
        "last_ebay_import": _last_ebay_import.isoformat() if _last_ebay_import else None,
        "next_full_sync_seconds": max(0, FULL_SYNC_SECONDS - int((now - _last_full_sync).total_seconds())) if _last_full_sync else 0,
        "next_light_reconcile_seconds": max(0, LIGHT_RECONCILE_SECONDS - int((now - _last_light_reconcile).total_seconds())) if _last_light_reconcile else 0,
        "last_error": _last_error,
        "runtime_heartbeat": persisted.get("persisted_heartbeat"),
        "runtime_status_source": "memory_or_persisted_heartbeat",
    }

