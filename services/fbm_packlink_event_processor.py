"""Exact event-driven Packlink processing for BT38 FBM.

No polling or batch scanning lives here. A Packlink callback wakes BT38 for one
shipment reference only. The provider is hydrated only for the facts relevant to
that event, so unpaid/unchanged labels remain asleep.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder
from services.fbm_packlink_adapter import PacklinkAdapter, PacklinkRequestError
from services.fbm_packlink_callback import (
    PacklinkCallbackError,
    SUPPORTED_EVENTS,
    _apply_lifecycle_state,
    _attach_by_marketplace_reference,
    _first_label_url,
    _platform,
    _provider_identity,
    extract_packlink_tracking,
)
from services.fbm_post_purchase import persist_external_label


def _event_parts(payload: dict[str, Any]) -> tuple[str, dict[str, Any], str, str | None]:
    event_name = str(payload.get("event") or payload.get("name") or "").strip()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reference = str(
        data.get("shipment_reference")
        or data.get("shipmentReference")
        or payload.get("shipment_reference")
        or ""
    ).strip()
    custom_reference = str(
        data.get("shipment_custom_reference")
        or data.get("shipmentCustomReference")
        or payload.get("shipment_custom_reference")
        or payload.get("shipmentCustomReference")
        or data.get("reference")
        or payload.get("reference")
        or ""
    ).strip() or None
    return event_name, data, reference, custom_reference


def _find_exact_shipment(reference: str, custom_reference: str | None):
    shipment = (
        FBMShipment.query
        .filter_by(provider="packlink", provider_shipment_id=reference)
        .order_by(FBMShipment.id.desc())
        .first()
    )
    order = None
    if shipment is None:
        shipment, order, attach_error = _attach_by_marketplace_reference(
            reference=reference,
            custom_reference=custom_reference,
        )
        return shipment, order, attach_error
    return shipment, order, None


def process_packlink_event(
    payload: dict[str, Any],
    *,
    adapter: PacklinkAdapter | None = None,
) -> dict[str, Any]:
    """Process exactly one Packlink event without polling unrelated shipments."""
    if not isinstance(payload, dict):
        raise PacklinkCallbackError("Packlink callback payload must be a JSON object.")

    event_name, data, reference, custom_reference = _event_parts(payload)
    if not event_name:
        raise PacklinkCallbackError("Packlink callback event name is missing.")
    if event_name not in SUPPORTED_EVENTS:
        return {"success": True, "ignored": True, "event": event_name, "reason": "unsupported_event"}
    if not reference:
        raise PacklinkCallbackError("Packlink callback shipment reference is missing.")

    shipment, order, attach_error = _find_exact_shipment(reference, custom_reference)
    if shipment is None:
        return {
            "success": True,
            "ignored": True,
            "event": event_name,
            "provider_reference": reference,
            "custom_reference": custom_reference,
            "reason": attach_error or "shipment_not_known_to_bt38",
        }

    # Duplicate/replayed provider events must never generate another marketplace write.
    if shipment.marketplace_confirmed_at is not None and event_name == "shipment.label.ready":
        return {
            "success": True,
            "ignored": True,
            "event": event_name,
            "shipment_id": shipment.id,
            "provider_reference": reference,
            "reason": "marketplace_already_confirmed",
        }

    now = datetime.utcnow()
    shipment.last_provider_checked_at = now
    shipment.last_provider_status = event_name

    if event_name in {"shipment.carrier.fail", "shipment.label.fail"}:
        shipment.status = "provider_error"
        shipment.purchase_error = str(data.get("message") or data.get("error") or event_name)
        db.session.commit()
        return {
            "success": True,
            "event": event_name,
            "shipment_id": shipment.id,
            "provider_reference": reference,
            "state": "provider_error",
        }

    adapter = adapter or PacklinkAdapter()

    # Label purchase is the only event that hydrates label + shipment + tracking.
    # No label-ready event means no label call and no marketplace confirmation attempt.
    if event_name == "shipment.label.ready":
        provider_payload = adapter.get_shipment(reference)
        labels = adapter.get_labels(reference)
        tracking_history = adapter.get_tracking_status(reference=reference)
        label_url = _first_label_url(labels)
        if not label_url:
            raise PacklinkRequestError(
                "Packlink reported label ready but the label URL is not readable yet.",
                status_code=503,
            )

        carrier, service, service_id = _provider_identity(provider_payload, shipment)
        tracking = extract_packlink_tracking(
            provider_payload,
            tracking_history,
            shipment.tracking_number,
        )
        if carrier:
            shipment.carrier = carrier
        if service:
            shipment.service = service
        if service_id:
            shipment.provider_service_id = service_id
        if tracking:
            shipment.tracking_number = tracking

        if order is None:
            order = MarketplaceOrder.query.filter_by(
                store_id=shipment.store_id,
                marketplace_order_id=shipment.marketplace_order_id,
            ).order_by(MarketplaceOrder.id.asc()).first()
        if order is None:
            shipment.status = "order_missing"
            db.session.commit()
            return {
                "success": False,
                "held": True,
                "event": event_name,
                "shipment_id": shipment.id,
                "reason": "marketplace_order_missing",
            }

        result = persist_external_label(
            shipment=shipment,
            marketplace=_platform(order),
            provider="packlink",
            provider_shipment_id=reference,
            carrier=carrier,
            service=service,
            tracking_number=tracking,
            provider_service_id=service_id,
            label={"type": "LABEL", "format": "PDF", "url": label_url, "storage_ref": reference},
        )
        return {
            "success": True,
            "event": event_name,
            "shipment_id": shipment.id,
            "provider_reference": reference,
            "custom_reference": custom_reference,
            "label_ready": True,
            "tracking_number": shipment.tracking_number,
            **result,
        }

    # Tracking events hydrate tracking only; they never fetch the label again.
    if event_name == "shipment.tracking.update":
        provider_payload = adapter.get_shipment(reference)
        tracking_history = adapter.get_tracking_status(reference=reference)
        tracking = extract_packlink_tracking(provider_payload, tracking_history, shipment.tracking_number)
        carrier, service, service_id = _provider_identity(provider_payload, shipment)
        if carrier:
            shipment.carrier = carrier
        if service:
            shipment.service = service
        if service_id:
            shipment.provider_service_id = service_id
        if tracking:
            shipment.tracking_number = tracking
        _apply_lifecycle_state(shipment, event_name, now)
        db.session.commit()
        return {
            "success": True,
            "event": event_name,
            "shipment_id": shipment.id,
            "provider_reference": reference,
            "tracking_number": shipment.tracking_number,
            "shipment_status": shipment.status,
        }

    # Carrier success / delivered are exact lifecycle changes. No label or tracking
    # endpoint is called unless Packlink specifically sent a tracking event.
    _apply_lifecycle_state(shipment, event_name, now)
    db.session.commit()
    return {
        "success": True,
        "event": event_name,
        "shipment_id": shipment.id,
        "provider_reference": reference,
        "shipment_status": shipment.status,
    }
