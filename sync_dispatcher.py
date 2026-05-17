"""BT38 marketplace worker startup disabled.

This compatibility shell keeps the old import surface available while preventing
any background marketplace worker, poller, or scheduled import loop from
starting during the shutdown proof phase.
"""

import logging

from old_path_shutdown import (
    GOVERNED_PATH_REQUIRED,
    MARKETPLACE_EXECUTION_DISABLED,
    OLD_SYNC_DISABLED,
    disabled_response,
)

WORKERS_DISABLED = True
_app_instance = None


class WorkerStartupDisabled:
    """Compatibility object for retired worker/scheduler interfaces."""

    running = False
    OLD_SYNC_DISABLED = OLD_SYNC_DISABLED
    MARKETPLACE_EXECUTION_DISABLED = MARKETPLACE_EXECUTION_DISABLED
    GOVERNED_PATH_REQUIRED = GOVERNED_PATH_REQUIRED

    def start(self):
        return disabled_response("worker_start")

    def stop(self):
        logging.warning("[WORKERS_DISABLED] Marketplace dispatcher/scheduler is already disabled.")
        return disabled_response("worker_stop")


def set_app_instance(app):
    """Keep compatibility with app.py without enabling worker execution."""
    global _app_instance
    _app_instance = app
    logging.warning(
        "[WORKERS_DISABLED] Flask app instance stored for compatibility only; "
        "worker execution remains disabled."
    )
    return disabled_response("set_app_instance")


def get_dispatcher():
    """Return a disabled dispatcher compatibility object."""
    return WorkerStartupDisabled()


def start_dispatcher():
    """Do not start any marketplace dispatcher work."""
    logging.warning("[WORKERS_DISABLED] start_dispatcher() blocked")
    return disabled_response("start_dispatcher")


def stop_dispatcher():
    """Compatibility no-op for disabled dispatcher."""
    return disabled_response("stop_dispatcher")


def get_order_import_scheduler():
    """Return a disabled scheduler compatibility object."""
    return WorkerStartupDisabled()


def start_order_import_scheduler():
    """Do not start any scheduled marketplace order import work."""
    logging.warning("[WORKERS_DISABLED] start_order_import_scheduler() blocked")
    return disabled_response("start_order_import_scheduler")


def stop_order_import_scheduler():
    """Compatibility no-op for disabled order scheduler."""
    return disabled_response("stop_order_import_scheduler")
