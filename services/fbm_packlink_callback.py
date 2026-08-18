"""Event-driven Packlink shipment callback processing for BT38 FBM.

No polling loop lives here. Packlink wakes BT38 with a shipment event; BT38 then
hydrates that exact Packlink shipment and feeds any paid label/tracking through
the existing post-purchase mapping and marketplace-confirmation path.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder
from services.fbm_packlink_adapter import PacklinkAdapter
from services.fbm_post_purchase import persist_external_label


SUPPORTED_EVENTS = {
    "shipment.carrier.success",
    "shipment.carrier.fail",
    "shipment.label.ready",
    "shipment.label.fail",
    "shipment.tracking.update",
    "shipment.delivered",
}


class PacklinkCallbackError(RuntimeError):
    pass


def _platform(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "platform", "") or "").strip() or "Unknown"


def _tracking_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None

    for key in (
        "tracking_number",
        "trackingNumber",
        "tracking_code",
        "trackingCode",
        "tracking",
        "parcel_tracking_number",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _tracking_code(value)
            if nested:
                return nested

    # Packlink shipment details use tracking_codes as an array. Accept both
    # strings and object forms so a label-ready callback can immediately carry
    # tracking into the existing marketplace confirmation path.
    for key in ("tracking_codes", "trackings"):
        values = payload.get(key)
        if isinstance(values, str) and values.strip():
            return values.strip()
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    candidate = (
                        value.get("code")
                        or value.get("tracking_number")
                        or value.get("tracking")
                    )
                    if candidate:
                        return str(candidate).strip() or None

    for key in ("carrier", "shipment", "package", "tracking_info"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            value = _tracking_code(nested)
            if value:
                return value
    return None


def _latest_tracking(history: list[dict[str, Any]]) -> str | None:
    for item in reversed(history or []):
        value = _tracking_code(item)
        if value:
            return value
    return None


def _first_label_url(labels: list[Any]) -> str | None:
    for label in labels or []:
        if isinstance(label, str) and label.strip():
            return label.strip()
        if isinstance(label, dict):
            for key in ("url", "label_url", "download_url"):
                value = label.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _provider_identity(
    provider_payload: dict[str, Any],
    shipment: FBMShipment,
) -> tuple[str | None, str | None, str | None]:
    carrier_raw = provider_payload.get("carrier")
    if isinstance(carrier_raw, dict):
        carrier = (
            str(carrier_raw.get("name") or carrier_raw.get("label") or "").strip()
            or shipment.carrier
        )
    else:
        carrier = str(carrier_raw or shipment.carrier or "").strip() or None

    service_raw = provider_payload.get("service")
    if isinstance(service_raw, dict):
        service = (
            str(service_raw.get("name") or service_raw.get("label") or "").strip()
            or shipment.service
        )
        service_id = str(
            service_raw.get("id")
            or provider_payload.get("service_id")
            or shipment.provider_service_id
            or ""
        ).strip() or None
    else:
        service = str(
            service_raw or provider_payload.get("service_name") or shipment.service or ""
        ).strip() or None
        service_id = str(
            provider_payload.get("service_id") or shipment.provider_service_id or ""
        ).strip() or None
    return carrier, service, service_id


def _apply_lifecycle_state(shipment: FBMShipment, event_name: str, now: datetime) -> None:
    """Apply the strongest provider lifecycle state after label persistence.

    ``persist_external_label`` intentionally resets a newly paid label to
    awaiting carrier acceptance. Later Packlink callbacks must therefore be
    applied after that function so in-transit/delivered state can never be
    downgraded by a repeated label read.
    """
    if event_name == "shipment.carrier.success":
        shipment.carrier_accepted_at = shipment.carrier_accepted_at or now
        if shipment.delivered_at is None and shipment.first_movement_at is None:
            shipment.status = "accepted"
    elif event_name == "shipment.tracking.update":
        shipment.carrier_accepted_at = shipment.carrier_accepted_at or now
        shipment.first_movement_at = shipment.first_movement_at or now
        if shipment.delivered_at is None:
            shipment.status = "in_transit"
    elif event_name == "shipment.delivered":
        shipment.carrier_accepted_at = shipment.carrier_accepted_at or now
        shipment.first_movement_at = shipment.first_movement_at or now
        shipment.delivered_at = shipment.delivered_at or now
        shipment.status = "delivered"


def process_packlink_callback(
    payload: dict[str, Any],
    *,
    adapter: PacklinkAdapter | None = None,
) -> dict[str, Any]:
    """Process one Packlink callback safely and idempotently."""
    if not isinstance(payload, dict):
        raise PacklinkCallbackError("Packlink callback payload must be a JSON object.")

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
        or ""
    ).strip() or None

    if not event_name:
        raise PacklinkCallbackError("Packlink callback event name is missing.")
    if event_name not in SUPPORTED_EVENTS:
        return {
            "success": True,
            "ignored": True,
            "event": event_name,
            "reason": "unsupported_event",
        }
    if not reference:
        raise PacklinkCallbackError("Packlink callback shipment reference is missing.")

    shipment = (
        FBMShipment.query
        .filter_by(provider="packlink", provider_shipment_id=reference)
        .order_by(FBMShipment.id.desc())
        .first()
    )
    if shipment is None:
        return {
            "success": True,
            "ignored": True,
            "event": event_name,
            "provider_reference": reference,
            "custom_reference": custom_reference,
            "reason": "shipment_not_known_to_bt38",
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
    provider_payload = adapter.get_shipment(reference)
    labels = adapter.get_labels(reference)
    tracking_history = adapter.get_tracking_status(reference=reference)

    provider_state = str(
        provider_payload.get("state") or provider_payload.get("status") or event_name
    ).strip()
    shipment.last_provider_status = provider_state
    shipment.last_provider_checked_at = now
    carrier, service, service_id = _provider_identity(provider_payload, shipment)
    tracking = (
        _tracking_code(provider_payload)
        or _latest_tracking(tracking_history)
        or shipment.tracking_number
    )

    if carrier:
        shipment.carrier = carrier
    if service:
        shipment.service = service
    if service_id:
        shipment.provider_service_id = service_id
    if tracking:
        shipment.tracking_number = tracking

    label_url = _first_label_url(labels)
    result: dict[str, Any] | None = None
    if label_url:
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
            label={
                "type": "LABEL",
                "format": "PDF",
                "url": label_url,
                "storage_ref": reference,
            },
        )

    # Apply provider movement/delivery AFTER label persistence, because the
    # shared post-purchase function correctly initializes a new label at
    # awaiting_carrier_acceptance. This makes repeated callbacks monotonic.
    _apply_lifecycle_state(shipment, event_name, now)
    db.session.commit()

    response = {
        "success": True,
        "event": event_name,
        "shipment_id": shipment.id,
        "provider_reference": reference,
        "label_ready": bool(label_url),
        "tracking_number": tracking,
        "provider_status": provider_state,
        "shipment_status": shipment.status,
    }
    if result:
        response.update(result)
        # Preserve the lifecycle state in the response even when the shared
        # post-purchase result is merged in.
        response["shipment_status"] = shipment.status
    return response
