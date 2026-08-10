"""Bounded recovery selectors for existing governed authorities.

This module does not create a worker, queue, importer, MCF writer, or grouping
authority. It selects stranded identities and hands each one back to the
existing governed implementation.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from extensions import db


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def recover_stranded_ebay_notifications(
    *,
    limit: int = 25,
    max_age_hours: int = 48,
) -> dict[str, Any]:
    """Replay unfinished captured eBay events through the existing path."""
    from services.governed_webhook_capture import mark_notification_status
    from services.governed_webhook_execution import process_marketplace_notification

    rows = db.session.execute(
        text(
            """
            SELECT id, payload_json
            FROM webhooks.ebay_notifications
            WHERE completed_at IS NULL
              AND received_at >= NOW() - (:max_age_hours * INTERVAL '1 hour')
              AND received_at <= NOW() - INTERVAL '2 minutes'
              AND processing_status IN ('RECEIVED', 'FAILED', 'PROCESSING')
            ORDER BY id ASC
            LIMIT :limit
            """
        ),
        {
            "max_age_hours": int(max_age_hours),
            "limit": int(limit),
        },
    ).mappings().all()

    recovered = 0
    failed = 0
    results = []

    for row in rows:
        notification_id = int(row["id"])
        payload = _decode_payload(row["payload_json"])

        if not payload:
            failed += 1
            mark_notification_status(
                "ebay",
                notification_id,
                processing_status="FAILED",
                last_error="startup_recovery_payload_missing",
                completed=True,
            )
            results.append({
                "notification_record_id": notification_id,
                "success": False,
                "reason": "payload_missing",
            })
            continue

        try:
            mark_notification_status(
                "ebay",
                notification_id,
                processing_status="PROCESSING",
                verification_status="PENDING",
            )
            result = process_marketplace_notification(
                marketplace="ebay",
                payload=payload,
                actor="startup_recovery",
                notification_record_id=notification_id,
            )
            success = bool(result.get("success", False))
            if success:
                mark_notification_status(
                    "ebay",
                    notification_id,
                    processing_status="COMPLETED",
                    verification_status="VERIFIED",
                    parsed=True,
                    completed=True,
                )
                recovered += 1
            else:
                reason = str(
                    result.get("reason")
                    or result.get("status")
                    or "startup_recovery_processing_failed"
                )
                mark_notification_status(
                    "ebay",
                    notification_id,
                    processing_status="FAILED",
                    last_error=reason[:4000],
                    parsed=True,
                    completed=True,
                )
                failed += 1

            results.append({
                "notification_record_id": notification_id,
                "success": success,
                "result": result,
            })
        except Exception as exc:
            db.session.rollback()
            failed += 1
            try:
                mark_notification_status(
                    "ebay",
                    notification_id,
                    processing_status="FAILED",
                    last_error=str(exc)[:4000],
                    completed=True,
                )
            except Exception:
                db.session.rollback()
            results.append({
                "notification_record_id": notification_id,
                "success": False,
                "reason": "exception",
                "error": str(exc),
            })

    return {
        "success": failed == 0,
        "selected": len(rows),
        "recovered": recovered,
        "failed": failed,
        "results": results,
        "full_scan_started": False,
        "new_worker_started": False,
        "new_queue_created": False,
    }


def recover_tracking_pending_mcf(
    *,
    limit: int = 10,
    max_age_hours: int = 72,
) -> dict[str, Any]:
    """Wake dispatched MCF orders whose Amazon tracking is still missing."""
    from models import MCFOrder, MarketplaceOrder
    from services.governed_mcf_execution import refresh_mcf_from_amazon_signal

    cutoff = datetime.utcnow() - timedelta(hours=int(max_age_hours))
    rows = (
        db.session.query(MCFOrder)
        .join(
            MarketplaceOrder,
            MarketplaceOrder.mcf_order_id == MCFOrder.id,
        )
        .filter(
            MCFOrder.created_at >= cutoff,
            MCFOrder.tracking_number.is_(None),
            MarketplaceOrder.shipped_at.isnot(None),
            db.or_(
                MCFOrder.status.is_(None),
                ~MCFOrder.status.in_(["cancelled", "failed"]),
            ),
        )
        .order_by(MCFOrder.id.asc())
        .distinct()
        .limit(int(limit))
        .all()
    )

    refreshed = 0
    failed = 0
    results = []

    for mcf in rows:
        seller_id = str(mcf.seller_fulfillment_order_id or "").strip()
        if not seller_id:
            results.append({
                "mcf_order_id": int(mcf.id),
                "success": True,
                "skipped": True,
                "reason": "seller_fulfillment_order_id_missing",
            })
            continue

        result = refresh_mcf_from_amazon_signal({
            "sellerFulfillmentOrderId": seller_id,
        })
        if result.get("success") or result.get("skipped"):
            refreshed += 1
        else:
            failed += 1
        results.append({
            "mcf_order_id": int(mcf.id),
            **result,
        })

    return {
        "success": failed == 0,
        "selected": len(rows),
        "refreshed": refreshed,
        "failed": failed,
        "results": results,
        "full_scan_started": False,
        "new_worker_started": False,
        "new_queue_created": False,
    }


def recover_missing_original_groups(
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Repair active linked Warehouse identities missing their original group."""
    from models import MarketplaceListing, WarehouseStock
    from services.governed_listing_refresh import ensure_permanent_original_group

    stocks = (
        db.session.query(WarehouseStock)
        .join(
            MarketplaceListing,
            MarketplaceListing.warehouse_stock_id == WarehouseStock.id,
        )
        .filter(
            WarehouseStock.is_active == True,  # noqa: E712
            WarehouseStock.is_deleted == False,  # noqa: E712
            WarehouseStock.master_product_group_id.is_(None),
            MarketplaceListing.is_active == True,  # noqa: E712
        )
        .order_by(WarehouseStock.id.asc())
        .distinct()
        .limit(int(limit))
        .all()
    )

    repaired = 0
    results = []

    for stock in stocks:
        group_id = ensure_permanent_original_group(stock)
        listings = (
            MarketplaceListing.query
            .filter(
                MarketplaceListing.warehouse_stock_id == stock.id,
                MarketplaceListing.is_active == True,  # noqa: E712
            )
            .all()
        )
        listing_ids = []
        for listing in listings:
            if listing.master_product_group_id is None:
                listing.master_product_group_id = int(group_id)
                listing.updated_at = datetime.utcnow()
            listing_ids.append(int(listing.id))

        db.session.commit()
        repaired += 1
        results.append({
            "warehouse_stock_id": int(stock.id),
            "original_group_id": int(group_id),
            "listing_ids": listing_ids,
        })

    return {
        "success": True,
        "selected": len(stocks),
        "repaired": repaired,
        "results": results,
        "full_scan_started": False,
        "new_worker_started": False,
        "new_queue_created": False,
    }


def run_bounded_startup_recovery_alignment(app) -> dict[str, Any]:
    """Run each bounded selector once after the deployed app has loaded."""
    with app.app_context():
        results = {
            "webhooks": recover_stranded_ebay_notifications(),
            "mcf_tracking": recover_tracking_pending_mcf(),
            "missing_groups": recover_missing_original_groups(),
        }
        results["success"] = all(
            bool(value.get("success"))
            for value in results.values()
            if isinstance(value, dict)
        )
        return results
