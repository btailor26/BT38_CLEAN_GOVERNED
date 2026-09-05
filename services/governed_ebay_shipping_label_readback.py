"""Persist exact eBay purchased-label shipment authority into existing FBMShipment.

The existing exact eBay hydration owns the Fulfillment API read and the existing
Finances reader owns monetary SHIPPING_LABEL truth.  This module joins those two
already-governed facts for one exact order.  It never infers a carrier from a
tracking number and it never creates an order, marketplace write, worker or
poller.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder
from services.governed_exact_ebay_order_hydration import (
    _fulfillment_line_ids,
    _fulfillment_truth,
    _fulfillment_values,
)
from services.governed_marketplace_order_import import _ebay_access_token, _text


def _fulfillment_id(value: dict[str, Any]) -> str:
    return _text(
        value.get("fulfillmentId")
        or value.get("shippingFulfillmentId")
        or value.get("shipmentId")
    )


def _service_value(value: dict[str, Any]) -> str:
    return _text(
        value.get("shippingServiceCode")
        or value.get("shippingServiceName")
        or value.get("service")
    )


def _confirmed_finance_purchase(*, store_id: int, order_id: str) -> dict[str, Any] | None:
    """Return persisted exact-order eBay label spend only when a debit is confirmed.

    Refund/adjustment rows have distinct sources and cannot establish purchase
    authority here.
    """
    row = db.session.execute(
        text(
            """
            SELECT MIN(recorded_at) AS purchased_at,
                   COUNT(*) AS purchase_rows,
                   MIN(currency) AS currency,
                   SUM(ABS(amount)) AS amount
            FROM shipping_spend_ledger
            WHERE store_id = :store_id
              AND marketplace_order_id = :order_id
              AND fulfillment_family = 'FBM'
              AND provider = 'ebay'
              AND source = 'ebay_finances_shipping_label'
              AND confirmed = TRUE
            """
        ),
        {"store_id": int(store_id), "order_id": order_id},
    ).mappings().first()
    if not row or int(row.get("purchase_rows") or 0) < 1:
        return None
    return dict(row)


def _unique_fulfillment_candidates(fulfillments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse duplicate payload rows by durable fulfillment id only."""
    candidates: dict[str, dict[str, Any]] = {}
    for fulfillment in fulfillments:
        fulfillment_id = _fulfillment_id(fulfillment)
        if not fulfillment_id:
            continue
        values = _fulfillment_values(fulfillment)
        candidate = {
            "fulfillment_id": fulfillment_id,
            "carrier": values.get("carrier") or None,
            "tracking_number": values.get("tracking_number") or None,
            "shipped_at": values.get("shipped_at"),
            "service": _service_value(fulfillment) or None,
            "line_item_ids": sorted(_fulfillment_line_ids(fulfillment)),
        }
        existing = candidates.get(fulfillment_id)
        if existing is None:
            candidates[fulfillment_id] = candidate
            continue
        # A repeated fulfillment id is safe only when its physical identity does
        # not conflict with the first copy returned by eBay.
        for key in ("carrier", "tracking_number", "service"):
            if existing.get(key) and candidate.get(key) and existing[key] != candidate[key]:
                candidates.pop(fulfillment_id, None)
                break
        else:
            if not existing.get("carrier"):
                existing["carrier"] = candidate.get("carrier")
            if not existing.get("tracking_number"):
                existing["tracking_number"] = candidate.get("tracking_number")
            if not existing.get("service"):
                existing["service"] = candidate.get("service")
            if existing.get("shipped_at") is None:
                existing["shipped_at"] = candidate.get("shipped_at")
            existing["line_item_ids"] = sorted(set(existing["line_item_ids"]) | set(candidate["line_item_ids"]))
    return list(candidates.values())


def persist_exact_ebay_purchased_shipment_authority(*, store, marketplace_order_id: str) -> dict[str, Any]:
    """Join exact eBay finance purchase proof to exact fulfillment identity.

    The join deliberately requires one unambiguous fulfillment id for the exact
    order.  Multiple physical fulfillments are not collapsed into a fabricated
    order-level shipment.  Tracking is optional; fulfillmentId remains durable
    shipment identity.
    """
    order_id = _text(marketplace_order_id)
    if not order_id:
        return {"success": False, "skipped": True, "reason": "ebay_order_id_missing"}

    rows = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == store.id,
            MarketplaceOrder.marketplace_order_id == order_id,
        )
        .order_by(MarketplaceOrder.id.asc())
        .all()
    )
    if not rows:
        return {"success": False, "skipped": True, "reason": "existing_marketplace_order_missing", "order_id": order_id}

    purchase = _confirmed_finance_purchase(store_id=store.id, order_id=order_id)
    if purchase is None:
        return {"success": False, "skipped": True, "reason": "ebay_shipping_label_purchase_not_confirmed", "order_id": order_id}

    try:
        access_token = _ebay_access_token(store)
        fulfillments, fulfillment_error = _fulfillment_truth(
            access_token=access_token,
            order_id=order_id,
        )
    except Exception as exc:
        return {"success": False, "skipped": False, "reason": f"ebay_fulfillment_read_failed:{exc}", "order_id": order_id}

    if fulfillment_error:
        return {"success": False, "skipped": False, "reason": fulfillment_error, "order_id": order_id}

    candidates = _unique_fulfillment_candidates(fulfillments)
    if len(candidates) != 1:
        return {
            "success": False,
            "skipped": True,
            "reason": "ebay_fulfillment_ambiguous" if candidates else "ebay_fulfillment_identity_missing",
            "order_id": order_id,
            "fulfillments_seen": len(fulfillments),
            "candidate_count": len(candidates),
        }

    candidate = candidates[0]
    fulfillment_id = candidate["fulfillment_id"]
    shipment = (
        FBMShipment.query
        .filter_by(
            store_id=store.id,
            marketplace_order_id=order_id,
            provider="ebay_shipping",
            provider_shipment_id=fulfillment_id,
        )
        .first()
    )
    created = shipment is None
    if shipment is None:
        shipment = FBMShipment(
            store_id=store.id,
            marketplace_order_id=order_id,
            provider="ebay_shipping",
            provider_shipment_id=fulfillment_id,
            purchase_key=f"ebay_shipping_readback:{store.id}:{order_id}:{fulfillment_id}"[:200],
        )
        db.session.add(shipment)

    purchased_at = purchase.get("purchased_at") or candidate.get("shipped_at") or datetime.utcnow()
    shipment.provider = "ebay_shipping"
    shipment.provider_shipment_id = fulfillment_id
    shipment.carrier = candidate.get("carrier") or shipment.carrier
    shipment.service = candidate.get("service") or shipment.service
    shipment.tracking_number = candidate.get("tracking_number") or shipment.tracking_number
    shipment.purchase_status = "purchased"
    shipment.purchase_error = None
    shipment.label_purchased_at = purchased_at
    shipment.label_source = "ebay_finances_shipping_label"
    shipment.status = "awaiting_carrier_acceptance" if shipment.tracking_number else "label_purchased"
    shipment.last_provider_status = "shipped"
    shipment.last_provider_checked_at = datetime.utcnow()
    shipment.marketplace_confirmation_status = "ebay_shipping_fulfillment_readback"

    # Attach the already-persisted finance evidence to the recovered physical
    # shipment. Refund/adjustment ledger rows remain independent audit facts.
    db.session.flush()
    db.session.execute(
        text(
            """
            UPDATE shipping_spend_ledger
            SET shipment_id = :shipment_id,
                updated_at = :updated_at
            WHERE store_id = :store_id
              AND marketplace_order_id = :order_id
              AND provider = 'ebay'
              AND source = 'ebay_finances_shipping_label'
              AND confirmed = TRUE
              AND (shipment_id IS NULL OR shipment_id = :shipment_id)
            """
        ),
        {
            "shipment_id": shipment.id,
            "updated_at": datetime.utcnow(),
            "store_id": int(store.id),
            "order_id": order_id,
        },
    )
    db.session.commit()

    return {
        "success": True,
        "skipped": False,
        "order_id": order_id,
        "shipment_id": shipment.id,
        "provider": "ebay_shipping",
        "provider_shipment_id": fulfillment_id,
        "carrier": shipment.carrier,
        "service": shipment.service,
        "tracking_number": shipment.tracking_number,
        "purchase_confirmed": True,
        "created": created,
        "marketplace_write_started": False,
    }
