"""BT38 governed passive queue contract.

The fuse box is the only authority.
This module remains only so old imports do not crash.

It must not create jobs, process jobs, call marketplaces, start workers,
or bypass governed routes.
"""

from __future__ import annotations

OLD_SYNC_DISABLED = True
MARKETPLACE_EXECUTION_DISABLED = True
GOVERNED_PATH_REQUIRED = True
QUEUE_MANAGER_DISABLED = True

JOB_PUSH_ITEM = "push_item"
JOB_FULL_SYNC = "full_sync"
JOB_AUTO_PUSH_DRY_RUN = "auto_push_dry_run"

PRIORITY_LOW = 1
PRIORITY_MEDIUM = 5
PRIORITY_HIGH = 10


def _blocked(reason: str) -> dict:
    return {
        "ok": False,
        "success": False,
        "governed": True,
        "execution_blocked": True,
        "execution_started": False,
        "queue_job_created": False,
        "reason": reason,
    }


def enqueue_sync_job(*args, **kwargs):
    """Disabled legacy queue entrypoint.

    Old callers may still import this symbol, but execution is blocked.
    Real execution must go through:
    UI -> governed route -> fuse box -> governed execution -> adapter
    """
    return _blocked(
        "Legacy queue enqueue is disabled. Use the governed event/action path."
    )


def cancel_stale_push_jobs_for_warehouse(*args, **kwargs):
    """Disabled legacy cleanup placeholder."""
    return {
        "ok": True,
        "success": True,
        "governed": True,
        "cancelled": 0,
        "reason": "Legacy queue cleanup is passive under fuse-box authority.",
    }


def process_next_job(*args, **kwargs):
    """Disabled worker processor placeholder."""
    return _blocked(
        "Legacy queue processing is disabled. Use governed routes."
    )


def start_worker(*args, **kwargs):
    """Disabled worker startup placeholder."""
    result = _blocked(
        "Legacy workers are disabled. Fuse box controls execution."
    )
    result["worker_started"] = False
    return result
