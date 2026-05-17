"""BT38 legacy queue manager disabled.

Temporary fail-closed compatibility shell for the shutdown phase. Old queue job
creation, queue polling, watchdog cleanup, retry handling, and stale job
cancellation are disabled so no old sync/worker path can create or execute work.
"""

from typing import Any, Dict, Optional

from old_path_shutdown import (
    GOVERNED_PATH_REQUIRED,
    MARKETPLACE_EXECUTION_DISABLED,
    OLD_SYNC_DISABLED,
    disabled_response,
)

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


def _disabled_result(action: str, **context: Any) -> Dict[str, Any]:
    result = disabled_response(action, **context)
    result["queue_disabled"] = True
    return result


def enqueue_sync_job(
    store_id: int,
    job_type: str,
    payload: Optional[Dict[str, Any]] = None,
    priority: int = PRIORITY_MEDIUM,
):
    """Fail closed: do not create rows from old queue paths."""
    return _disabled_result(
        "enqueue_sync_job",
        store_id=store_id,
        job_type=job_type,
        payload=payload or {},
        priority=priority,
    )


def get_next_pending_job(store_id: int):
    _disabled_result("get_next_pending_job", store_id=store_id)
    return None


def mark_job_running(job_id: int) -> bool:
    _disabled_result("mark_job_running", job_id=job_id)
    return False


def mark_job_complete(job_id: int) -> bool:
    _disabled_result("mark_job_complete", job_id=job_id)
    return False


def mark_job_failed(job_id: int, error_message: str, retry_in_minutes: Optional[int] = None) -> bool:
    _disabled_result("mark_job_failed", job_id=job_id, error_message=error_message, retry_in_minutes=retry_in_minutes)
    return False


def get_pending_jobs_count(store_id: int) -> int:
    _disabled_result("get_pending_jobs_count", store_id=store_id)
    return 0


def has_active_job(store_id: int) -> bool:
    _disabled_result("has_active_job", store_id=store_id)
    return False


def reset_stuck_jobs(timeout_minutes: int = 10):
    return _disabled_result("reset_stuck_jobs", timeout_minutes=timeout_minutes)


def reset_stuck_sync_logs(timeout_minutes: int = 30) -> int:
    _disabled_result("reset_stuck_sync_logs", timeout_minutes=timeout_minutes)
    return 0


def cancel_stale_push_jobs_for_warehouse(warehouse_stock_id: int) -> int:
    _disabled_result("cancel_stale_push_jobs_for_warehouse", warehouse_stock_id=warehouse_stock_id)
    return 0


def cleanup_old_jobs(days_old: int = 7) -> int:
    _disabled_result("cleanup_old_jobs", days_old=days_old)
    return 0
