"""Governed runtime action guard for BT38 execution paths.

This module performs only authorization decisions. It never calls marketplace
APIs, never enqueues jobs, and never mutates inventory/listing/store state.
"""

import logging
import os
from typing import Any, Dict, Optional

READ_ONLY_ACTIONS = {"preview", "read", "read_only", "status", "audit"}
RUNTIME_ACTIONS = {"push", "sync", "import"}
VALID_ACTIONS = READ_ONLY_ACTIONS | RUNTIME_ACTIONS


def _store_value(store: Any, attr: str, default: Any = None) -> Any:
    return getattr(store, attr, default) if store is not None else default


def _structured_result(store: Any, action_type: str, manual: bool, allowed: bool, reason: str) -> Dict[str, Any]:
    return {
        "allowed": allowed,
        "reason": reason,
        "action_type": action_type,
        "manual": manual,
        "store_id": _store_value(store, "id"),
        "store_name": _store_value(store, "name"),
    }



def _flag_disabled(store: Any, attr: str) -> bool:
    """Return True only when a store setting exists and is explicitly falsey."""
    if store is None or not hasattr(store, attr):
        return False
    value = getattr(store, attr)
    if isinstance(value, str):
        return value.strip().lower() in {"0", "false", "no", "off", "disabled"}
    return value is False

def _system_config_disabled(action_type: str) -> Optional[str]:
    """Best-effort read of global runtime config without mutating state."""
    env_execution_mode = os.getenv("EXECUTION_MODE", "").strip().lower()
    if env_execution_mode in {"read-only", "readonly", "disabled"} and action_type in RUNTIME_ACTIONS:
        return f"EXECUTION_MODE={env_execution_mode} blocks runtime {action_type}"

    if action_type == "push" and os.getenv("PUSH_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return "PUSH_ENABLED disables push execution"

    if action_type == "push" and os.getenv("ENABLE_PUSH_JOBS", "true").strip().lower() in {"0", "false", "no", "off"}:
        return "ENABLE_PUSH_JOBS disables push execution"

    key_candidates = {
        "push": ("push_enabled", "runtime_push_enabled", "marketplace_push_enabled"),
        "sync": ("sync_enabled", "runtime_sync_enabled", "marketplace_sync_enabled"),
        "import": ("import_enabled", "runtime_import_enabled", "marketplace_import_enabled"),
    }.get(action_type, ())

    try:
        from models import SystemConfig
        for key in key_candidates:
            config = SystemConfig.query.filter_by(key=key).first()
            if config and str(config.value).strip().lower() in {"0", "false", "no", "off", "disabled"}:
                return f"SystemConfig {key} disables {action_type} execution"
    except Exception as exc:
        logging.debug("Runtime action guard skipped SystemConfig lookup: %s", exc)

    return None


def is_runtime_action_allowed(store, action_type, manual=False, context=None):
    """Return a structured authorization decision for runtime actions.

    The guard allows read-only/preview actions, blocks unknown action types, and
    fail-closes push/sync/import execution when global or store-level controls do
    not permit runtime work. Fulfillment-specific SKU/listing checks are handled
    by marketplace_push_eligibility() at the marketplace guard layer.
    """
    normalized_action = (action_type or "").strip().lower()
    manual = bool(manual)
    context = context or {}

    if normalized_action not in VALID_ACTIONS:
        return _structured_result(store, normalized_action, manual, False, "Unknown or unsupported runtime action type")

    if normalized_action in READ_ONLY_ACTIONS:
        return _structured_result(store, normalized_action, manual, True, "Read-only runtime action allowed")

    global_block = _system_config_disabled(normalized_action)
    if global_block:
        return _structured_result(store, normalized_action, manual, False, global_block)

    if store is None:
        return _structured_result(store, normalized_action, manual, False, "Store is required for runtime execution")

    if not bool(_store_value(store, "is_active", False)):
        return _structured_result(store, normalized_action, manual, False, "Store is inactive")

    store_mode = str(_store_value(store, "store_mode", "live") or "live").strip().lower()
    if store_mode != "live" and normalized_action in {"push", "sync"}:
        return _structured_result(store, normalized_action, manual, False, f"store_mode={store_mode} blocks {normalized_action} execution")

    if normalized_action == "push" and any(_flag_disabled(store, attr) for attr in ("push_enabled", "auto_push_enabled", "marketplace_push_enabled")):
        return _structured_result(store, normalized_action, manual, False, "Store push setting disables push execution")

    if normalized_action == "sync" and any(_flag_disabled(store, attr) for attr in ("sync_enabled", "auto_sync_enabled", "marketplace_sync_enabled")):
        return _structured_result(store, normalized_action, manual, False, "Store sync setting disables sync execution")

    if normalized_action == "import" and any(_flag_disabled(store, attr) for attr in ("import_enabled", "fba_import_enabled", "marketplace_import_enabled")):
        return _structured_result(store, normalized_action, manual, False, "Store import setting disables import execution")

    if normalized_action in RUNTIME_ACTIONS and not _store_value(store, "api_key"):
        return _structured_result(store, normalized_action, manual, False, "Store credentials are missing")

    platform = str(_store_value(store, "platform", "") or "").strip().lower()
    fulfillment_type = str(_store_value(store, "fulfillment_type", "") or "").strip().upper()
    if normalized_action == "push" and ("fba" in platform or fulfillment_type == "FBA"):
        return _structured_result(store, normalized_action, manual, False, "Amazon FBA/read-only stores cannot push")

    return _structured_result(store, normalized_action, manual, True, "Runtime action allowed")
