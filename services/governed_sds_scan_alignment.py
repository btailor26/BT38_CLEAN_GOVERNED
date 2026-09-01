"""Persist real SDS seller scans into the existing FBM shipment lifecycle.

Only explicit scan events advance SDS. No marketplace write is performed here.
"""
from __future__ import annotations

from datetime import datetime

from flask import jsonify, request
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from fbm_models import FBMShipment
from sds_models import SDSScanEvent


EVENTS = {
    "handover": {
        "allowed_from": {"awaiting_seller_handover"},
        "status": "seller_handover_confirmed",
        "timestamp_field": "carrier_accepted_at",
    },
    "in_transit": {
        "allowed_from": {"seller_handover_confirmed", "in_transit"},
        "status": "in_transit",
        "timestamp_field": "first_movement_at",
    },
    "delivered": {
        "allowed_from": {"seller_handover_confirmed", "in_transit"},
        "status": "delivered",
        "timestamp_field": "delivered_at",
    },
}


def _actor() -> str | None:
    for field in ("email", "username", "id"):
        value = getattr(current_user, field, None)
        if value not in (None, ""):
            return str(value)[:150]
    return None


def install_governed_sds_scan_alignment(app) -> None:
    if getattr(app, "_bt38_sds_scan_alignment_installed", False):
        return
    with app.app_context():
        SDSScanEvent.__table__.create(bind=db.engine, checkfirst=True)

    @app.post("/fbm/shipments/<int:shipment_id>/sds/scan", endpoint="bt38_sds_scan")
    @login_required
    def sds_scan(shipment_id: int):
        shipment = db.session.get(FBMShipment, shipment_id)
        if shipment is None or str(shipment.provider or "").lower() != "sds":
            return jsonify({"success": False, "message": "SDS shipment not found."}), 404

        body = request.get_json(silent=True) or {}
        event_type = str(body.get("event_type") or "").strip().lower()
        rule = EVENTS.get(event_type)
        if rule is None:
            return jsonify({"success": False, "message": "Unsupported SDS scan event."}), 400
        if body.get("confirm_scan") != f"SCAN_{event_type.upper()}":
            return jsonify({"success": False, "message": "Explicit SDS scan confirmation is required."}), 400

        current_status = str(shipment.status or "").strip().lower()
        if current_status == "delivered":
            return jsonify({"success": False, "message": "Delivered SDS shipments cannot be advanced again."}), 409
        if current_status not in rule["allowed_from"]:
            return jsonify({
                "success": False,
                "message": "SDS scan is out of sequence.",
                "current_status": shipment.status,
                "requested_event": event_type,
            }), 409

        event_key = f"sds:{shipment.id}:{event_type}"
        existing = SDSScanEvent.query.filter_by(event_key=event_key).first()
        if existing is not None:
            return jsonify({
                "success": True,
                "already_recorded": True,
                "shipment_id": shipment.id,
                "event_type": existing.event_type,
                "occurred_at": existing.occurred_at.isoformat(),
                "status": shipment.status,
            })

        now = datetime.utcnow()
        event = SDSScanEvent(
            shipment_id=shipment.id,
            event_type=event_type,
            event_key=event_key,
            occurred_at=now,
            source="seller_scan",
            created_by=_actor(),
        )
        db.session.add(event)
        setattr(shipment, rule["timestamp_field"], now)
        shipment.status = rule["status"]
        shipment.last_provider_status = f"sds_{event_type}"
        shipment.last_provider_checked_at = now
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            existing = SDSScanEvent.query.filter_by(event_key=event_key).first()
            if existing is not None:
                refreshed = db.session.get(FBMShipment, shipment.id)
                return jsonify({
                    "success": True,
                    "already_recorded": True,
                    "shipment_id": shipment.id,
                    "event_type": existing.event_type,
                    "occurred_at": existing.occurred_at.isoformat(),
                    "status": refreshed.status if refreshed else None,
                })
            return jsonify({"success": False, "message": "SDS scan state changed; reload before retrying."}), 409

        return jsonify({
            "success": True,
            "already_recorded": False,
            "shipment_id": shipment.id,
            "event_type": event_type,
            "occurred_at": now.isoformat(),
            "status": shipment.status,
            "marketplace_written": False,
        })

    app._bt38_sds_scan_alignment_installed = True
