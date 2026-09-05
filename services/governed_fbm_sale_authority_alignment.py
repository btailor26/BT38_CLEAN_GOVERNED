"""Persist FBM sale facts from the original governed Amazon webhook.

This is not recovery and it does not call Amazon. The original ORDER_CHANGE
notification is the authority for Prime/Premium classification. MarketplaceOrder
remains the sale/order authority; FBMOrderProfile stores only the shipping facts
needed by the existing FBM page.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import request


def _find_order_summary(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        order_id = value.get("AmazonOrderId") or value.get("amazonOrderId")
        programs = value.get("OrderPrograms") or value.get("orderPrograms")
        if order_id not in (None, "") and programs is not None:
            return value
        for nested in value.values():
            found = _find_order_summary(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_order_summary(nested)
            if found is not None:
                return found
    return None


def _program_flags(summary: dict[str, Any]) -> tuple[bool, bool]:
    raw = summary.get("OrderPrograms")
    if raw is None:
        raw = summary.get("orderPrograms")
    if isinstance(raw, str):
        programs = {raw.strip().lower()}
    elif isinstance(raw, (list, tuple, set)):
        programs = {str(item or "").strip().lower() for item in raw}
    else:
        programs = set()
    return "prime" in programs, "premium" in programs


def _persist_current_amazon_fbm_profile(response):
    if request.method != "POST" or (request.path.rstrip("/") or "/") != "/governed/webhooks/amazon":
        return response
    if int(getattr(response, "status_code", 200) or 200) >= 400:
        return response

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return response

    summary = _find_order_summary(payload)
    if summary is None:
        return response

    order_id = str(summary.get("AmazonOrderId") or summary.get("amazonOrderId") or "").strip()
    if not order_id:
        return response

    is_prime, is_premium = _program_flags(summary)
    if not (is_prime or is_premium):
        return response

    from extensions import db
    from fbm_models import FBMOrderProfile
    from models import MarketplaceOrder, Store

    rows = (
        db.session.query(MarketplaceOrder)
        .join(Store, Store.id == MarketplaceOrder.store_id)
        .filter(
            MarketplaceOrder.marketplace_order_id == order_id,
            Store.platform.ilike("%amazon%"),
        )
        .order_by(MarketplaceOrder.id)
        .all()
    )
    if not rows:
        return response

    now = datetime.utcnow()
    changed = False
    for store_id in sorted({int(row.store_id) for row in rows if row.store_id is not None}):
        profile = FBMOrderProfile.query.filter_by(
            store_id=store_id,
            marketplace_order_id=order_id,
        ).first()
        if profile is None:
            profile = FBMOrderProfile(
                store_id=store_id,
                marketplace_order_id=order_id,
                platform="amazon",
                source="amazon_order_change",
            )
            db.session.add(profile)
            changed = True
        if profile.is_prime is not is_prime:
            profile.is_prime = is_prime
            changed = True
        if profile.is_premium is not is_premium:
            profile.is_premium = is_premium
            changed = True
        fulfillment = str(summary.get("FulfillmentType") or summary.get("fulfillmentType") or "").strip() or None
        service = str(summary.get("ShipServiceLevel") or summary.get("shipServiceLevel") or "").strip() or None
        if fulfillment and profile.fulfillment_channel != fulfillment:
            profile.fulfillment_channel = fulfillment
            changed = True
        if service and profile.shipment_service_level != service:
            profile.shipment_service_level = service
            changed = True
        profile.checked_at = now
        profile.last_error = None

    if changed:
        db.session.commit()
    return response


def install_governed_fbm_sale_authority_alignment(app) -> None:
    """Carry current webhook sale facts into the existing FBM profile only."""
    if getattr(app, "_bt38_fbm_sale_authority_alignment_installed", False):
        return
    app.after_request(_persist_current_amazon_fbm_profile)
    app._bt38_fbm_sale_authority_alignment_installed = True
    app.logger.info(
        "BT38 FBM sale authority aligned: current Amazon ORDER_CHANGE -> existing FBM profile; no recovery/API read"
    )
