"""BT38 governed runtime gate.

Default is force-closed. Stage 5 introduces a second explicit live allow flag
for the one internal Amazon FBM/MFN single-SKU inventory push contract. Both
RUNTIME_GATE_FORCE_CLOSED must be False and GOVERNED_AMAZON_FBM_LIVE_ENABLED
must be True before any live governed command can pass.
"""

from __future__ import annotations

import os

RUNTIME_GATE_FORCE_CLOSED = True
GOVERNED_AMAZON_FBM_LIVE_ENABLED = os.getenv("GOVERNED_AMAZON_FBM_LIVE_ENABLED", "false").lower() == "true"
RUNTIME_GATE_MESSAGE = "BT38 marketplace push/sync/import is disabled during governed-path rebuild."
APPROVED_AMAZON_FBM_PUSH_TYPE = "amazon_fbm_single_sku_inventory_push"


def is_runtime_allowed(command=None, *_args, **_kwargs) -> bool:
    """Return True only for the approved governed Amazon FBM live command."""
    if RUNTIME_GATE_FORCE_CLOSED:
        return False
    if not GOVERNED_AMAZON_FBM_LIVE_ENABLED:
        return False
    if command is None:
        return False

    payload = dict(getattr(command, "payload", {}) or {})
    approval = dict(getattr(command, "approval", {}) or {})
    if getattr(command, "dry_run", True):
        return False
    if getattr(command, "marketplace", None) != "amazon":
        return False
    if getattr(command, "action", None) != "push_inventory":
        return False
    if approval.get("approved") is not True:
        return False
    if approval.get("approval_type") != APPROVED_AMAZON_FBM_PUSH_TYPE:
        return False

    scope = approval.get("scope") or {}
    required = ("sku", "store_id", "listing_id", "quantity")
    if set(scope.keys()) != set(required):
        return False
    if any(key not in payload for key in required):
        return False
    return all(_normalize(scope[key]) == _normalize(payload[key]) for key in required)


def assert_runtime_allowed(command=None, *_args, **_kwargs) -> None:
    """Raise when runtime execution is not allowed."""
    if not is_runtime_allowed(command):
        raise RuntimeError(RUNTIME_GATE_MESSAGE)


def _normalize(value):
    if isinstance(value, str):
        return value.strip()
    return value
