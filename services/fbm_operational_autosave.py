"""Governed FBM operational-state endpoints and page alignment hook.

This module is intentionally narrow. It exposes marketplace-owned shipping facts
for already-existing FBM orders and persists packed-parcel values entered by the
user. It never buys postage, dispatches an order, mutates inventory, or creates a
marketplace order.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import jsonify, request
from flask_login import login_required

from app import app
from extensions import db
from fbm_models import FBMOrderProfile, FBMShipment
from models import MarketplaceOrder
from services.fbm_operational_state import (
    operational_state,
    promise_state,
    save_order_parcel,
    saved_order_parcel,
)


_FIELDS = ("weight_kg", "length_cm", "width_cm", "height_cm")
_OPERATIONAL_REFRESH_TTL = timedelta(minutes=5)
_ALIGNMENT_SCRIPT = "/static/js/fbm_operational_alignment.js"


def _is_fbm_eligible(order: MarketplaceOrder) -> bool:
    fulfillment = str(getattr(order, "fulfillment_type", "") or "").upper()
    status = str(getattr(order, "status", "") or "").lower()
    return fulfillment not in {"FBA", "AFN", "MCF"} and not status.startswith("mcf_")


def _platform(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "platform", "") or "").strip().lower()


def _positive_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _latest_shipment(order: MarketplaceOrder):
    return (
        FBMShipment.query
        .filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
        )
        .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
        .first()
    )


def _operational_state_stale(state) -> bool:
    if state is None:
        return True
    checked = getattr(state, "marketplace_checked_at", None)
    return not checked or datetime.utcnow() - checked >= _OPERATIONAL_REFRESH_TTL


def _hydrate_missing_marketplace_facts(order: MarketplaceOrder) -> str | None:
    """Refresh only when BT38 is missing/stale marketplace-owned FBM facts."""
    platform = _platform(order)
    state = operational_state(order, create=False)
    promise_known = bool(
        state
        and (
            getattr(state, "earliest_delivery_at", None)
            or getattr(state, "latest_delivery_at", None)
        )
    )
    service_known = bool(state and getattr(state, "shipping_service", None))

    # Do not turn every FBM page refresh into marketplace traffic. Once a
    # complete promise/service snapshot exists, the five-minute freshness gate
    # applies. Missing facts are read immediately from the exact marketplace
    # order because the UI must never invent a delivery promise.
    needs_refresh = not promise_known or not service_known or _operational_state_stale(state)
    if not needs_refresh:
        return None

    try:
        if platform == "amazon":
            from services.fbm_amazon_order_profile import get_or_refresh_amazon_profile

            get_or_refresh_amazon_profile(order, force=not promise_known)
            return None

        if platform == "ebay":
            from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

            result = hydrate_exact_ebay_order(
                store=order.store,
                marketplace_order_id=str(order.marketplace_order_id),
                source="fbm_operational_status",
            )
            if result and result.get("success") is False:
                return str(result.get("reason") or "ebay_shipping_facts_unavailable")
            return None
    except Exception as exc:
        db.session.rollback()
        app.logger.warning(
            "FBM operational marketplace refresh failed for %s %s: %s",
            platform,
            order.marketplace_order_id,
            exc,
        )
        return str(exc)

    return "marketplace_shipping_facts_not_supported"


@app.post("/fbm/orders/<int:order_id>/parcel")
@login_required
def fbm_autosave_parcel(order_id: int):
    order = db.session.get(MarketplaceOrder, order_id)
    if order is None or not _is_fbm_eligible(order):
        return jsonify({"success": False, "message": "FBM order not found."}), 404

    body = request.get_json(silent=True) or {}
    incoming = body.get("parcel") if isinstance(body.get("parcel"), dict) else body
    values = {field: _positive_float(incoming.get(field)) for field in _FIELDS}
    values = {field: value for field, value in values.items() if value is not None}
    if not values:
        return jsonify({"success": False, "message": "Enter at least one valid parcel value."}), 400

    try:
        save_order_parcel(order, values)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("FBM parcel autosave failed for order %s", order_id)
        return jsonify({"success": False, "message": "Parcel values could not be saved."}), 500

    return jsonify({
        "success": True,
        "order_id": order.id,
        "marketplace_order_id": order.marketplace_order_id,
        "parcel": saved_order_parcel(order),
        "saved_fields": sorted(values),
        "message": "Packed parcel saved.",
    })


@app.get("/fbm/orders/operational-status")
@login_required
def fbm_operational_status():
    raw_ids = str(request.args.get("order_ids") or "")
    order_ids: list[int] = []
    for raw in raw_ids.split(","):
        try:
            value = int(raw.strip())
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in order_ids:
            order_ids.append(value)
        if len(order_ids) >= 100:
            break

    if not order_ids:
        return jsonify({"success": False, "message": "No FBM orders supplied."}), 400

    rows = MarketplaceOrder.query.filter(MarketplaceOrder.id.in_(order_ids)).all()
    rows_by_id = {row.id: row for row in rows if _is_fbm_eligible(row)}
    payload = []

    for order_id in order_ids:
        order = rows_by_id.get(order_id)
        if order is None:
            continue

        refresh_error = _hydrate_missing_marketplace_facts(order)
        state = operational_state(order, create=False)
        shipment = _latest_shipment(order)
        promise = promise_state(order, shipment=shipment)
        profile = FBMOrderProfile.query.filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
        ).first()

        payload.append({
            "id": order.id,
            "marketplace_order_id": order.marketplace_order_id,
            "platform": _platform(order),
            "is_prime": bool(profile and profile.is_prime is True),
            "prime_locked": bool(profile and profile.is_prime is True),
            "shipping_service": (
                getattr(state, "shipping_service", None)
                if state is not None
                else getattr(profile, "shipment_service_level", None)
            ),
            "ship_by_at": (
                state.ship_by_at.isoformat()
                if state is not None and getattr(state, "ship_by_at", None)
                else None
            ),
            "promise": {
                "available": bool(promise.get("available")),
                "label": promise.get("label"),
                "latest_delivery_at": (
                    promise.get("latest_delivery_at").isoformat()
                    if getattr(promise.get("latest_delivery_at"), "isoformat", None)
                    else promise.get("latest_delivery_at")
                ),
                "late": bool(promise.get("late")),
                "delivered_late": bool(promise.get("delivered_late")),
                "delivered_on_time": bool(promise.get("delivered_on_time")),
            },
            "journey_state": (
                "delivered" if shipment and shipment.delivered_at
                else "in_transit" if shipment and shipment.first_movement_at
                else "accepted" if shipment and shipment.carrier_accepted_at
                else "not_started"
            ),
            "parcel": saved_order_parcel(order),
            "marketplace_refresh_error": refresh_error,
        })

    return jsonify({"success": True, "orders": payload, "count": len(payload)})


@app.after_request
def install_fbm_operational_alignment_script(response):
    """Load the alignment JS on the existing FBM page without replacing it."""
    if request.path.rstrip("/") != "/fbm":
        return response
    if request.method != "GET":
        return response
    content_type = str(response.headers.get("Content-Type") or "").lower()
    if "text/html" not in content_type:
        return response
    try:
        html = response.get_data(as_text=True)
    except Exception:
        return response
    if _ALIGNMENT_SCRIPT in html or "</body>" not in html:
        return response
    html = html.replace(
        "</body>",
        f'<script src="{_ALIGNMENT_SCRIPT}?v=20260828-1"></script></body>',
        1,
    )
    response.set_data(html)
    return response
