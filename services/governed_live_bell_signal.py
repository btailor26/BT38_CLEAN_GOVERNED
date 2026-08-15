"""
BT38 governed live bell wake signal.

This is transport only.

DB remains event truth.
The signal contains no marketplace data and persists nothing.

Flow:
committed existing DB event -> in-process condition -> SSE wake -> bell light.

No database polling.
No marketplace polling.
No scheduler.
No event table.
"""

from __future__ import annotations

import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

from models import (
    MarketplaceListing,
    MarketplaceOrder,
    SyncLog,
    SystemLog,
)


_live_condition = threading.Condition()
_live_sequence = 0


def current_live_bell_sequence() -> int:
    with _live_condition:
        return int(_live_sequence)


def notify_live_bell() -> int:
    global _live_sequence

    with _live_condition:
        _live_sequence += 1
        sequence = int(_live_sequence)
        _live_condition.notify_all()
        return sequence


def wait_for_live_bell(
    previous_sequence: int,
    timeout: float = 25.0,
) -> int:
    """Wait in memory until a committed relevant event occurs.

    timeout exists only to allow an SSE keepalive frame.
    It performs no DB/API work.
    """
    previous_sequence = int(previous_sequence or 0)

    with _live_condition:
        if _live_sequence == previous_sequence:
            _live_condition.wait(timeout=float(timeout))
        return int(_live_sequence)


def _sync_log_is_bell_event(row: SyncLog) -> bool:
    message = str(getattr(row, "message", "") or "").strip().lower()

    return (
        message.startswith("event_type=marketplace_push")
        or message.startswith("event_type=product_linking_")
    )


def _system_log_is_bell_event(row: SystemLog) -> bool:
    return str(
        getattr(row, "log_type", "") or ""
    ).strip().lower() == "marketplace_webhook"


@event.listens_for(Session, "before_flush")
def _bt38_live_bell_before_flush(
    session,
    flush_context,
    instances,
):
    # One wake per transaction is enough. The bell will read canonical DB
    # truth later; it does not need one socket message per row.
    if session.info.get("_bt38_live_bell_pending"):
        return

    for row in session.new:
        if isinstance(row, (MarketplaceOrder, MarketplaceListing)):
            session.info["_bt38_live_bell_pending"] = True
            return

        if isinstance(row, SyncLog) and _sync_log_is_bell_event(row):
            session.info["_bt38_live_bell_pending"] = True
            return

        if isinstance(row, SystemLog) and _system_log_is_bell_event(row):
            session.info["_bt38_live_bell_pending"] = True
            return


@event.listens_for(Session, "after_commit")
def _bt38_live_bell_after_commit(session):
    if session.info.pop("_bt38_live_bell_pending", False):
        notify_live_bell()


@event.listens_for(Session, "after_rollback")
def _bt38_live_bell_after_rollback(session):
    # A failed/rolled-back DB operation must never light the bell.
    session.info.pop("_bt38_live_bell_pending", None)
