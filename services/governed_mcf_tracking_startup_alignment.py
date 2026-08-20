"""Event-driven MCF split-tracking alignment.

BT38 must not poll Amazon MCF on a timer. Amazon SQS/webhook notifications are
wake-up signals only; the existing Fulfillment Outbound status lookup remains
the tracking authority and the existing governed eBay CompleteSale adapter
remains the only marketplace write path.

When an exact Amazon MCF signal is received:
- refresh that exact MCF order from Amazon;
- retain every package tracking number in the durable MCF tracking state;
- if the source eBay order is already dispatched, send the complete current
  tracking set to eBay in one CompleteSale call;
- mark that exact tracking set as forwarded only after eBay succeeds;
- publish one committed UI event so Orders / MCF rereads DB state immediately.

There is no interval, periodic retry, startup scan, 24-hour refresh, or second
worker/scheduler in this module.
"""
from __future__ import annotations

from datetime import datetime


def _source_lines_for_mcf(mcf_order_id: int):
    from models import MarketplaceOrder

    return (
        MarketplaceOrder.query
        .filter(MarketplaceOrder.mcf_order_id == int(mcf_order_id))
        .order_by(MarketplaceOrder.id)
        .all()
    )


def _tracking_numbers(details) -> list[str]:
    values = []
    seen = set()
    for item in details or []:
        number = str(item.get("tracking_number") or "").strip()
        if number and number not in seen:
            seen.add(number)
            values.append(number)
    return values


def _publish_mcf_ui_event(mcf, lines, details, *, reason: str) -> None:
    """Wake the shared Orders / MCF UI only after committed state exists."""
    try:
        from services.governed_ui_event_signal import publish_governed_ui_event

        anchor = lines[0] if lines else None
        publish_governed_ui_event(
            source="amazon_mcf_tracking",
            scope={
                "event_type": "mcf_tracking_updated",
                "order_id": getattr(anchor, "marketplace_order_id", None),
                "store_id": getattr(anchor, "store_id", None),
                "mcf_order_id": getattr(mcf, "id", None),
                "tracking_numbers": _tracking_numbers(details),
                "tracking_count": len(_tracking_numbers(details)),
                "reason": reason,
            },
        )
    except Exception:
        # UI notification is observational. Never roll back or fail a confirmed
        # Amazon/eBay tracking commit because a browser wake signal failed.
        return


def _forward_current_tracking_set(mcf_order_id: int, *, source: str) -> dict:
    """Send every currently known Amazon package tracking number to eBay."""
    from extensions import db
    from models import MCFOrder
    from services.governed_ebay_dispatch import complete_sale
    from services.governed_mcf_tracking import (
        has_unforwarded_tracking,
        load_tracking_details,
        mark_tracking_forwarded,
    )
    from services.runtime_action_guard import is_runtime_action_allowed

    mcf = db.session.get(MCFOrder, int(mcf_order_id))
    if mcf is None:
        return {
            "success": False,
            "skipped": False,
            "reason": "mcf_order_missing",
            "mcf_order_id": int(mcf_order_id),
        }

    lines = _source_lines_for_mcf(mcf.id)
    if not lines:
        return {
            "success": False,
            "skipped": False,
            "reason": "mcf_source_marketplace_order_missing",
            "mcf_order_id": mcf.id,
        }

    # Tracking enrichment is a later phase. Once eBay is already marked
    # dispatched, a refreshed Amazon timestamp must never reopen the one-hour
    # cancellation window.
    if not any(getattr(line, "shipped_at", None) for line in lines):
        return {
            "success": True,
            "skipped": True,
            "reason": "source_marketplace_dispatch_pending",
            "mcf_order_id": mcf.id,
        }

    details = load_tracking_details(mcf.id)
    numbers = _tracking_numbers(details)
    if not details:
        return {
            "success": True,
            "skipped": True,
            "reason": "amazon_mcf_tracking_not_available_yet",
            "mcf_order_id": mcf.id,
            "tracking_numbers": [],
        }

    if not has_unforwarded_tracking(mcf.id):
        _publish_mcf_ui_event(
            mcf,
            lines,
            details,
            reason="mcf_tracking_current_set_already_forwarded",
        )
        return {
            "success": True,
            "skipped": True,
            "reason": "mcf_tracking_current_set_already_forwarded",
            "mcf_order_id": mcf.id,
            "tracking_numbers": numbers,
            "tracking_count": len(numbers),
        }

    anchor = lines[0]
    store = getattr(anchor, "store", None)
    if store is None or "ebay" not in str(store.platform or "").lower():
        return {
            "success": True,
            "skipped": True,
            "reason": "mcf_source_marketplace_tracking_not_supported",
            "mcf_order_id": mcf.id,
            "tracking_numbers": numbers,
        }

    guard = is_runtime_action_allowed(
        store,
        "push",
        manual=False,
        context={
            "actor_user": None,
            "context": "mcf_tracking_amazon_event_enrichment",
        },
    )
    if not guard.get("allowed"):
        return {
            "success": False,
            "skipped": False,
            "reason": "mcf_tracking_ebay_enrichment_blocked",
            "mcf_order_id": mcf.id,
            "tracking_numbers": numbers,
            "error": guard.get("reason"),
        }

    primary = details[0]
    dispatch = complete_sale(
        anchor,
        carrier=primary.get("carrier") or mcf.carrier or "Other",
        tracking_number=(
            primary.get("tracking_number")
            or mcf.tracking_number
        ),
        tracking_details=details,
    )
    if not dispatch.get("success"):
        now = datetime.utcnow()
        for line in lines:
            line.status = "mcf_tracking_update_failed"
            line.error_message = dispatch.get("error")
            line.updated_at = now
        db.session.commit()
        return {
            "success": False,
            "skipped": False,
            "reason": "mcf_tracking_ebay_update_failed",
            "mcf_order_id": mcf.id,
            "tracking_numbers": numbers,
            "error": dispatch.get("error"),
        }

    now = datetime.utcnow()
    for line in lines:
        # Preserve the first value in the legacy scalar column. The complete set
        # remains authoritative in governed_mcf_tracking and is supplied to eBay.
        line.carrier = primary.get("carrier") or mcf.carrier
        line.tracking_number = (
            primary.get("tracking_number")
            or mcf.tracking_number
        )
        line.shipped_at = line.shipped_at or anchor.shipped_at or now
        line.status = "mcf_tracking_updated"
        line.error_message = None
        line.updated_at = now

    mark_tracking_forwarded(mcf.id)
    db.session.commit()
    _publish_mcf_ui_event(
        mcf,
        lines,
        details,
        reason=source,
    )

    return {
        "success": True,
        "skipped": False,
        "reason": "mcf_tracking_enriched_all_packages",
        "mcf_order_id": mcf.id,
        "amazon_status": mcf.amazon_status,
        "carrier": mcf.carrier,
        "tracking_number": mcf.tracking_number,
        "tracking_numbers": numbers,
        "tracking_count": len(numbers),
        "ui_event_published": True,
    }


def _mcf_ui_read_page() -> bool:
    """True only for read-only Orders / MCF HTML requests."""
    try:
        from flask import has_request_context, request

        if not has_request_context() or request.method != "GET":
            return False
        path = request.path.rstrip("/") or "/"
        return path == "/orders-mcf" or path.startswith("/orders-mcf/")
    except Exception:
        return False


def _install_multi_tracking_ui_projection() -> bool:
    """Project the durable tracking set into existing MCF HTML without writes.

    The database keeps the legacy primary tracking scalar unchanged. On the
    read-only Orders / MCF pages only, the existing template sees a joined
    display value when Amazon supplied multiple package tracking numbers.
    """
    from models import MCFOrder, MarketplaceOrder
    from services.governed_mcf_tracking import load_tracking_details

    installed = False

    current_mcf_get = MCFOrder.__getattribute__
    if not getattr(current_mcf_get, "_bt38_mcf_multi_tracking_ui", False):
        def mcf_getattribute(self, name):
            value = current_mcf_get(self, name)
            if name != "tracking_number" or not _mcf_ui_read_page():
                return value
            try:
                mcf_id = current_mcf_get(self, "id")
                numbers = _tracking_numbers(load_tracking_details(mcf_id))
                return " · ".join(numbers) if len(numbers) > 1 else value
            except Exception:
                return value

        mcf_getattribute._bt38_mcf_multi_tracking_ui = True
        MCFOrder.__getattribute__ = mcf_getattribute
        installed = True

    current_order_get = MarketplaceOrder.__getattribute__
    if not getattr(current_order_get, "_bt38_mcf_multi_tracking_ui", False):
        def order_getattribute(self, name):
            value = current_order_get(self, name)
            if name != "tracking_number" or not _mcf_ui_read_page():
                return value
            try:
                mcf_id = current_order_get(self, "mcf_order_id")
                if not mcf_id:
                    return value
                numbers = _tracking_numbers(load_tracking_details(mcf_id))
                return " · ".join(numbers) if len(numbers) > 1 else value
            except Exception:
                return value

        order_getattribute._bt38_mcf_multi_tracking_ui = True
        MarketplaceOrder.__getattribute__ = order_getattribute
        installed = True

    return installed


def install_mcf_tracking_startup_alignment() -> bool:
    """Install event-only MCF tracking completion and read-only UI projection."""
    import services.governed_mcf_execution as execution

    installed = _install_multi_tracking_ui_projection()

    current_signal = execution.refresh_mcf_from_amazon_signal
    if getattr(current_signal, "_bt38_mcf_event_tracking_aligned", False):
        return installed

    def aligned_signal(payload: dict):
        result = current_signal(payload)
        mcf_id = result.get("mcf_order_id")
        try:
            mcf_id = int(mcf_id)
        except (TypeError, ValueError):
            return result

        # The canonical signal path already refreshes Amazon and may already
        # forward the full tracking set. If anything remains unforwarded, finish
        # it immediately on this same event; never schedule a timer retry.
        from services.governed_mcf_tracking import (
            has_unforwarded_tracking,
            load_tracking_details,
        )

        details = load_tracking_details(mcf_id)
        if details and has_unforwarded_tracking(mcf_id):
            completion = _forward_current_tracking_set(
                mcf_id,
                source="amazon_mcf_event_tracking_completion",
            )
            result["mcf_tracking_completion"] = completion
            if not completion.get("success", False) and not completion.get(
                "skipped", False
            ):
                return completion
            return completion if not completion.get("skipped") else result

        if details:
            lines = _source_lines_for_mcf(mcf_id)
            mcf = next(
                (
                    line.mcf_order
                    for line in lines
                    if line.mcf_order is not None
                ),
                None,
            )
            if mcf is not None:
                _publish_mcf_ui_event(
                    mcf,
                    lines,
                    details,
                    reason=str(result.get("reason") or "amazon_mcf_event"),
                )

        return result

    aligned_signal._bt38_mcf_event_tracking_aligned = True
    execution.refresh_mcf_from_amazon_signal = aligned_signal
    return True


install_mcf_tracking_startup_alignment()
