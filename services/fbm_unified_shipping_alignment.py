"""Unified live marketplace shipping alignment for the governed FBM desk.

One shipping rule for every connected marketplace:
marketplace owns commercial order facts -> BT38 persists an exact-order cache ->
shipping providers consume that persisted order.  The initial /fbm page remains
DB-only; exact marketplace reads happen only when the operator opens Shipping
Options or explicitly asks a provider for rates.

This module never creates marketplace orders, mutates Warehouse stock, buys
postage, dispatches, or introduces a second provider route.
"""
from __future__ import annotations

from functools import wraps
from typing import Any

import requests

from app import app
from extensions import db
from fbm_models import FBMOrderProfile
from models import MarketplaceOrder
from services.fbm_operational_state import update_marketplace_facts


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_quantity(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _platform(order: Any) -> str:
    store = getattr(order, "store", None)
    return _text(getattr(store, "platform", None)).casefold() if store is not None else ""


def _current_rows(order: MarketplaceOrder) -> list[MarketplaceOrder]:
    return (
        MarketplaceOrder.query
        .filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
        )
        .order_by(MarketplaceOrder.id.asc())
        .all()
    )


def _refresh_ebay(order: MarketplaceOrder, *, source: str) -> dict[str, Any]:
    """Read one exact eBay order and update existing DB rows only."""
    from services.governed_exact_ebay_order_hydration import (
        _ebay_shipping_facts,
        _safe_stale_identity_alias,
    )
    from services.governed_marketplace_order_import import (
        EBAY_ORDERS_URL,
        _ebay_access_token,
        _parse_ebay_datetime,
    )
    from urllib.parse import quote

    store = order.store
    order_id = _text(order.marketplace_order_id)
    rows = _current_rows(order)
    if not rows:
        return {"success": False, "reason": "existing_marketplace_order_missing"}

    response = requests.get(
        f"{EBAY_ORDERS_URL}/{quote(order_id, safe='')}",
        headers={
            "Authorization": f"Bearer {_ebay_access_token(store)}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        return {
            "success": False,
            "reason": "exact_ebay_order_read_failed",
            "status_code": response.status_code,
            "error": response.text[:1000],
        }

    payload = response.json() or {}
    if _text(payload.get("orderId")) not in {"", order_id}:
        return {"success": False, "reason": "exact_ebay_order_identity_mismatch"}

    instructions = payload.get("fulfillmentStartInstructions") or []
    instruction = instructions[0] if instructions else {}
    shipping_step = instruction.get("shippingStep") or {}
    ship_to = shipping_step.get("shipTo") or {}
    address = ship_to.get("contactAddress") or {}
    address_text = ", ".join(
        part for part in (
            _text(address.get("addressLine1")),
            _text(address.get("addressLine2")),
        ) if part
    )
    destination = {
        "ship_to_name": _text(ship_to.get("fullName")),
        "ship_to_address": address_text,
        "ship_to_city": _text(address.get("city")),
        "ship_to_postcode": _text(address.get("postalCode")),
        "ship_to_country": _text(address.get("countryCode")).upper()[:2],
        "ship_to_email": _text(ship_to.get("email")),
        "ship_to_phone": _text((ship_to.get("primaryPhone") or {}).get("phoneNumber"))
        or _text(ship_to.get("phoneNumber")),
    }

    items = [item for item in (payload.get("lineItems") or []) if isinstance(item, dict)]
    by_line_id = {_text(item.get("lineItemId")): item for item in items if _text(item.get("lineItemId"))}
    by_sku: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        sku = _text(item.get("sku"))
        if sku:
            by_sku.setdefault(sku, []).append(item)

    stale_aliases: list[MarketplaceOrder] = []
    matched_payload_lines: set[str] = set()
    conflicts: list[dict[str, Any]] = []

    for row in rows:
        for field, value in destination.items():
            if value:
                setattr(row, field, value)

        current_line_id = _text(row.marketplace_order_item_id)
        item = by_line_id.get(current_line_id)
        if item is None:
            sku_matches = by_sku.get(_text(row.sku), [])
            if len(sku_matches) == 1:
                item = sku_matches[0]
        if item is None:
            continue

        canonical_line_id = _text(item.get("lineItemId"))
        if not canonical_line_id:
            continue
        canonical_key = f"{store.id}:{order_id}:{canonical_line_id}:{_text(row.sku)}"
        conflict = (
            MarketplaceOrder.query
            .filter(
                MarketplaceOrder.idempotency_key == canonical_key,
                MarketplaceOrder.id != row.id,
            )
            .first()
        )
        if conflict is not None:
            if _safe_stale_identity_alias(row, conflict, canonical_line_id=canonical_line_id):
                stale_aliases.append(row)
                continue
            conflicts.append({"row_id": row.id, "conflicting_row_id": conflict.id})
            continue

        row.marketplace_order_item_id = canonical_line_id
        row.idempotency_key = canonical_key
        # eBay is the quantity authority.  This is a shipping/order-fact cache
        # update only; it deliberately does not invoke the stock mutation path.
        row.quantity = _positive_quantity(item.get("quantity"))
        row.line_total = float(getattr(row, "unit_price", 0) or 0) * row.quantity
        matched_payload_lines.add(canonical_line_id)

    for stale in stale_aliases:
        db.session.delete(stale)

    if conflicts:
        db.session.rollback()
        return {"success": False, "reason": "exact_ebay_order_identity_conflict", "conflicts": conflicts}

    shipping = _ebay_shipping_facts(payload)
    surviving = [row for row in rows if row not in stale_aliases]
    canonical = surviving[0] if surviving else None
    if canonical is None:
        db.session.rollback()
        return {"success": False, "reason": "canonical_ebay_order_missing"}

    profile = FBMOrderProfile.query.filter_by(
        store_id=store.id,
        marketplace_order_id=order_id,
    ).first() or FBMOrderProfile(
        store_id=store.id,
        marketplace_order_id=order_id,
        platform="ebay",
    )
    profile.is_prime = False
    profile.is_premium = False
    profile.fulfillment_channel = "FBM"
    if shipping.get("shipping_service"):
        profile.shipment_service_level = shipping["shipping_service"]
    if shipping.get("ship_by_at"):
        profile.latest_ship_at = shipping["ship_by_at"]
    from datetime import datetime
    profile.checked_at = datetime.utcnow()
    profile.last_error = None
    profile.source = source
    db.session.add(profile)

    update_marketplace_facts(
        canonical,
        platform="ebay",
        shipping_service=shipping.get("shipping_service"),
        ship_by_at=shipping.get("ship_by_at"),
        earliest_delivery_at=shipping.get("earliest_delivery_at"),
        latest_delivery_at=shipping.get("latest_delivery_at"),
    )
    created_at = _parse_ebay_datetime(payload.get("creationDate"))
    if created_at is not None:
        for row in surviving:
            row.marketplace_created_at = created_at
    db.session.commit()

    canonical = (
        MarketplaceOrder.query
        .filter_by(store_id=store.id, marketplace_order_id=order_id)
        .order_by(MarketplaceOrder.id.asc())
        .first()
    )
    return {
        "success": canonical is not None,
        "platform": "ebay",
        "order": canonical,
        "quantity": sum(_positive_quantity(row.quantity) for row in _current_rows(canonical)) if canonical else 0,
        "source": source,
    }


def _amazon_order_items(store: Any, order_id: str) -> list[dict[str, Any]]:
    """Fetch Amazon-owned order-line quantities for one exact order."""
    try:
        from sp_api.api import Orders
        from sp_api.base import Marketplaces
        from amazon_service_live_patch import _marketplace_for_id, _sp_api_credentials
    except Exception as exc:
        raise RuntimeError("Amazon Orders API is unavailable") from exc

    creds = getattr(store, "amazon_credentials", None)
    if not creds or not getattr(creds, "is_valid", lambda: False)():
        raise RuntimeError("Amazon credentials are not configured for this store.")
    normalized = {
        "refresh_token": creds.refresh_token,
        "lwa_app_id": creds.lwa_app_id,
        "lwa_client_secret": creds.lwa_client_secret,
        "seller_id": creds.seller_id,
        "marketplace_id": creds.marketplace_id,
        "aws_access_key_id": getattr(creds, "aws_access_key_id", None),
        "aws_secret_access_key": getattr(creds, "aws_secret_access_key", None),
        "role_arn": getattr(creds, "aws_user_arn", None),
    }
    client = Orders(
        credentials=_sp_api_credentials(normalized),
        marketplace=_marketplace_for_id(creds.marketplace_id, Marketplaces),
    )
    response = client.get_order_items(order_id)
    payload = getattr(response, "payload", None)
    if payload is None and hasattr(response, "json"):
        payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("payload"), dict):
        payload = payload["payload"]
    if not isinstance(payload, dict):
        return []
    return [item for item in (payload.get("OrderItems") or payload.get("orderItems") or []) if isinstance(item, dict)]


def _refresh_amazon(order: MarketplaceOrder, *, source: str) -> dict[str, Any]:
    """Refresh exact Amazon profile/address and existing line quantities only."""
    from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile

    profile = get_or_refresh_amazon_profile(order, force=True)
    rows = _current_rows(order)
    items = _amazon_order_items(order.store, _text(order.marketplace_order_id))
    by_id = {_text(item.get("OrderItemId")): item for item in items if _text(item.get("OrderItemId"))}
    by_sku: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        sku = _text(item.get("SellerSKU"))
        if sku:
            by_sku.setdefault(sku, []).append(item)

    conflicts = []
    for row in rows:
        item = by_id.get(_text(row.marketplace_order_item_id))
        if item is None:
            candidates = by_sku.get(_text(row.sku), [])
            if len(candidates) == 1:
                item = candidates[0]
        if item is None:
            continue
        item_id = _text(item.get("OrderItemId"))
        if item_id:
            conflict = next(
                (candidate for candidate in rows if candidate.id != row.id and _text(candidate.marketplace_order_item_id) == item_id),
                None,
            )
            if conflict is not None:
                conflicts.append({"row_id": row.id, "conflicting_row_id": conflict.id})
                continue
            row.marketplace_order_item_id = item_id
            row.idempotency_key = f"{row.store_id}:{row.marketplace_order_id}:{item_id}:{_text(row.sku)}"
        quantity = _positive_quantity(item.get("QuantityOrdered"))
        row.quantity = quantity
        row.line_total = float(getattr(row, "unit_price", 0) or 0) * quantity

    if conflicts:
        db.session.rollback()
        return {"success": False, "reason": "exact_amazon_order_identity_conflict", "conflicts": conflicts}
    profile.source = source
    db.session.add(profile)
    db.session.commit()
    canonical = (
        MarketplaceOrder.query
        .filter_by(store_id=order.store_id, marketplace_order_id=order.marketplace_order_id)
        .order_by(MarketplaceOrder.id.asc())
        .first()
    )
    return {
        "success": canonical is not None,
        "platform": "amazon",
        "order": canonical,
        "quantity": sum(_positive_quantity(row.quantity) for row in _current_rows(canonical)) if canonical else 0,
        "source": source,
    }


def refresh_marketplace_shipping_order(order: MarketplaceOrder, *, source: str) -> dict[str, Any]:
    """Apply the same exact-marketplace-first shipping rule to every platform."""
    if order is None:
        return {"success": False, "reason": "order_missing"}
    platform = _platform(order)
    try:
        if platform == "ebay":
            return _refresh_ebay(order, source=source)
        if platform == "amazon":
            return _refresh_amazon(order, source=source)
        return {
            "success": False,
            "skipped": True,
            "reason": "exact_shipping_reader_not_configured",
            "platform": platform,
        }
    except Exception as exc:
        db.session.rollback()
        return {
            "success": False,
            "reason": "exact_marketplace_shipping_refresh_failed",
            "platform": platform,
            "error": str(exc),
        }


def _resolve_canonical(order: MarketplaceOrder) -> MarketplaceOrder | None:
    return (
        MarketplaceOrder.query
        .filter_by(store_id=order.store_id, marketplace_order_id=order.marketplace_order_id)
        .order_by(MarketplaceOrder.id.asc())
        .first()
    )


def install_unified_packlink_rate_alignment() -> None:
    """Make Packlink consume the same exact refreshed order on every marketplace."""
    from services import fbm_packlink_adapter as packlink

    current = packlink.PacklinkAdapter.get_rates
    if getattr(current, "_bt38_unified_marketplace_alignment", False):
        return

    @wraps(current)
    def aligned_get_rates(self, *, order: Any, parcel: dict):
        platform = _platform(order)
        if platform in {"amazon", "ebay"}:
            entered = {
                key: parcel.get(key)
                for key in ("weight_kg", "length_cm", "width_cm", "height_cm")
                if parcel.get(key) not in (None, "")
            }
            result = refresh_marketplace_shipping_order(
                order,
                source="provider_rate_exact_marketplace_refresh",
            )
            if not result.get("success"):
                detail = _text(result.get("error") or result.get("reason"))
                raise packlink.PacklinkRequestError(
                    f"{platform.title()} live order refresh failed: {detail or 'unknown error'}"
                )
            order = result.get("order") or _resolve_canonical(order)
            if order is None:
                raise packlink.PacklinkRequestError("Live marketplace order disappeared during exact refresh.")
            from services.fbm_order_mapper import provider_parcel
            parcel = provider_parcel(order, entered)
        return current(self, order=order, parcel=parcel)

    aligned_get_rates._bt38_unified_marketplace_alignment = True
    packlink.PacklinkAdapter.get_rates = aligned_get_rates


def install_shipping_options_exact_refresh() -> None:
    """Refresh selected order facts before the modal reads DB quantity/address."""
    endpoint = "governed_fbm.fbm_shipping_options"
    original = app.view_functions.get(endpoint)
    if original is None or getattr(original, "_bt38_unified_shipping_options", False):
        return

    @wraps(original)
    def aligned_view(*args, **kwargs):
        from flask import jsonify, request

        raw_ids = _text(request.args.get("order_ids"))
        order_ids: list[int] = []
        for value in raw_ids.split(","):
            try:
                candidate = int(value.strip())
            except (TypeError, ValueError):
                continue
            if candidate > 0 and candidate not in order_ids:
                order_ids.append(candidate)
            if len(order_ids) >= 50:
                break

        rows = MarketplaceOrder.query.filter(MarketplaceOrder.id.in_(order_ids)).all() if order_ids else []
        refreshed_orders: set[tuple[int, str]] = set()
        for row in rows:
            platform = _platform(row)
            if platform not in {"amazon", "ebay"}:
                continue
            key = (row.store_id, _text(row.marketplace_order_id))
            if key in refreshed_orders:
                continue
            refreshed_orders.add(key)
            result = refresh_marketplace_shipping_order(
                row,
                source="shipping_options_exact_marketplace_refresh",
            )
            if not result.get("success"):
                return jsonify({
                    "success": False,
                    "message": f"{platform.title()} live order refresh failed before shipping: {_text(result.get('error') or result.get('reason'))}",
                }), 502
        return original(*args, **kwargs)

    aligned_view._bt38_unified_shipping_options = True
    app.view_functions[endpoint] = aligned_view


install_unified_packlink_rate_alignment()
install_shipping_options_exact_refresh()
