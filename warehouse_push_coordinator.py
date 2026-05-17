"""
BT38 warehouse push coordinator disabled.

Temporary fail-closed compatibility shell for shutdown phase.
All warehouse-driven marketplace push preparation, enqueue logic,
group enforcement pushes, and sync fan-out coordination are blocked.

Warehouse operations must not trigger legacy marketplace execution paths.
"""

import logging
from typing import Dict, List

WAREHOUSE_PUSH_COORDINATOR_DISABLED = True
LEGACY_WAREHOUSE_PUSH_DISABLED = True


def _disabled(action: str) -> Dict:
    logging.warning("[WAREHOUSE_PUSH_DISABLED] %s blocked.", action)
    return {
        "success": False,
        "ok": False,
        "execution_blocked": True,
        "warehouse_push_disabled": True,
        "action": action,
        "error": "Legacy warehouse push coordinator disabled during governed-path rebuild.",
    }


class WarehousePushCoordinator:
    execution_disabled = True

    def __init__(self):
        logging.warning("[WAREHOUSE_PUSH_DISABLED] WarehousePushCoordinator initialized in disabled mode.")

    def prepare_for_items(self, skus: List[str], operation: str = "update") -> int:
        logging.warning("[WAREHOUSE_PUSH_DISABLED] prepare_for_items blocked for %s", skus)
        return 0

    def enqueue_pending_jobs(self) -> int:
        logging.warning("[WAREHOUSE_PUSH_DISABLED] enqueue_pending_jobs blocked.")
        return 0


def verify_group_integrity(warehouse_stock_id: int, check_pending_jobs: bool = True) -> Dict:
    result = _disabled("verify_group_integrity")
    result["warehouse_stock_id"] = warehouse_stock_id
    result["is_consistent"] = False
    return result


def log_group_push_audit(warehouse_stock_id: int, source: str, quantity_pushed: int) -> None:
    logging.warning(
        "[WAREHOUSE_PUSH_DISABLED] log_group_push_audit blocked for warehouse_stock_id=%s source=%s",
        warehouse_stock_id,
        source,
    )
    return None
