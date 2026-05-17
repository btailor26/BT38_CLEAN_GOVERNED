"""
BT38 marketplace worker startup disabled.

This module intentionally keeps the old sync_dispatcher import surface alive
while preventing any background dispatcher thread, store worker thread, or
order-import scheduler from starting.

Reason:
- BT38 must have one clean execution path only.
- Legacy/background workers must be disabled first before the new path is added.
- App imports should continue to load safely while execution is fail-closed.

No marketplace sync, push, import, scheduler, polling loop, or worker thread
is started from this module.
"""

import logging

_app_instance = None


class WorkerStartupDisabled:
    """No-op compatibility object for retired worker/scheduler interfaces."""

    running = False

    def start(self):
        logging.warning(
            "[WORKERS_DISABLED] Marketplace dispatcher/scheduler startup is disabled. "
            "No background worker thread was started."
        )
        return None

    def stop(self):
        logging.warning(
            "[WORKERS_DISABLED] Marketplace dispatcher/scheduler is already disabled."
        )
        return None


def set_app_instance(app):
    """Keep compatibility with app.py without enabling worker execution."""
    global _app_instance
    _app_instance = app
    logging.warning(
        "[WORKERS_DISABLED] Flask app instance stored for compatibility only; "
        "worker execution remains disabled."
    )
    return None


def get_dispatcher():
    """Return a disabled dispatcher compatibility object."""
    return WorkerStartupDisabled()


def start_dispatcher():
    """Do not start any marketplace dispatcher thread."""
    logging.warning(
        "[WORKERS_DISABLED] start_dispatcher() blocked. "
        "No dispatcher loop, store queue worker, import, sync, or push worker was started."
    )
    return None


def stop_dispatcher():
    """Compatibility no-op for disabled dispatcher."""
    logging.warning("[WORKERS_DISABLED] stop_dispatcher() no-op; dispatcher is disabled.")
    return None


def get_order_import_scheduler():
    """Return a disabled scheduler compatibility object."""
    return WorkerStartupDisabled()


def start_order_import_scheduler():
    """Do not start any scheduled marketplace order import loop."""
    logging.warning(
        "[WORKERS_DISABLED] start_order_import_scheduler() blocked. "
        "No scheduled order import loop was started."
    )
    return None


def stop_order_import_scheduler():
    """Compatibility no-op for disabled order scheduler."""
    logging.warning(
        "[WORKERS_DISABLED] stop_order_import_scheduler() no-op; scheduler is disabled."
    )
    return None
