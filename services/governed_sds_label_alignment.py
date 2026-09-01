"""Read-only printable label and QR authority for persisted SDS shipments.

The label is built only from the canonical persisted FBMShipment and already
persisted MarketplaceOrder delivery data. Fetching/printing a label never
advances the journey and never writes a marketplace.
"""
from __future__ import annotations

import cv2
import numpy as np
from flask import Response, jsonify, make_response, render_template
from flask_login import login_required

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder


def _text(value):
    value = str(value or "").strip()
    return value or None


def _sds_shipment(shipment_id: int) -> FBMShipment | None:
    shipment = db.session.get(FBMShipment, shipment_id)
    if shipment is None or str(shipment.provider or "").strip().lower() != "sds":
        return None
    reference = _text(shipment.provider_shipment_id)
    if not reference or reference.upper() != f"SDS-{shipment.id:010d}":
        return None
    return shipment


def _persisted_order(shipment: FBMShipment) -> MarketplaceOrder | None:
    return (
        MarketplaceOrder.query
        .filter_by(
            store_id=shipment.store_id,
            marketplace_order_id=shipment.marketplace_order_id,
        )
        .order_by(MarketplaceOrder.id.asc())
        .first()
    )


def _label_payload(shipment: FBMShipment, order: MarketplaceOrder) -> dict:
    lines = (
        MarketplaceOrder.query
        .filter_by(
            store_id=shipment.store_id,
            marketplace_order_id=shipment.marketplace_order_id,
        )
        .order_by(MarketplaceOrder.id.asc())
        .all()
    )
    items = []
    for line in lines:
        items.append({
            "sku": _text(getattr(line, "sku", None)),
            "title": _text(getattr(line, "title", None)),
            "quantity": max(1, int(getattr(line, "quantity", 1) or 1)),
        })
    return {
        "shipment_id": shipment.id,
        "sds_reference": shipment.provider_shipment_id,
        "service": "SDS",
        "status": shipment.status,
        "recipient": {
            "name": _text(getattr(order, "ship_to_name", None)),
            "address1": _text(getattr(order, "ship_to_address", None)),
            "address2": _text(getattr(order, "ship_to_address2", None)),
            "city": _text(getattr(order, "ship_to_city", None)),
            "region": _text(getattr(order, "ship_to_region", None)),
            "postcode": _text(getattr(order, "ship_to_postcode", None)),
            "country": (_text(getattr(order, "ship_to_country", None)) or "GB").upper(),
        },
        "items": items,
        "scan_value": shipment.provider_shipment_id,
        "marketplace_order_id": shipment.marketplace_order_id,
        "read_only": True,
    }


def _qr_png(reference: str) -> bytes:
    encoder = cv2.QRCodeEncoder_create()
    matrix = encoder.encode(reference)
    if matrix is None or getattr(matrix, "size", 0) == 0:
        raise ValueError("QR encoder returned no image")
    matrix = np.asarray(matrix, dtype=np.uint8)
    if matrix.max(initial=0) <= 1:
        matrix = matrix * 255
    matrix = np.pad(matrix, 4, mode="constant", constant_values=255)
    matrix = cv2.resize(matrix, (512, 512), interpolation=cv2.INTER_NEAREST)
    ok, encoded = cv2.imencode(".png", matrix)
    if not ok:
        raise ValueError("QR image encoding failed")
    return encoded.tobytes()


def install_governed_sds_label_alignment(app) -> None:
    if getattr(app, "_bt38_sds_label_alignment_installed", False):
        return

    @app.get("/fbm/shipments/<int:shipment_id>/sds/label/data", endpoint="bt38_sds_label_data")
    @login_required
    def sds_label_data(shipment_id: int):
        shipment = _sds_shipment(shipment_id)
        if shipment is None:
            return jsonify({"success": False, "message": "SDS shipment not found or parcel identity is invalid."}), 404
        order = _persisted_order(shipment)
        if order is None:
            return jsonify({"success": False, "message": "Persisted marketplace order for this SDS shipment is missing."}), 404
        return jsonify({"success": True, "label": _label_payload(shipment, order)})

    @app.get("/fbm/shipments/<int:shipment_id>/sds/label/qr.png", endpoint="bt38_sds_label_qr")
    @login_required
    def sds_label_qr(shipment_id: int):
        shipment = _sds_shipment(shipment_id)
        if shipment is None:
            return jsonify({"success": False, "message": "SDS shipment not found or parcel identity is invalid."}), 404
        try:
            image = _qr_png(str(shipment.provider_shipment_id))
        except (AttributeError, ValueError, cv2.error):
            return jsonify({"success": False, "message": "SDS QR generation is unavailable."}), 503
        response = Response(image, mimetype="image/png")
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/fbm/shipments/<int:shipment_id>/sds/label", endpoint="bt38_sds_label")
    @login_required
    def sds_label(shipment_id: int):
        shipment = _sds_shipment(shipment_id)
        if shipment is None:
            return jsonify({"success": False, "message": "SDS shipment not found or parcel identity is invalid."}), 404
        order = _persisted_order(shipment)
        if order is None:
            return jsonify({"success": False, "message": "Persisted marketplace order for this SDS shipment is missing."}), 404
        payload = _label_payload(shipment, order)
        response = make_response(render_template("fbm_sds_label.html", shipment=shipment, label=payload))
        response.headers["Cache-Control"] = "private, no-store"
        return response

    app._bt38_sds_label_alignment_installed = True
