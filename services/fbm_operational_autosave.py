"""Persist packed-parcel values for the existing governed FBM desk.

This endpoint only saves measurements against an existing FBM MarketplaceOrder.
It does not create a second FBM page/status path, buy postage, dispatch, mutate
inventory or call a marketplace/provider.
"""
from __future__ import annotations

from flask import jsonify, request
from flask_login import login_required

from app import app
from extensions import db
from models import MarketplaceOrder
from services.fbm_operational_state import save_order_parcel, saved_order_parcel


_FIELDS = ("weight_kg", "length_cm", "width_cm", "height_cm")


def _is_fbm_eligible(order: MarketplaceOrder) -> bool:
    fulfillment = str(getattr(order, "fulfillment_type", "") or "").upper()
    status = str(getattr(order, "status", "") or "").lower()
    return fulfillment not in {"FBA", "AFN", "MCF"} and not status.startswith("mcf_")


def _positive_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


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
