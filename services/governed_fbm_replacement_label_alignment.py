"""Govern replacement-label purchases from the FBM Dispatched workspace.

A dispatched marketplace order may legitimately need another physical label, but
that purchase must never look like an accidental duplicate original shipment.
Replacement label attempts therefore require an explicit reason and remain on the
existing FBMShipment / Packlink purchase path. No marketplace order is recreated,
no stock is moved and no background/provider work is introduced here.
"""
from __future__ import annotations

from datetime import datetime
from functools import wraps

from flask import jsonify, request
from flask_login import current_user
from sqlalchemy import text

from extensions import db
from fbm_models import FBMShipment


REPLACEMENT_REASON_CODES = {
    "label_damaged",
    "parcel_damaged",
    "lost",
    "customer_replacement",
    "wrong_item",
    "other",
}


def _ensure_replacement_reason_schema() -> None:
    """Add only the audit fields required on the existing physical shipment table."""
    db.session.execute(text("ALTER TABLE fbm_shipments ADD COLUMN IF NOT EXISTS replacement_reason_code VARCHAR(50)"))
    db.session.execute(text("ALTER TABLE fbm_shipments ADD COLUMN IF NOT EXISTS replacement_reason TEXT"))
    db.session.execute(text("ALTER TABLE fbm_shipments ADD COLUMN IF NOT EXISTS replacement_reason_recorded_at TIMESTAMP"))
    db.session.execute(text("ALTER TABLE fbm_shipments ADD COLUMN IF NOT EXISTS replacement_reason_recorded_by VARCHAR(150)"))
    db.session.execute(text("CREATE INDEX IF NOT EXISTS ix_fbm_shipments_replacement_reason_code ON fbm_shipments (replacement_reason_code)"))
    db.session.commit()


def _response_parts(response):
    base = response
    status = None
    headers = None
    if isinstance(response, tuple):
        base = response[0]
        if len(response) > 1:
            status = response[1]
        if len(response) > 2:
            headers = response[2]
    return base, status, headers


def _completed_original_exists(order_id: int) -> bool:
    import governed_fbm_routes as routes

    order = routes._get_fbm_order(order_id)
    if order is None:
        return False
    return (
        FBMShipment.query
        .filter_by(store_id=order.store_id, marketplace_order_id=order.marketplace_order_id)
        .filter(FBMShipment.tracking_number.isnot(None))
        .filter(FBMShipment.tracking_number != "")
        .first()
        is not None
    )


def _install_packlink_replacement_reason_guard(app) -> None:
    endpoint = "governed_fbm.packlink_create_draft"
    current = app.view_functions.get(endpoint)
    if current is None or getattr(current, "_bt38_replacement_reason_guarded", False):
        return

    @wraps(current)
    def replacement_reason_guard(*args, **kwargs):
        body = request.get_json(silent=True) or {}
        purpose = str(body.get("shipment_purpose") or "").strip().lower()
        if purpose != "replacement":
            return current(*args, **kwargs)

        order_id = kwargs.get("order_id")
        if order_id is None and args:
            order_id = args[0]
        try:
            order_id = int(order_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Replacement order could not be resolved."}), 400

        if not _completed_original_exists(order_id):
            return jsonify({
                "success": False,
                "message": "Replacement labels are only available after the original order has been dispatched.",
            }), 409

        reason_code = str(body.get("replacement_reason_code") or "").strip().lower()
        reason = " ".join(str(body.get("replacement_reason") or "").strip().split())
        if reason_code not in REPLACEMENT_REASON_CODES:
            return jsonify({
                "success": False,
                "message": "Choose why this replacement label is being purchased.",
                "allowed_reasons": sorted(REPLACEMENT_REASON_CODES),
            }), 400
        if len(reason) < 3:
            return jsonify({
                "success": False,
                "message": "State the reason for this replacement label purchase.",
            }), 400

        response = current(*args, **kwargs)
        base, status, headers = _response_parts(response)
        payload = base.get_json(silent=True) if hasattr(base, "get_json") else None
        shipment_id = payload.get("shipment_id") if isinstance(payload, dict) else None

        # The reason belongs to the replacement shipment attempt itself. Persist
        # it even when Packlink returns verification-required, so a retry can be
        # audited without losing why the extra label was requested.
        if shipment_id is not None:
            shipment = db.session.get(FBMShipment, shipment_id)
            if shipment is not None:
                shipment.replacement_reason_code = reason_code
                shipment.replacement_reason = reason
                shipment.replacement_reason_recorded_at = datetime.utcnow()
                shipment.replacement_reason_recorded_by = str(
                    getattr(current_user, "username", None)
                    or getattr(current_user, "email", None)
                    or getattr(current_user, "id", "user")
                    or "user"
                )[:150]
                db.session.commit()

        if isinstance(payload, dict) and payload.get("success") is True:
            payload = dict(payload)
            payload["shipment_purpose"] = "replacement"
            payload["replacement_reason_code"] = reason_code
            payload["replacement_reason"] = reason
            replacement_response = jsonify(payload)
            if status is None:
                return replacement_response
            if headers is None:
                return replacement_response, status
            return replacement_response, status, headers
        return response

    replacement_reason_guard._bt38_replacement_reason_guarded = True
    app.view_functions[endpoint] = replacement_reason_guard


def _install_replacement_asset(app) -> None:
    """Inject the dispatched-row control without another DB/API request."""
    marker = "fbm_replacement_label_alignment.js"

    @app.after_request
    def inject_fbm_replacement_label_asset(response):
        if request.method != "GET" or request.path.rstrip("/") != "/fbm":
            return response
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type:
            return response
        html = response.get_data(as_text=True)
        if marker in html or "</body>" not in html:
            return response
        tag = f'<script src="/static/js/{marker}"></script>'
        response.set_data(html.replace("</body>", tag + "</body>", 1))
        response.headers["Content-Length"] = str(len(response.get_data()))
        return response


def install_governed_fbm_replacement_label_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_replacement_label_alignment_installed", False):
        return
    with app.app_context():
        _ensure_replacement_reason_schema()
    _install_packlink_replacement_reason_guard(app)
    _install_replacement_asset(app)
    app._bt38_fbm_replacement_label_alignment_installed = True
