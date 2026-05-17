"""Shared fail-closed contract for retired BT38 marketplace execution paths."""

from __future__ import annotations

import logging
from typing import Any, Dict

OLD_SYNC_DISABLED = True
MARKETPLACE_EXECUTION_DISABLED = True
GOVERNED_PATH_REQUIRED = True
FBA_READ_ONLY = True
FBM_DISABLED_UNTIL_GOVERNED_PATH = True
FUTURE_MARKETPLACES_REQUIRE_GOVERNED_PATH = True

DISABLED_MESSAGE = (
    "Old marketplace sync/push/import execution is disabled. "
    "Use the approved governed dispatcher/executor path only after it is built."
)


def disabled_response(action: str, **context: Any) -> Dict[str, Any]:
    """Return the standard blocked response for any retired execution path."""
    logging.warning("[OLD_SYNC_DISABLED] %s blocked; governed path is required.", action)
    return {
        "success": False,
        "ok": False,
        "execution_blocked": True,
        "old_sync_disabled": OLD_SYNC_DISABLED,
        "marketplace_execution_disabled": MARKETPLACE_EXECUTION_DISABLED,
        "governed_path_required": GOVERNED_PATH_REQUIRED,
        "fba_read_only": FBA_READ_ONLY,
        "fbm_disabled_until_governed_path": FBM_DISABLED_UNTIL_GOVERNED_PATH,
        "future_marketplaces_require_governed_path": FUTURE_MARKETPLACES_REQUIRE_GOVERNED_PATH,
        "action": action,
        "context": context,
        "error": DISABLED_MESSAGE,
    }


class DisabledMarketplaceService:
    """Compatibility object whose methods all fail closed."""

    OLD_SYNC_DISABLED = OLD_SYNC_DISABLED
    MARKETPLACE_EXECUTION_DISABLED = MARKETPLACE_EXECUTION_DISABLED
    GOVERNED_PATH_REQUIRED = GOVERNED_PATH_REQUIRED

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.init_args = args
        self.init_kwargs = kwargs

    def _blocked(self, action: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return disabled_response(action, args=args, kwargs=kwargs)

    def __getattr__(self, name: str):
        def disabled_callable(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            return self._blocked(name, *args, **kwargs)

        return disabled_callable
