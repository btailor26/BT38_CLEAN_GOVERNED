"""BT38 legacy push orchestration disabled during shutdown proof."""

from typing import Any, Dict

from old_path_shutdown import (
    GOVERNED_PATH_REQUIRED,
    MARKETPLACE_EXECUTION_DISABLED,
    OLD_SYNC_DISABLED,
    disabled_response,
)

SMART_PUSH_DISABLED = True
LEGACY_PUSH_ORCHESTRATION_DISABLED = True


def _blocked(action: str, **context: Any) -> Dict[str, Any]:
    result = disabled_response(action, **context)
    result["smart_push_disabled"] = True
    return result


def __getattr__(name: str):
    def disabled_callable(*args, **kwargs):
        return _blocked(name, args=args, kwargs=kwargs)

    return disabled_callable
