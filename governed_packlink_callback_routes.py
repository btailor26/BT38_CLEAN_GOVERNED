"""Governed public callback intake for Packlink PRO shipment events.

Registration is explicit and one-time; nothing polls Packlink in the background.
The public callback is authenticated with an opaque BT38 secret embedded in the
registered HTTPS callback URL because Packlink's callback registration contract
stores a URL rather than a BT38 login session.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from flask import Blueprint, jsonify, request, url_for
from flask_login import login_required

from fbm_models import FBMShipment
from services.fbm_packlink_adapter import (
    PacklinkAdapter,
    PacklinkConfigurationError,
    PacklinkRequestError,
)
from services.fbm_packlink_callback import PacklinkCallbackError
from services.fbm_packlink_event_processor import process_packlink_event


governed_packlink_callback_bp = Blueprint(
    "governed_packlink_callback",
    __name__,
)


def _callback_secret() -> str:
    return str(os.environ.get("PACKLINK_CALLBACK_SECRET") or "").strip()


def _authenticated_callback() -> bool:
    expected = _callback_secret()
    supplied = str(request.args.get("token") or "").strip()
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _registered_callback_url() -> str:
    secret = _callback_secret()
    if not secret:
        raise PacklinkConfigurationError("PACKLINK_CALLBACK_SECRET is not configured.")

    configured = str(os.environ.get("PACKLINK_CALLBACK_URL") or "").strip()
    base_url = configured or url_for(
        "governed_packlink_callback.packlink_callback",
        _external=True,
        _scheme="https",
    )
    parts = urlsplit(base_url)
    if parts.scheme.lower() != "https":
        raise PacklinkConfigurationError("Packlink callback URL must use HTTPS.")

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["token"] = secret
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _register_callback(adapter: PacklinkAdapter) -> str:
    callback_url = _registered_callback_url()
    registered = adapter.register_callback(callback_url)
    if not registered:
        raise PacklinkRequestError("Packlink did not confirm callback registration.")
    return callback_url


@governed_packlink_callback_bp.post("/governed/fbm/packlink/callback/register")
@login_required
def register_packlink_callback():
    """Register BT38's Packlink callback once; no scheduler or polling is enabled."""
    try:
        _register_callback(PacklinkAdapter())
    except PacklinkConfigurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except PacklinkRequestError as exc:
        return jsonify({"success": False, "message": str(exc)}), exc.status_code or 502

    return jsonify({
        "success": True,
        "registered": True,
        "callback_path": "/governed/fbm/packlink/callback",
        "message": "Packlink callback registered. Runtime remains asleep until Packlink sends an event.",
    })


@governed_packlink_callback_bp.post("/governed/fbm/packlink/recover-today")
@login_required
def recover_packlink_today():
    """One-time manual recovery for one exact shipment only.

    This route is not part of normal runtime. It never scans all Packlink rows and
    never registers a callback. It exists only to recover a known missed event.
    """
    body = request.get_json(silent=True) or {}
    if body.get("confirm_recovery") != "RECOVER_TODAY_PACKLINK":
        return jsonify({
            "success": False,
            "message": "Explicit RECOVER_TODAY_PACKLINK confirmation is required.",
        }), 400

    try:
        shipment_id = int(body.get("shipment_id"))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "shipment_id is required. Recover one exact Packlink shipment at a time.",
        }), 400

    shipment = FBMShipment.query.filter_by(id=shipment_id, provider="packlink").first()
    if shipment is None or not shipment.provider_shipment_id:
        return jsonify({"success": False, "message": "Packlink shipment not found."}), 404
    if shipment.created_at.date() != datetime.utcnow().date():
        return jsonify({
            "success": False,
            "message": "This recovery route is limited to Packlink shipments created today.",
        }), 409
    if shipment.marketplace_confirmed_at is not None:
        return jsonify({
            "success": True,
            "already_confirmed": True,
            "shipment_id": shipment.id,
            "marketplace_order_id": shipment.marketplace_order_id,
            "provider_reference": shipment.provider_shipment_id,
            "message": "Marketplace shipment was already confirmed. No duplicate confirmation was sent.",
        })

    try:
        result = process_packlink_event(
            {
                "event": "shipment.label.ready",
                "data": {
                    "shipment_reference": shipment.provider_shipment_id,
                    "shipment_custom_reference": shipment.marketplace_order_id,
                },
            },
            adapter=PacklinkAdapter(),
        )
    except PacklinkConfigurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except PacklinkRequestError as exc:
        return jsonify({
            "success": False,
            "shipment_id": shipment.id,
            "marketplace_order_id": shipment.marketplace_order_id,
            "provider_reference": shipment.provider_shipment_id,
            "message": str(exc),
        }), exc.status_code or 502

    return jsonify({
        "success": True,
        "shipment_id": shipment.id,
        "marketplace_order_id": shipment.marketplace_order_id,
        "provider_reference": shipment.provider_shipment_id,
        "recovery": result,
        "message": "Exact Packlink shipment checked once through the event-driven confirmation path.",
    })


@governed_packlink_callback_bp.post("/governed/fbm/packlink/callback")
def packlink_callback():
    """Wake BT38 for one authenticated Packlink event and one shipment only."""
    if not _callback_secret():
        return jsonify({"success": False, "message": "Packlink callback intake is not configured."}), 503
    if not _authenticated_callback():
        return jsonify({"success": False, "message": "Unauthorized Packlink callback."}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"success": False, "message": "Packlink callback JSON is required."}), 400

    try:
        result = process_packlink_event(payload)
    except PacklinkCallbackError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except PacklinkConfigurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except PacklinkRequestError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500

    return jsonify(result), 200
