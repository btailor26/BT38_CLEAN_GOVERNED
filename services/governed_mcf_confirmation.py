"""Exact post-submit Amazon MCF confirmation and FBA verification handoff.

Amazon remains authority for both MCF lifecycle state and FBA inventory. This
module never derives an FBA quantity from the source marketplace sale and never
starts a marketplace-wide inventory scan.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from extensions import db
from models import MCFOrder, SyncLog
from services.governed_mcf_execution import refresh_mcf_status


def confirm_exact_mcf_after_submission(
    *,
    mcf_order_id: int,
    source: str = "mcf_post_submit_exact_confirmation",
) -> dict[str, Any]:
    """Verify one exact MCF order and queue exact FBA truth checks for its SKUs."""
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

    queued = []
    if verified:
        from services.governed_runtime_engine import notify_governed_runtime_work

        seen = set()
        for item in mcf.items.all():
            seller_sku = str(getattr(item, "fba_sku", None) or "").strip()
            if not seller_sku or seller_sku in seen:
                continue
            seen.add(seller_sku)
            queued.append(
                notify_governed_runtime_work(
                    source="mcf_exact_fba_verification",
                    event={
                        "event_type": "fba_inventory_alignment",
                        "marketplace": "amazon_fba",
                        "store_id": mcf.fba_store_id,
                        "seller_sku": seller_sku,
                        "verify_after": datetime.utcnow() + timedelta(seconds=30),
                        "payload": {
                            "mcf_order_id": mcf.id,
                            "seller_fulfillment_order_id": (
                                mcf.seller_fulfillment_order_id
                            ),
                            "source_order_id": mcf.source_order_id,
                        },
                    },
                )
            )

    # Persist diagnostic confirmation independently of the notification bell.
    # Real FULFILLMENT_ORDER_STATUS notifications remain the bell/webhook source.
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
                f"pending_visibility={bool(status_result.get('pending_visibility'))}"
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
        "fba_exact_verifications_queued": len(queued),
        "full_scan_started": False,
        "marketplace_write_started": False,
    }
