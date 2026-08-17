"""Exact post-submit Amazon MCF confirmation.

Amazon remains authority for both MCF lifecycle state and FBA inventory. This
module may verify the exact MCF order after submission, but it must never start
FBA inventory propagation. FBA-led group propagation begins only from a real
Amazon webhook signal and the existing exact FBA verifier.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from models import MCFOrder, SyncLog
from services.governed_mcf_execution import refresh_mcf_status


def confirm_exact_mcf_after_submission(
    *,
    mcf_order_id: int,
    source: str = "mcf_post_submit_exact_confirmation",
) -> dict[str, Any]:
    """Verify one exact MCF order without starting inventory propagation."""
    mcf = db.session.get(MCFOrder, int(mcf_order_id))
    if mcf is None:
        return {
            "success": False,
            "governed": True,
            "targeted": True,
            "reason": "mcf_order_missing",
            "mcf_order_id": int(mcf_order_id),
        }

    verified, status_result = refresh_mcf_status(mcf)

    # refresh_mcf_status deliberately treats Amazon's short post-create
    # visibility lag as success/pending_visibility. Never turn an accepted MCF
    # submission into a failure merely because getFulfillmentOrder is not yet
    # visible.
    confirmation_visible = bool(
        verified
        and not status_result.get("pending_visibility")
    )

    # Critical FBA contract:
    # submitting/confirming MCF is not inventory authority and must not queue a
    # marketplace push or an exact FBA inventory check. FBA verification is
    # woken only by Amazon webhook processing (ORDER_CHANGE or
    # FULFILLMENT_ORDER_STATUS), after which the existing changed-only FBA
    # verifier performs at most one group propagation for the new Amazon truth.

    # Persist diagnostic confirmation independently of the notification bell.
    # Real Amazon notifications remain the bell/webhook source.
    db.session.add(
        SyncLog(
            store_id=mcf.fba_store_id,
            status="success" if verified else "error",
            items_synced=1 if verified else 0,
            message=(
                "event_type=mcf_exact_confirmation "
                f"source={source} "
                f"mcf_order_id={mcf.id} "
                f"seller_fulfillment_order_id={mcf.seller_fulfillment_order_id} "
                f"amazon_order_id={mcf.amazon_order_id or ''} "
                f"amazon_status={mcf.amazon_status or ''} "
                f"pending_visibility={bool(status_result.get('pending_visibility'))} "
                "fba_verification_queued=false webhook_gate=true"
            )[:500],
            created_at=datetime.utcnow(),
        )
    )
    db.session.commit()

    return {
        "success": bool(verified),
        "governed": True,
        "targeted": True,
        "mcf_order_id": mcf.id,
        "seller_fulfillment_order_id": mcf.seller_fulfillment_order_id,
        "amazon_order_id": mcf.amazon_order_id,
        "amazon_status": mcf.amazon_status,
        "confirmation_visible": confirmation_visible,
        "pending_visibility": bool(status_result.get("pending_visibility")),
        "status_result": status_result,
        "fba_exact_verifications_queued": 0,
        "fba_verification_waiting_for_amazon_webhook": True,
        "full_scan_started": False,
        "marketplace_write_started": False,
    }
