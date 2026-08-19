"""Governed standalone manual shipping orders.

Manual shipping orders are postage jobs only. They are deliberately separate
from MarketplaceOrder, Store, warehouse inventory and marketplace dispatch.
Destination and parcel facts are committed to BT38 before any external Packlink
request so a provider error cannot lose what the user entered.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from extensions import db
from fbm_models import FBMManualOrder
from services.fbm_order_mapper import ship_from
from services.fbm_packlink_adapter import (
    PacklinkAdapter,
    PacklinkConfigurationError,
    PacklinkRequestError,
)


governed_fbm_manual_bp = Blueprint("governed_fbm_manual", __name__)


_DESTINATION_FIELDS = (
    "ship_to_name",
    "ship_to_address",
    "ship_to_address2",
    "ship_to_city",
    "ship_to_region",
    "ship_to_postcode",
    "ship_to_country",
    "ship_to_email",
    "ship_to_phone",
)

_PARCEL_FIELDS = ("weight_kg", "length_cm", "width_cm", "height_cm")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _positive_int(value: Any, default: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _payload() -> dict[str, Any]:
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return request.form.to_dict(flat=True)


def _manual_reference(order_id: int) -> str:
    return f"MAN-{datetime.utcnow().strftime('%Y%m%d')}-{order_id:06d}"


def _parcel(order: FBMManualOrder) -> dict[str, Any]:
    origin = ship_from()
    return {
        "weight_kg": order.weight_kg,
        "length_cm": order.length_cm,
        "width_cm": order.width_cm,
        "height_cm": order.height_cm,
        "source": "manual_shipping_order",
        "complete": all(_positive_float(getattr(order, field, None)) for field in _PARCEL_FIELDS),
        "from_country": origin["country"],
        "from_zip": origin["postcode"],
        "to_country": (order.ship_to_country or "GB").upper(),
        "to_zip": order.ship_to_postcode,
    }


def _rate_id(rate: dict[str, Any]) -> str:
    return _text(rate.get("rate_id") or rate.get("id") or rate.get("service_id"))


def _selected_rate(order: FBMManualOrder, rate_id: str) -> dict[str, Any] | None:
    return next(
        (
            rate for rate in (order.rates or [])
            if isinstance(rate, dict) and _rate_id(rate) == rate_id
        ),
        None,
    )


def _extract_checkout_url(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates = (
        payload.get("checkout_url"),
        payload.get("payment_url"),
        payload.get("paymentUrl"),
        payload.get("url"),
    )
    for value in candidates:
        text = _text(value)
        if text.startswith("https://"):
            return text
    for key in ("checkout", "payment", "links"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            found = _extract_checkout_url(nested)
            if found:
                return found
    return None


def _extract_tracking(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("tracking_number", "tracking", "tracking_code", "trackingNumber"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    values = payload.get("trackings") or payload.get("tracking_codes")
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                found = _extract_tracking(value)
                if found:
                    return found
    return None


def _apply_editable_fields(order: FBMManualOrder, data: dict[str, Any]) -> list[str]:
    """Apply user-entered shipping facts. Caller commits before provider access."""
    changed: list[str] = []
    for field in _DESTINATION_FIELDS:
        if field not in data:
            continue
        value = _text(data.get(field)) or None
        if field == "ship_to_country":
            value = (value or "GB").upper()[:2]
        if getattr(order, field, None) != value:
            setattr(order, field, value)
            changed.append(field)

    for field in _PARCEL_FIELDS:
        if field not in data:
            continue
        value = _positive_float(data.get(field))
        if value is not None and getattr(order, field, None) != value:
            setattr(order, field, value)
            changed.append(field)

    if "item_title" in data:
        value = _text(data.get("item_title")) or "Goods"
        if order.item_title != value:
            order.item_title = value
            changed.append("item_title")
    if "sku" in data:
        value = _text(data.get("sku")) or None
        if order.sku != value:
            order.sku = value
            changed.append("sku")
    if "quantity" in data:
        value = _positive_int(data.get("quantity"), 1)
        if order.quantity != value:
            order.quantity = value
            changed.append("quantity")
    if "declared_value" in data:
        raw = data.get("declared_value")
        value = _positive_float(raw) if raw not in (None, "") else None
        if order.declared_value != value:
            order.declared_value = value
            changed.append("declared_value")
    return changed


def _validation_errors(order: FBMManualOrder) -> list[str]:
    missing: list[str] = []
    required = (
        ("ship_to_name", "destination name"),
        ("ship_to_address", "destination address"),
        ("ship_to_city", "destination city"),
        ("ship_to_postcode", "destination postcode"),
        ("ship_to_country", "destination country"),
        ("ship_to_phone", "destination phone"),
    )
    for field, label in required:
        if not _text(getattr(order, field, None)):
            missing.append(label)
    for field in _PARCEL_FIELDS:
        if not _positive_float(getattr(order, field, None)):
            missing.append(field)
    return missing


def _order_payload(order: FBMManualOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "reference": order.reference,
        "status": order.status,
        "ship_to_name": order.ship_to_name,
        "ship_to_address": order.ship_to_address,
        "ship_to_address2": order.ship_to_address2,
        "ship_to_city": order.ship_to_city,
        "ship_to_region": order.ship_to_region,
        "ship_to_postcode": order.ship_to_postcode,
        "ship_to_country": order.ship_to_country,
        "ship_to_email": order.ship_to_email,
        "ship_to_phone": order.ship_to_phone,
        "item_title": order.item_title,
        "sku": order.sku,
        "quantity": order.quantity,
        "declared_value": order.declared_value,
        "parcel": _parcel(order),
        "provider": order.provider,
        "selected_rate_id": order.selected_rate_id,
        "carrier": order.carrier,
        "service": order.service,
        "provider_shipment_id": order.provider_shipment_id,
        "provider_status": order.provider_status,
        "tracking_number": order.tracking_number,
        "checkout_url": order.checkout_url,
        "label_url": order.label_url,
        "rate_expires_at": order.rate_expires_at.isoformat() if order.rate_expires_at else None,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


@governed_fbm_manual_bp.get("/fbm/manual")
@governed_fbm_manual_bp.get("/fbm/manual/new")
@login_required
def manual_shipping_page():
    recent = FBMManualOrder.query.order_by(FBMManualOrder.created_at.desc()).limit(25).all()
    return render_template("fbm_manual_shipping.html", manual_orders=recent)


@governed_fbm_manual_bp.post("/fbm/manual")
@login_required
def create_manual_shipping_order():
    data = _payload()
    order = FBMManualOrder(
        ship_to_name=_text(data.get("ship_to_name")),
        ship_to_address=_text(data.get("ship_to_address")),
        ship_to_address2=_text(data.get("ship_to_address2")) or None,
        ship_to_city=_text(data.get("ship_to_city")),
        ship_to_region=_text(data.get("ship_to_region")) or None,
        ship_to_postcode=_text(data.get("ship_to_postcode")),
        ship_to_country=(_text(data.get("ship_to_country")) or "GB").upper()[:2],
        ship_to_email=_text(data.get("ship_to_email")) or None,
        ship_to_phone=_text(data.get("ship_to_phone")),
        item_title=_text(data.get("item_title")) or "Goods",
        sku=_text(data.get("sku")) or None,
        quantity=_positive_int(data.get("quantity"), 1),
        declared_value=_positive_float(data.get("declared_value")),
        weight_kg=_positive_float(data.get("weight_kg")) or 0,
        length_cm=_positive_float(data.get("length_cm")) or 0,
        width_cm=_positive_float(data.get("width_cm")) or 0,
        height_cm=_positive_float(data.get("height_cm")) or 0,
        provider="packlink",
        status="draft",
        created_by=_text(
            getattr(current_user, "username", None)
            or getattr(current_user, "email", None)
            or getattr(current_user, "id", None)
        ) or None,
    )
    missing = _validation_errors(order)
    if missing:
        return jsonify({
            "success": False,
            "message": "Manual shipping data is incomplete.",
            "missing": missing,
        }), 422

    db.session.add(order)
    db.session.flush()
    order.reference = _manual_reference(order.id)
    db.session.commit()
    return jsonify({
        "success": True,
        "manual_order": _order_payload(order),
        "message": "Manual shipping order saved. No marketplace order or stock movement was created.",
    })


@governed_fbm_manual_bp.get("/fbm/manual/<int:manual_order_id>")
@login_required
def get_manual_shipping_order(manual_order_id: int):
    order = db.session.get(FBMManualOrder, manual_order_id)
    if order is None:
        return jsonify({"success": False, "message": "Manual shipping order not found."}), 404
    return jsonify({"success": True, "manual_order": _order_payload(order), "rates": order.rates or []})


@governed_fbm_manual_bp.post("/fbm/manual/<int:manual_order_id>/packlink/rates")
@login_required
def manual_packlink_rates(manual_order_id: int):
    order = db.session.get(FBMManualOrder, manual_order_id)
    if order is None:
        return jsonify({"success": False, "message": "Manual shipping order not found."}), 404
    if order.provider_shipment_id:
        return jsonify({
            "success": False,
            "message": "This manual order already has a Packlink shipment. A second shipment was blocked.",
        }), 409

    data = _payload()
    changed = _apply_editable_fields(order, data)
    missing = _validation_errors(order)
    if missing:
        if changed:
            db.session.commit()
        return jsonify({
            "success": False,
            "message": "Manual shipping data is incomplete.",
            "missing": missing,
            "saved_fields": changed,
        }), 422

    # Important contract: persist destination/weight/dimensions BEFORE Packlink.
    order.status = "saved"
    order.last_error = None
    db.session.commit()

    parcel = _parcel(order)
    try:
        rates = PacklinkAdapter().get_rates(order=order, parcel=parcel)
    except (PacklinkConfigurationError, PacklinkRequestError) as exc:
        order.last_error = str(exc)
        order.status = "provider_error"
        db.session.commit()
        return jsonify({
            "success": False,
            "message": str(exc),
            "manual_order_id": order.id,
            "saved": True,
            "parcel": parcel,
        }), getattr(exc, "status_code", None) or 502

    order.rates = rates
    order.rate_expires_at = datetime.utcnow() + timedelta(minutes=15)
    order.status = "rates_ready"
    order.last_error = None
    db.session.commit()
    return jsonify({
        "success": True,
        "manual_order_id": order.id,
        "reference": order.reference,
        "rates": rates,
        "rate_expires_at": order.rate_expires_at.isoformat(),
        "parcel": parcel,
        "message": "Packlink rates loaded from the saved manual shipping order.",
    })


@governed_fbm_manual_bp.post("/fbm/manual/<int:manual_order_id>/packlink/draft")
@login_required
def manual_packlink_draft(manual_order_id: int):
    order = db.session.get(FBMManualOrder, manual_order_id)
    if order is None:
        return jsonify({"success": False, "message": "Manual shipping order not found."}), 404
    if order.provider_shipment_id:
        return jsonify({
            "success": True,
            "already_created": True,
            "manual_order": _order_payload(order),
            "message": "The Packlink shipment already exists. No duplicate was created.",
        })

    data = _payload()
    if data.get("confirm_create") != "CREATE_MANUAL_PACKLINK_DRAFT":
        return jsonify({
            "success": False,
            "message": "Explicit CREATE_MANUAL_PACKLINK_DRAFT confirmation is required.",
        }), 400

    rate_id = _text(data.get("rate_id"))
    if not rate_id:
        return jsonify({"success": False, "message": "Choose a Packlink service first."}), 400
    if not order.rates or not order.rate_expires_at or datetime.utcnow() >= order.rate_expires_at:
        return jsonify({"success": False, "message": "Packlink rates expired. Get fresh rates."}), 409
    rate = _selected_rate(order, rate_id)
    if rate is None:
        return jsonify({"success": False, "message": "Selected Packlink service is not in the saved quote."}), 409

    # Save the selected service before the provider write.
    order.selected_rate_id = rate_id
    order.provider_service_id = _text(rate.get("service_id") or rate.get("id")) or None
    order.carrier = _text(rate.get("carrier_name") or rate.get("carrier")) or None
    order.service = _text(rate.get("service_name") or rate.get("service")) or None
    order.status = "draft_creating"
    order.last_error = None
    db.session.commit()

    try:
        draft = PacklinkAdapter().create_shipment_draft(
            order=order,
            parcel=_parcel(order),
            rate=rate,
        )
    except (PacklinkConfigurationError, PacklinkRequestError) as exc:
        order.status = "draft_verification_required"
        order.last_error = str(exc)
        db.session.commit()
        return jsonify({
            "success": False,
            "message": str(exc),
            "manual_order_id": order.id,
            "saved": True,
        }), getattr(exc, "status_code", None) or 502
    except Exception as exc:
        order.status = "draft_verification_required"
        order.last_error = str(exc)
        db.session.commit()
        return jsonify({
            "success": False,
            "message": "Packlink draft could not be confirmed. Check Packlink before trying again.",
            "manual_order_id": order.id,
            "saved": True,
        }), 502

    order.provider_shipment_id = _text(draft.get("reference")) or None
    order.provider_status = _text(draft.get("payment_status")) or "pending_packlink_payment"
    order.checkout_url = _extract_checkout_url(draft.get("raw"))
    order.status = "awaiting_provider_payment"
    order.last_error = None
    db.session.commit()
    return jsonify({
        "success": True,
        "manual_order": _order_payload(order),
        "payment_mode": "packlink_checkout_required",
        "message": "Manual Packlink shipment prepared. Complete payment in Packlink; BT38 did not create a marketplace sale.",
    })


@governed_fbm_manual_bp.post("/fbm/manual/<int:manual_order_id>/packlink/status")
@login_required
def manual_packlink_status(manual_order_id: int):
    order = db.session.get(FBMManualOrder, manual_order_id)
    if order is None:
        return jsonify({"success": False, "message": "Manual shipping order not found."}), 404
    if not order.provider_shipment_id:
        return jsonify({"success": False, "message": "Packlink shipment has not been created yet."}), 409

    try:
        payload = PacklinkAdapter().get_shipment(order.provider_shipment_id)
    except (PacklinkConfigurationError, PacklinkRequestError) as exc:
        order.last_error = str(exc)
        db.session.commit()
        return jsonify({"success": False, "message": str(exc)}), getattr(exc, "status_code", None) or 502

    order.provider_status = _text(payload.get("status") or payload.get("state")) or order.provider_status
    order.carrier = _text(payload.get("carrier")) or order.carrier
    order.service = _text(payload.get("service")) or order.service
    order.tracking_number = _extract_tracking(payload) or order.tracking_number
    order.checkout_url = _extract_checkout_url(payload) or order.checkout_url
    label = payload.get("label") or payload.get("label_url")
    if isinstance(label, str) and label.startswith("http"):
        order.label_url = label
    if order.tracking_number:
        order.status = "tracking_ready"
    elif order.provider_status:
        order.status = "provider_updated"
    order.last_error = None
    db.session.commit()
    return jsonify({"success": True, "manual_order": _order_payload(order), "provider": payload})
