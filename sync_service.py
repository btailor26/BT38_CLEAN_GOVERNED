"""
BT38 legacy sync service disabled.

Temporary fail-closed compatibility shell for shutdown phase.
All old sync loops, automatic marketplace imports, push orchestration,
background execution, feed checks, and direct marketplace coordination are blocked.

This file remains import-safe only.
"""

import logging
from typing import Any, Dict, Tuple

SYNC_SERVICE_DISABLED = True
LEGACY_SYNC_ORCHESTRATION_DISABLED = True


def _disabled(action: str) -> Dict[str, Any]:
    logging.warning("[SYNC_SERVICE_DISABLED] %s blocked.", action)
    return {
        "success": False,
        "ok": False,
        "sync_service_disabled": True,
        "execution_blocked": True,
        "action": action,
        "error": "Legacy sync service disabled. Only the future governed path may execute sync logic.",
    }


def start_sync_service():
    return _disabled("start_sync_service")


def sync_store(store):
    return _disabled("sync_store")


def immediate_sync_store(store_id):
    return False, "Legacy sync service disabled"


def should_sync(store):
    return False


def should_push_to_store(store):
    return False


def get_stores_by_push_priority():
    return []


def increment_store_failure_count(store):
    return _disabled("increment_store_failure_count")


def reset_store_failure_count(store):
    return _disabled("reset_store_failure_count")


def create_warehouse_stock_from_import(items_data, store):
    return 0


def attempt_store_connection(store):
    return False


def sync_item_to_store(store, item):
    return False, "Legacy sync execution disabled"


def automatic_push_to_stores(item, operation="update"):
    return False, _disabled("automatic_push_to_stores")


def trigger_automatic_push(item, operation="update", run_async=True):
    return False, _disabled("trigger_automatic_push")
