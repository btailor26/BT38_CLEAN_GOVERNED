"""
BT38 legacy queue manager disabled.

Temporary fail-closed compatibility shell for shutdown phase.
Old SyncJob creation, queue polling, watchdog cleanup, retry handling, and stale job
cancellation are disabled so no old sync/worker path can create or execute work.

New governed execution may only be added after shutdown tests pass.
"""

import logging
from typing import Any, Dict, Optional

QUEUE_MANAGER_DISABLED = True
LEGACY_SYNC_QUEUE_DISABLED = True

PRIORITY_LOW = 1
PRIORITY_MEDIUM = 5
PRIORITY_HIGH = 10

JOB_FULL_SYNC = "full_sync"
JOB_PUSH_ITEM = "push_item"
JOB_IMPORT_LISTINGS = "import_listings"
JOB_ORDER_IMPORT = "order_import"
JOB_AUTO_PUSH_DRY_RUN = "auto_push_dry_run"


def _disabled_result(action: str) -> Dict[str, Any]:
    logging.warning("[QUEUE_DISABLED] %s blocked. Legacy queue manager is disabled.", action)
    return {
        "success": False,
        "ok": False,
        "queue_disabled": True,
        "execution_blocked": True,
        "action": action,
        "error": "Legacy sync queue is disabled. Use the new governed execution path only after shutdown tests pass.",
    }


def enqueue_sync_job(store_id: int, job_type: str, payload: Optional[Dict[str, Any]] = None, priority: int = PRIORITY_MEDIUM):
    """Fail closed: do not create SyncJob rows from old queue paths."""
    result = _disabled_result("enqueue_sync_job")
    result.update({"store_id": store_id, "job_type": job_type, "payload": payload or {}, "priority": priority})
    return result


def get_next_pending_job(store_id: int):
    _disabled_result("get_next_pending_job")
    return None


def mark_job_running(job_id: int) -> bool:
    _disabled_result("mark_job_running")
    return False


def mark_job_complete(job_id: int) -> bool:
    _disabled_result("mark_job_complete")
    return False


def mark_job_failed(job_id: int, error_message: str, retry_in_minutes: Optional[int] = None) -> bool:
    _disabled_result("mark_job_failed")
    return False


def get_pending_jobs_count(store_id: int) -> int:
    _disabled_result("get_pending_jobs_count")
    return 0


def has_active_job(store_id: int) -> bool:
    _disabled_result("has_active_job")
    return False


def reset_stuck_jobs(timeout_minutes: int = 10):
    _disabled_result("reset_stuck_jobs")
    return {}


def reset_stuck_sync_logs(timeout_minutes: int = 30) -> int:
    _disabled_result("reset_stuck_sync_logs")
    return 0


def cancel_stale_push_jobs_for_warehouse(warehouse_stock_id: int) -> int:
    _disabled_result("cancel_stale_push_jobs_for_warehouse")
    return 0


def cleanup_old_jobs(days_old: int = 7) -> int:
    _disabled_result("cleanup_old_jobs")
    return 0
