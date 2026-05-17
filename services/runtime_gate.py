"""BT38 centralized runtime gate — shutdown mode.

All marketplace push/sync/import execution must fail closed while the old worker,
queue, service, and route paths are being retired.

No future marketplace path may bypass this module.
The governed execution path can only be opened after shutdown tests pass.
"""

RUNTIME_GATE_FORCE_CLOSED = True
BLOCKED_ACTIONS = {"push", "sync", "import", "order_import", "marketplace", "auto_push"}


def is_runtime_action_allowed(store=None, action_type="unknown", manual=False):
    """Fail closed for every marketplace execution action during shutdown."""
    normalized_action = (action_type or "unknown").strip().lower()

    if RUNTIME_GATE_FORCE_CLOSED or normalized_action in BLOCKED_ACTIONS:
        return False, (
            "BT38 marketplace push/sync/import is disabled during the governed-path rebuild. "
            "No old worker, queue, route, or marketplace service path may execute."
        )

    return False, "Runtime gate is closed by default during governed-path rebuild."
