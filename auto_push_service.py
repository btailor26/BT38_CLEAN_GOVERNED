"""
BT38 auto push service disabled.

Temporary fail-closed compatibility shell for shutdown phase.
All auto-sync, dry-run job creation, real push queue creation, and auto-push
execution are blocked. This prevents old warehouse-change hooks from creating
legacy sync jobs while the new governed path is not yet built.
"""

import logging
from typing import Any, Dict, Tuple, List

AUTO_PUSH_SERVICE_DISABLED = True
LEGACY_AUTO_PUSH_DISABLED = True
AUTO_SYNC_CONFIG_KEY = "auto_sync_enabled"


def _disabled(action: str) -> Dict[str, Any]:
    logging.warning("[AUTO_PUSH_DISABLED] %s blocked.", action)
    return {
        "success": False,
        "ok": False,
        "auto_push_disabled": True,
        "execution_blocked": True,
        "action": action,
        "error": "Legacy auto-push service disabled. Use the new governed execution path only after shutdown tests pass.",
    }


def is_auto_sync_enabled() -> bool:
    return False


def set_auto_sync_enabled(enabled: bool) -> bool:
    logging.warning("[AUTO_PUSH_DISABLED] set_auto_sync_enabled blocked; legacy auto-sync remains disabled.")
    return False


def is_listing_pushable(listing, store) -> Tuple[bool, str]:
    return False, "Legacy auto-push disabled"


def get_pushable_listings_for_warehouse(warehouse_stock_id: int) -> List[Tuple[Any, Any]]:
    return []


def queue_auto_push_for_sku(warehouse_stock_id: int) -> Dict[str, Any]:
    result = _disabled("queue_auto_push_for_sku")
    result["warehouse_stock_id"] = warehouse_stock_id
    result["jobs_created"] = 0
    return result


def queue_real_push_for_warehouse(warehouse_stock_id: int) -> Dict[str, Any]:
    result = _disabled("queue_real_push_for_warehouse")
    result["warehouse_stock_id"] = warehouse_stock_id
    result["jobs_created"] = 0
    result["real_push"] = False
    return result


def execute_dry_run_push(job) -> Dict[str, Any]:
    return _disabled("execute_dry_run_push")
