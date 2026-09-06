"""Fail closed at the existing Packlink draft and rate boundaries.

The original Packlink route already uses one deterministic purchase_key for the
original outbound shipment. This alignment prevents that same shipment from
being re-POSTed to Packlink when a previous provider call is still in progress,
created a provider reference, or ended in an uncertain verification state.

Packlink upstream 5xx responses are also normalized as a gateway/provider error
instead of surfacing to the browser as a BT38 HTTP 500. No quote or label is
invented and no retry/provider call is introduced by this alignment.
"""
from __future__ import annotations

from functools import wraps

from flask import jsonify, request

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder


_UNCERTAIN_OR_CREATED_STATES = {
    "draft_creating",
    "draft_verification_required",
    "pending_provider_payment",
    "ready_to_ship",
    "purchased",
}


def _normal(value) -> str:
    return str(value or "").strip().lower()


def _original_packlink_shipment(order: MarketplaceOrder) -> FBMShipment | None:
    purchase_key = f"packlink_draft:{order.store_id}:{order.marketplace_order_id}"
    return FBMShipment.query.filter_by(purchase_key=purchase_key).first()


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


def _install_rate_gateway_normalization(app) -> None:
    """Do not present an upstream Packlink 5xx as an internal BT38 500."""
    endpoints = (
        "governed_fbm.packlink_rates",
        "governed_fbm_manual.manual_packlink_rates",
    )
    for endpoint in endpoints:
        current = app.view_functions.get(endpoint)
        if current is None or getattr(current, "_bt38_packlink_rate_gateway_aligned", False):
            continue

        @wraps(current)
        def aligned_rates(*args, __current=current, **kwargs):
            response = __current(*args, **kwargs)
            base, status, headers = _response_parts(response)
            try:
                numeric_status = int(status) if status is not None else int(getattr(base, "status_code", 200) or 200)
            except (TypeError, ValueError):
                numeric_status = 200
            payload = base.get_json(silent=True) if hasattr(base, "get_json") else None

            if numeric_status < 500 or not isinstance(payload, dict) or payload.get("success") is not False:
                return response

            rewritten_payload = dict(payload)
            rewritten_payload["provider"] = "packlink"
            rewritten_payload["provider_status_code"] = numeric_status
            rewritten_payload["provider_error"] = True
            rewritten_payload["quote_created"] = False
            rewritten_payload["label_purchased"] = False
            original_message = str(payload.get("message") or "Packlink rate request failed.").strip()
            rewritten_payload["message"] = (
                "Packlink could not return rates for this parcel. "
                f"{original_message} No quote or label was created."
            )
            rewritten = jsonify(rewritten_payload)
            if headers is None:
                return rewritten, 502
            return rewritten, 502, headers

        aligned_rates._bt38_packlink_rate_gateway_aligned = True
        app.view_functions[endpoint] = aligned_rates


def install_governed_fbm_packlink_idempotency_alignment(app) -> None:
    _install_rate_gateway_normalization(app)

    endpoint = "governed_fbm.packlink_create_draft"
    current = app.view_functions.get(endpoint)
    if current is None or getattr(current, "_bt38_packlink_idempotency_guarded", False):
        return

    @wraps(current)
    def guarded_packlink_draft(*args, **kwargs):
        body = request.get_json(silent=True) or {}
        purpose = _normal(body.get("shipment_purpose"))

        if purpose in {"return", "replacement"}:
            return current(*args, **kwargs)

        order_id = kwargs.get("order_id")
        if order_id is None and args:
            order_id = args[0]
        try:
            order_id = int(order_id)
        except (TypeError, ValueError):
            return current(*args, **kwargs)

        order = db.session.get(MarketplaceOrder, order_id)
        if order is None:
            return current(*args, **kwargs)

        existing = _original_packlink_shipment(order)
        if existing is not None:
            state = _normal(existing.purchase_status)
            provider_reference = str(existing.provider_shipment_id or "").strip()
            tracking = str(existing.tracking_number or "").strip()

            if provider_reference or tracking or state in _UNCERTAIN_OR_CREATED_STATES:
                return jsonify({
                    "success": False,
                    "message": (
                        "A Packlink draft/purchase attempt already exists for this order. "
                        "BT38 will not create another provider shipment until the existing attempt is verified."
                    ),
                    "shipment_id": existing.id,
                    "provider_reference": provider_reference or None,
                    "tracking_number": tracking or None,
                    "purchase_status": existing.purchase_status,
                    "duplicate_provider_shipment_blocked": True,
                    "automatic_retry_allowed": False,
                }), 409

        response = current(*args, **kwargs)
        base, status, headers = _response_parts(response)
        payload = base.get_json(silent=True) if hasattr(base, "get_json") else None

        if isinstance(payload, dict) and payload.get("retry_allowed") is True:
            payload = dict(payload)
            payload["retry_allowed"] = False
            payload["automatic_retry_allowed"] = False
            payload["duplicate_provider_shipment_blocked"] = True
            payload["message"] = (
                "Packlink did not return a fully verified draft result. BT38 has blocked automatic retry "
                "because the provider may already have created the shipment. Verify the existing attempt before retrying."
            )
            rewritten = jsonify(payload)
            if status is None:
                return rewritten
            if headers is None:
                return rewritten, status
            return rewritten, status, headers

        return response

    guarded_packlink_draft._bt38_packlink_idempotency_guarded = True
    app.view_functions[endpoint] = guarded_packlink_draft
