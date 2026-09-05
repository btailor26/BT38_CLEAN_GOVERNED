"""Keep one FBM authority for a newly accepted marketplace sale.

This is not historical recovery and it does not call Amazon. The current
ORDER_CHANGE notification supplies Prime/Premium shipping facts. The canonical
MarketplaceOrder created by the same governed intake supplies the exact FBM row
identity and product context. One existing UI event is then emitted for the
newly-created sale only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import request


def _find_order_summary(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        order_id = value.get("AmazonOrderId") or value.get("amazonOrderId")
        if order_id not in (None, "") and any(
            key in value
            for key in (
                "OrderPrograms",
                "orderPrograms",
                "FulfillmentType",
                "fulfillmentType",
                "OrderStatus",
                "orderStatus",
            )
        ):
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


def _program_flags(summary: dict[str, Any]) -> tuple[bool | None, bool | None]:
    raw = summary.get("OrderPrograms")
    if raw is None:
        raw = summary.get("orderPrograms")
    if raw is None:
        return None, None
    if isinstance(raw, str):
        programs = {raw.strip().lower()}
    elif isinstance(raw, (list, tuple, set)):
        programs = {str(item or "").strip().lower() for item in raw}
    else:
        programs = set()
    return "prime" in programs, "premium" in programs


def _response_created_order(value: Any, order_id: str) -> bool:
    if isinstance(value, dict):
        candidate = str(
            value.get("marketplace_order_id")
            or value.get("order_id")
            or ""
        ).strip()
        if value.get("created") is True and candidate == order_id:
            return True
        return any(_response_created_order(nested, order_id) for nested in value.values())
    if isinstance(value, list):
        return any(_response_created_order(nested, order_id) for nested in value)
    return False


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

    response_payload = response.get_json(silent=True) if hasattr(response, "get_json") else None
    created_sale = _response_created_order(response_payload, order_id)
    is_prime, is_premium = _program_flags(summary)

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
        if is_prime is not None and profile.is_prime is not is_prime:
            profile.is_prime = is_prime
            changed = True
        if is_premium is not None and profile.is_premium is not is_premium:
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

    # One user-facing sale notification, emitted only when this governed intake
    # actually created the canonical order. Retries and later lifecycle signals
    # therefore cannot manufacture another "Get ready to dispatch" event.
    if created_sale:
        row = rows[0]
        stock = getattr(row, "warehouse_stock", None)
        product_title = str(getattr(stock, "product_name", None) or "").strip()
        from services.governed_ui_event_signal import publish_governed_ui_event

        publish_governed_ui_event(
            source="fbm_page",
            scope={
                "event_type": "fbm_sale_ready",
                "notification_label": "Get ready to dispatch",
                "notification_source": "fbm_page",
                "platform": "Amazon",
                "order_id": order_id,
                "marketplace_order_id": order_id,
                "seller_sku": getattr(row, "sku", None),
                "product_title": product_title or None,
                "quantity": getattr(row, "quantity", None),
                "fulfillment_type": getattr(row, "fulfillment_type", None),
                "is_prime": bool(is_prime),
            },
        )
    return response


def install_governed_fbm_sale_authority_alignment(app) -> None:
    """Carry the current accepted sale into the existing FBM profile/event path."""
    if getattr(app, "_bt38_fbm_sale_authority_alignment_installed", False):
        return
    app.after_request(_persist_current_amazon_fbm_profile)
    app._bt38_fbm_sale_authority_alignment_installed = True
    app.logger.info(
        "BT38 FBM sale authority aligned: current Amazon sale -> existing FBM profile/event; no historical replay/API read"
    )
