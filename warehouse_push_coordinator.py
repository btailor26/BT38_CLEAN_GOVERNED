"""BT38 legacy warehouse push coordination disabled during shutdown proof."""

from typing import Any, Dict

from old_path_shutdown import (
    GOVERNED_PATH_REQUIRED,
    MARKETPLACE_EXECUTION_DISABLED,
    OLD_SYNC_DISABLED,
    disabled_response,
)

WAREHOUSE_PUSH_COORDINATOR_DISABLED = True
LEGACY_WAREHOUSE_PUSH_DISABLED = True


def _blocked(action: str, **context: Any) -> Dict[str, Any]:
    result = disabled_response(action, **context)
    result["warehouse_push_coordinator_disabled"] = True
    return result


def __getattr__(name: str):
    def disabled_callable(*args, **kwargs):
        return _blocked(name, args=args, kwargs=kwargs)

    return disabled_callable
