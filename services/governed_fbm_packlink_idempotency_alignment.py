"""Fail closed at the existing Packlink draft boundary.

The original Packlink route already uses one deterministic purchase_key for the
original outbound shipment. This alignment prevents that same shipment from
being re-POSTed to Packlink when a previous provider call is still in progress,
created a provider reference, or ended in an uncertain verification state.

No new shipment path, provider call, poller or background recovery is introduced.
A fresh provider write is allowed only when the persisted original shipment has
no ambiguous prior attempt.
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


def install_governed_fbm_packlink_idempotency_alignment(app) -> None:
    endpoint = "governed_fbm.packlink_create_draft"
    current = app.view_functions.get(endpoint)
    if current is None or getattr(current, "_bt38_packlink_idempotency_guarded", False):
        return

    @wraps(current)
    def guarded_packlink_draft(*args, **kwargs):
        body = request.get_json(silent=True) or {}
        purpose = _normal(body.get("shipment_purpose"))

        # Returns/replacements deliberately use their own additional-shipment
        # confirmation and unique purchase keys. Do not reinterpret them as the
        # deterministic original outbound purchase.
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

        # The core route previously advertised retry_allowed=True after an
        # uncertain provider exception. At that point Packlink may already have
        # accepted POST /shipments, so automatic retry can create a second draft.
        # Persisted verification must happen before another provider write.
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
