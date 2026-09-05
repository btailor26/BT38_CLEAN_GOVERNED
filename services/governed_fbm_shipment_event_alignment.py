"""Finish exact FBM shipment authority from the successful marketplace event.

This is a narrow wrapper around the existing governed webhook executor. The
canonical webhook handler runs first. Only when that exact event has already
committed a terminal lifecycle update for an existing order do we invoke the
existing exact marketplace shipment readbacks for that same order.

Rules:
- no worker, poller, scheduler, startup repair, page read or broad scan;
- no order replay, stock mutation or marketplace write;
- one event may inspect only its exact marketplace order identity;
- already-purchased FBM shipment authority skips provider/API recovery;
- Amazon FBA/AFN/MCF remains excluded;
- readback failure is best-effort and never changes webhook success.
"""
from __future__ import annotations

from typing import Any

import services.governed_webhook_execution as _execution


_ORIGINAL = _execution.process_marketplace_notification
_INSTALLED = False


def _text(value: Any) -> str:
    return str(value or "").strip()


def _terminal_existing_order(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    lifecycle = result.get("order_lifecycle")
    return bool(
        isinstance(lifecycle, dict)
        and lifecycle.get("handled") is True
        and lifecycle.get("terminal") is True
        and _text(result.get("status")).lower() == "order_lifecycle_updated"
    )


def _resolve_exact_order(*, marketplace: str, payload: dict, result: dict):
    """Resolve exactly one persisted store/order identity; never scan a window."""
    from extensions import db
    from models import MarketplaceOrder, Store

    lifecycle = result.get("order_lifecycle") if isinstance(result, dict) else None
    order_id = _text(
        (lifecycle or {}).get("order_id")
        or result.get("order_id")
        or payload.get("marketplace_order_id")
        or payload.get("order_id")
    )
    if not order_id:
        return None, [], "exact_order_id_missing"

    raw_store_id = payload.get("_bt38_store_id") or result.get("store_id")
    try:
        store_id = int(raw_store_id) if raw_store_id is not None else None
    except (TypeError, ValueError):
        store_id = None

    query = MarketplaceOrder.query.filter(
        MarketplaceOrder.marketplace_order_id == order_id
    )
    if store_id is not None:
        query = query.filter(MarketplaceOrder.store_id == store_id)
    else:
        query = query.join(Store, Store.id == MarketplaceOrder.store_id).filter(
            Store.platform.ilike(f"%{_text(marketplace).lower()}%")
        )

    rows = query.order_by(MarketplaceOrder.id).all()
    store_ids = sorted({int(row.store_id) for row in rows if row.store_id is not None})
    if not rows:
        return None, [], "exact_order_not_persisted"
    if len(store_ids) != 1:
        return None, rows, "exact_store_identity_ambiguous"

    store = db.session.get(Store, store_ids[0])
    if store is None:
        return None, rows, "exact_store_missing"
    return store, rows, None


def _already_has_purchased_authority(*, store_id: int, order_id: str) -> bool:
    from fbm_models import FBMShipment

    return (
        FBMShipment.query.filter_by(
            store_id=int(store_id),
            marketplace_order_id=str(order_id),
        )
        .filter(
            (FBMShipment.purchase_status == "purchased")
            | (FBMShipment.label_purchased_at.isnot(None))
        )
        .first()
        is not None
    )


def _align_exact_shipment_authority(*, marketplace: str, payload: dict, result: dict) -> dict:
    if not _terminal_existing_order(result):
        return {
            "success": True,
            "skipped": True,
            "reason": "not_terminal_existing_order_event",
            "broad_scan_started": False,
            "marketplace_write_started": False,
        }

    store, rows, reason = _resolve_exact_order(
        marketplace=marketplace,
        payload=payload,
        result=result,
    )
    if store is None:
        return {
            "success": True,
            "skipped": True,
            "reason": reason,
            "broad_scan_started": False,
            "marketplace_write_started": False,
        }

    order_id = _text(rows[0].marketplace_order_id)
    platform = _text(marketplace).lower()

    if _already_has_purchased_authority(store_id=store.id, order_id=order_id):
        return {
            "success": True,
            "skipped": True,
            "reason": "purchased_shipment_authority_already_persisted",
            "order_id": order_id,
            "store_id": int(store.id),
            "broad_scan_started": False,
            "marketplace_write_started": False,
        }

    if platform == "amazon":
        eligible = [
            row
            for row in rows
            if _text(getattr(row, "fulfillment_type", "")).upper()
            not in {"FBA", "AFN", "MCF", "AMAZON"}
            and not _text(getattr(row, "status", "")).lower().startswith("mcf_")
        ]
        if not eligible:
            return {
                "success": True,
                "skipped": True,
                "reason": "amazon_order_not_fbm",
                "order_id": order_id,
                "store_id": int(store.id),
                "broad_scan_started": False,
                "marketplace_write_started": False,
            }

        from services.governed_amazon_tracking_readback import (
            hydrate_amazon_tracking_for_order,
        )
        from services.governed_amazon_shipping_label_readback import (
            hydrate_amazon_purchased_label_for_order,
        )

        tracking = hydrate_amazon_tracking_for_order(
            store=store,
            marketplace_order_id=order_id,
            source="amazon_successful_lifecycle_event",
        )
        label = hydrate_amazon_purchased_label_for_order(
            store=store,
            marketplace_order_id=order_id,
            source="amazon_successful_lifecycle_event",
        )
        return {
            "success": True,
            "skipped": False,
            "reason": None,
            "order_id": order_id,
            "store_id": int(store.id),
            "amazon_tracking": tracking,
            "amazon_shipping_label": label,
            "broad_scan_started": False,
            "marketplace_write_started": False,
        }

    if platform == "ebay":
        # This function is already wrapped by the installed eBay Finances
        # alignment, so one exact fulfillment read can also persist confirmed
        # purchased eBay-label authority into the existing FBMShipment table.
        from services.governed_exact_ebay_order_hydration import (
            hydrate_exact_ebay_order,
        )

        hydration = hydrate_exact_ebay_order(
            store=store,
            marketplace_order_id=order_id,
            source="ebay_successful_lifecycle_event",
        )
        return {
            "success": True,
            "skipped": False,
            "reason": None,
            "order_id": order_id,
            "store_id": int(store.id),
            "ebay_hydration": hydration,
            "broad_scan_started": False,
            "marketplace_write_started": False,
        }

    return {
        "success": True,
        "skipped": True,
        "reason": "unsupported_marketplace",
        "order_id": order_id,
        "store_id": int(store.id),
        "broad_scan_started": False,
        "marketplace_write_started": False,
    }


def _aligned_process_marketplace_notification(*, marketplace: str, payload: dict, actor: str = "marketplace_webhook", notification_record_id: int | None = None):
    result = _ORIGINAL(
        marketplace=marketplace,
        payload=payload,
        actor=actor,
        notification_record_id=notification_record_id,
    )

    if not _terminal_existing_order(result):
        return result

    try:
        result["shipment_authority_alignment"] = _align_exact_shipment_authority(
            marketplace=marketplace,
            payload=dict(payload or {}),
            result=result,
        )
    except Exception as exc:
        # Exact marketplace readback is best-effort. The canonical event commit
        # remains successful and later exact events/actions can try again.
        result["shipment_authority_alignment"] = {
            "success": False,
            "skipped": False,
            "reason": "exact_shipment_authority_readback_failed",
            "error": str(exc)[:1000],
            "broad_scan_started": False,
            "marketplace_write_started": False,
        }
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _execution.process_marketplace_notification = _aligned_process_marketplace_notification
    _INSTALLED = True


install()
