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

from services.fbm_packlink_adapter import (
    PacklinkAdapter,
    PacklinkConfigurationError,
    PacklinkRequestError,
)
from services.fbm_packlink_callback import (
    PacklinkCallbackError,
    process_packlink_callback,
    recover_packlink_shipments_for_day,
)


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
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment,
    ))


def _register_callback(adapter: PacklinkAdapter) -> str:
    callback_url = _registered_callback_url()
    registered = adapter.register_callback(callback_url)
    if not registered:
        raise PacklinkRequestError("Packlink did not confirm callback registration.")
    return callback_url


@governed_packlink_callback_bp.post("/governed/fbm/packlink/callback/register")
@login_required
def register_packlink_callback():
    """Explicitly register BT38's callback URL with Packlink once."""
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
        "message": "Packlink callback registered. No background polling was enabled.",
    })


@governed_packlink_callback_bp.post("/governed/fbm/packlink/recover-today")
@login_required
def recover_packlink_today():
    """Register the live callback and recover today's exact known Packlink shipments."""
    body = request.get_json(silent=True) or {}
    if body.get("confirm_recovery") != "RECOVER_TODAY_PACKLINK":
        return jsonify({
            "success": False,
            "message": "Explicit RECOVER_TODAY_PACKLINK confirmation is required.",
        }), 400

    adapter = PacklinkAdapter()
    try:
        _register_callback(adapter)
        recovery = recover_packlink_shipments_for_day(
            datetime.utcnow().date(),
            adapter=adapter,
        )
    except PacklinkConfigurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except PacklinkRequestError as exc:
        return jsonify({"success": False, "message": str(exc)}), exc.status_code or 502

    return jsonify({
        "success": True,
        "callback_registered": True,
        "recovery": recovery,
        "message": "Packlink callback is registered and today's known shipments were checked once.",
    })


@governed_packlink_callback_bp.post("/governed/fbm/packlink/callback")
def packlink_callback():
    """Receive one authenticated Packlink event and process that shipment only."""
    if not _callback_secret():
        return jsonify({
            "success": False,
            "message": "Packlink callback intake is not configured.",
        }), 503
    if not _authenticated_callback():
        return jsonify({"success": False, "message": "Unauthorized Packlink callback."}), 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"success": False, "message": "Packlink callback JSON is required."}), 400

    try:
        result = process_packlink_callback(payload)
    except PacklinkCallbackError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except PacklinkConfigurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except PacklinkRequestError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 500

    return jsonify(result), 200
