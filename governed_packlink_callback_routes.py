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

from extensions import db
from fbm_models import FBMOrderProfile, FBMRateQuote, FBMShipment
from models import MarketplaceOrder
from services.fbm_packlink_adapter import PacklinkAdapter, PacklinkConfigurationError, PacklinkRequestError
from services.fbm_packlink_callback import PacklinkCallbackError
from services.fbm_packlink_event_processor import process_packlink_event


governed_packlink_callback_bp = Blueprint("governed_packlink_callback", __name__)


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
    base_url = configured or url_for("governed_packlink_callback.packlink_callback", _external=True, _scheme="https")
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


def _selector_diagnostic(value):
    """Return only non-PII Packlink selector/location facts."""
    if not isinstance(value, dict):
        return value if isinstance(value, (str, int, float, bool)) or value is None else str(type(value).__name__)
    allowed = {
        "id", "name", "label", "code", "value", "isoCode", "iso_code",
        "countryCode", "country_code", "zip_code", "zipCode", "zipcode",
        "postcode", "city", "locality", "town", "municipality",
        "postal_zone_id", "postalZoneId", "postal_zone_name", "postalZoneName",
        "postal_zone_id_to", "postal_zone_name_to", "zip_code_id_to",
        "postal_zone_id_from", "zip_code_id_from",
    }
    result = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if isinstance(item, dict):
            result[key] = _selector_diagnostic(item)
        elif isinstance(item, (str, int, float, bool)) or item is None:
            result[key] = item
    return result


def _shipment_reference(row: dict) -> str:
    if not isinstance(row, dict):
        return ""
    return str(
        row.get("shipment_reference")
        or row.get("packlink_reference")
        or row.get("reference")
        or row.get("id")
        or ""
    ).strip()


def _shipment_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("shipments", "items", "results", "data"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, dict)]
    return []


@governed_packlink_callback_bp.post("/governed/fbm/packlink/callback/register")
@login_required
def register_packlink_callback():
    try:
        _register_callback(PacklinkAdapter())
    except PacklinkConfigurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except PacklinkRequestError as exc:
        return jsonify({"success": False, "message": str(exc)}), exc.status_code or 502
    return jsonify({"success": True, "registered": True, "callback_path": "/governed/fbm/packlink/callback", "message": "Packlink callback registered. Runtime remains asleep until Packlink sends an event."})


@governed_packlink_callback_bp.get("/governed/fbm/packlink/diagnostic/<int:shipment_id>")
@login_required
def packlink_shipment_diagnostic(shipment_id: int):
    """Read one exact Packlink shipment across pre-payment inbox states without PII.

    The detail endpoint can return 404 for a pre-payment UN... reference. Check a
    small fixed set of Packlink shipment inbox states and stop on the exact
    reference. This is an explicit user-triggered diagnostic: no polling or writes.
    """
    shipment = FBMShipment.query.filter_by(id=shipment_id, provider="packlink").first()
    if shipment is None or not shipment.provider_shipment_id:
        return jsonify({"success": False, "message": "Packlink shipment not found."}), 404

    adapter = PacklinkAdapter()
    reference = shipment.provider_shipment_id
    remote = None
    read_source = "shipment_detail"
    detail_status = None
    detail_error = None
    inbox_counts = {}
    inbox_errors = {}

    try:
        remote = adapter.get_shipment(reference)
    except PacklinkConfigurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except PacklinkRequestError as exc:
        detail_status = exc.status_code
        detail_error = str(exc)

        for inbox in ("READY_TO_PURCHASE", "PENDING", "DRAFT", "ALL"):
            try:
                inbox_payload = adapter._get_json("shipments", query={"inbox": inbox})
            except PacklinkConfigurationError as inbox_exc:
                return jsonify({"success": False, "message": str(inbox_exc)}), 503
            except PacklinkRequestError as inbox_exc:
                inbox_errors[inbox] = {
                    "status_code": inbox_exc.status_code,
                    "message": str(inbox_exc),
                }
                continue

            rows = _shipment_rows(inbox_payload)
            inbox_counts[inbox] = len(rows)
            exact = next((row for row in rows if _shipment_reference(row) == reference), None)
            if exact is not None:
                remote = exact
                read_source = f"{inbox.lower()}_inbox"
                break

        if remote is None:
            return jsonify({
                "success": False,
                "shipment_id": shipment.id,
                "provider_reference": reference,
                "detail_status_code": detail_status,
                "detail_message": detail_error,
                "inbox_counts": inbox_counts,
                "inbox_errors": inbox_errors,
                "message": "Packlink rejected the detail read and the exact reference was not present in the checked shipment inbox states.",
            }), detail_status or 502

    if not isinstance(remote, dict):
        return jsonify({
            "success": False,
            "shipment_id": shipment.id,
            "provider_reference": reference,
            "message": "Packlink returned no readable shipment object.",
        }), 502

    remote_to = remote.get("to") if isinstance(remote.get("to"), dict) else {}
    additional = remote.get("additional_data") if isinstance(remote.get("additional_data"), dict) else {}
    nested_additional = additional.get("additional_data") if isinstance(additional.get("additional_data"), dict) else {}

    return jsonify({
        "success": True,
        "shipment_id": shipment.id,
        "provider_reference": reference,
        "read_source": read_source,
        "detail_status_code": detail_status,
        "detail_message": detail_error,
        "inbox_counts": inbox_counts,
        "inbox_errors": inbox_errors,
        "remote_top_level_keys": sorted(str(key) for key in remote.keys()),
        "remote_to": _selector_diagnostic(remote_to),
        "remote_additional_data": _selector_diagnostic(additional),
        "remote_nested_additional_data": _selector_diagnostic(nested_additional),
        "country_present": bool(remote_to.get("country")),
        "country_value": _selector_diagnostic(remote_to.get("country")),
        "postcode_value": _selector_diagnostic(
            remote_to.get("zip_code") or remote_to.get("zipCode") or remote_to.get("zipcode") or remote_to.get("postcode")
        ),
        "city_value": _selector_diagnostic(remote_to.get("city")),
        "postal_zone_id_to": additional.get("postal_zone_id_to"),
        "postal_zone_name_to": additional.get("postal_zone_name_to"),
        "zip_code_id_to": additional.get("zip_code_id_to"),
        "message": "Read one exact Packlink shipment. No shipment or marketplace data was changed.",
    })


@governed_packlink_callback_bp.post("/governed/fbm/orders/delete")
@login_required
def delete_fbm_orders():
    """Delete selected FBM rows from BT38 only.

    This is a local BT38 data action. It does not cancel an order on Amazon/eBay
    and it does not cancel or refund any shipment/label at a provider. Associated
    FBM state is removed only when the last BT38 row for that marketplace order
    has been deleted.
    """
    body = request.get_json(silent=True) or {}
    if body.get("confirm_delete") != "DELETE_SELECTED_FBM_RECORDS":
        return jsonify({"success": False, "message": "Explicit DELETE_SELECTED_FBM_RECORDS confirmation is required."}), 400

    raw_ids = body.get("order_ids")
    if not isinstance(raw_ids, list):
        return jsonify({"success": False, "message": "order_ids must be a list."}), 400

    order_ids: list[int] = []
    for value in raw_ids[:100]:
        try:
            order_id = int(value)
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in order_ids:
            order_ids.append(order_id)
    if not order_ids:
        return jsonify({"success": False, "message": "Select at least one FBM record."}), 400

    selected = MarketplaceOrder.query.filter(MarketplaceOrder.id.in_(order_ids)).all()
    by_id = {row.id: row for row in selected}
    missing = [order_id for order_id in order_ids if order_id not in by_id]
    if missing:
        return jsonify({"success": False, "message": "One or more selected records no longer exist.", "missing_order_ids": missing}), 409

    identities = sorted({(row.store_id, str(row.marketplace_order_id)) for row in selected})
    deleted_marketplace_rows = 0
    deleted_shipments = 0
    deleted_quotes = 0
    deleted_profiles = 0

    try:
        for row in selected:
            db.session.delete(row)
            deleted_marketplace_rows += 1
        db.session.flush()

        for store_id, marketplace_order_id in identities:
            remaining = MarketplaceOrder.query.filter_by(
                store_id=store_id,
                marketplace_order_id=marketplace_order_id,
            ).count()
            if remaining:
                continue

            shipments = FBMShipment.query.filter_by(
                store_id=store_id,
                marketplace_order_id=marketplace_order_id,
            ).all()
            for shipment in shipments:
                db.session.delete(shipment)
                deleted_shipments += 1

            deleted_quotes += FBMRateQuote.query.filter_by(
                store_id=store_id,
                marketplace_order_id=marketplace_order_id,
            ).delete(synchronize_session=False)
            deleted_profiles += FBMOrderProfile.query.filter_by(
                store_id=store_id,
                marketplace_order_id=marketplace_order_id,
            ).delete(synchronize_session=False)

        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": "FBM delete was rolled back.", "detail": str(exc)}), 409

    return jsonify({
        "success": True,
        "deleted_order_ids": order_ids,
        "deleted_marketplace_rows": deleted_marketplace_rows,
        "deleted_shipments": deleted_shipments,
        "deleted_quotes": deleted_quotes,
        "deleted_profiles": deleted_profiles,
        "message": f"Deleted {deleted_marketplace_rows} selected FBM record{'s' if deleted_marketplace_rows != 1 else ''} from BT38.",
    })


@governed_packlink_callback_bp.post("/governed/fbm/packlink/recover-today")
@login_required
def recover_packlink_today():
    body = request.get_json(silent=True) or {}
    if body.get("confirm_recovery") != "RECOVER_TODAY_PACKLINK":
        return jsonify({"success": False, "message": "Explicit RECOVER_TODAY_PACKLINK confirmation is required."}), 400
    try:
        shipment_id = int(body.get("shipment_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "shipment_id is required. Recover one exact Packlink shipment at a time."}), 400
    shipment = FBMShipment.query.filter_by(id=shipment_id, provider="packlink").first()
    if shipment is None or not shipment.provider_shipment_id:
        return jsonify({"success": False, "message": "Packlink shipment not found."}), 404
    if shipment.created_at.date() != datetime.utcnow().date():
        return jsonify({"success": False, "message": "This recovery route is limited to Packlink shipments created today."}), 409
    if shipment.marketplace_confirmed_at is not None:
        return jsonify({"success": True, "already_confirmed": True, "shipment_id": shipment.id, "marketplace_order_id": shipment.marketplace_order_id, "provider_reference": shipment.provider_shipment_id, "message": "Marketplace shipment was already confirmed. No duplicate confirmation was sent."})
    try:
        result = process_packlink_event({"event": "shipment.label.ready", "data": {"shipment_reference": shipment.provider_shipment_id, "shipment_custom_reference": shipment.marketplace_order_id}}, adapter=PacklinkAdapter())
    except PacklinkConfigurationError as exc:
        return jsonify({"success": False, "message": str(exc)}), 503
    except PacklinkRequestError as exc:
        return jsonify({"success": False, "shipment_id": shipment.id, "marketplace_order_id": shipment.marketplace_order_id, "provider_reference": shipment.provider_shipment_id, "message": str(exc)}), exc.status_code or 502
    return jsonify({"success": True, "shipment_id": shipment.id, "marketplace_order_id": shipment.marketplace_order_id, "provider_reference": shipment.provider_shipment_id, "recovery": result, "message": "Exact Packlink shipment checked once through the event-driven confirmation path."})


@governed_packlink_callback_bp.post("/governed/fbm/packlink/callback")
def packlink_callback():
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


# Standalone manual shipping and its explicit postcode lookup remain inside the
# governed FBM registration tree. Neither creates marketplace/stock side effects.
from governed_fbm_manual_routes import governed_fbm_manual_bp
from governed_fbm_address_lookup_routes import governed_fbm_address_lookup_bp

governed_packlink_callback_bp.register_blueprint(governed_fbm_manual_bp)
governed_packlink_callback_bp.register_blueprint(governed_fbm_address_lookup_bp)