"""Event-driven Packlink shipment callback processing for BT38 FBM.

No polling loop lives here. Packlink wakes BT38 with a shipment event; BT38 then
hydrates that exact Packlink shipment and feeds any paid label/tracking through
the existing post-purchase mapping and marketplace-confirmation path.

Packlink's shipment custom reference is the marketplace order reference. That
allows a paid Packlink shipment to flow back into BT38 even when BT38 did not
create a Packlink draft first.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder
from services.fbm_packlink_adapter import PacklinkAdapter, PacklinkRequestError
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


def extract_packlink_tracking(
    provider_payload: Any,
    tracking_history: list[dict[str, Any]] | None = None,
    fallback: Any = None,
) -> str | None:
    """Resolve one Packlink tracking number consistently for every BT38 path."""
    direct = _tracking_code(provider_payload)
    if direct:
        return direct
    history_value = _latest_tracking(tracking_history or [])
    if history_value:
        return history_value
    fallback_text = str(fallback or "").strip()
    return fallback_text or None


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


def _attach_by_marketplace_reference(
    *,
    reference: str,
    custom_reference: str | None,
) -> tuple[FBMShipment | None, MarketplaceOrder | None, str | None]:
    """Attach an externally-created Packlink shipment using marketplace Reference.

    Tracking is the completion boundary. Before tracking exists, a newer Packlink
    shipment for the same order may replace the stale/deleted draft reference.
    After tracking exists, a different Packlink shipment using the same order
    number is held until the user classifies it as a return or replacement.
    """
    if not custom_reference:
        return None, None, "marketplace_reference_missing"

    orders = (
        MarketplaceOrder.query
        .filter_by(marketplace_order_id=custom_reference)
        .order_by(MarketplaceOrder.id.asc())
        .all()
    )
    if not orders:
        return None, None, "marketplace_order_not_found"
    if len(orders) != 1:
        return None, None, "marketplace_reference_ambiguous"

    order = orders[0]
    shipment = (
        FBMShipment.query
        .filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
            provider="packlink",
        )
        .order_by(FBMShipment.id.desc())
        .first()
    )
    if shipment is None:
        shipment = FBMShipment(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
            provider="packlink",
            provider_shipment_id=reference,
            purchase_key=f"packlink_external:{order.store_id}:{order.marketplace_order_id}",
            purchase_status="provider_event_received",
            status="awaiting_label",
        )
        db.session.add(shipment)
    else:
        existing_reference = str(shipment.provider_shipment_id or "").strip()
        completed_tracking = str(shipment.tracking_number or "").strip()
        if completed_tracking and existing_reference and existing_reference != reference:
            return (
                None,
                order,
                "additional_shipment_requires_return_or_replacement_confirmation",
            )
        shipment.provider_shipment_id = reference
        if shipment.purchase_status not in {"purchased"}:
            shipment.purchase_status = "provider_event_received"
    db.session.commit()
    return shipment, order, None


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
        or payload.get("shipmentCustomReference")
        or data.get("reference")
        or payload.get("reference")
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
    order = None
    if shipment is None:
        shipment, order, attach_error = _attach_by_marketplace_reference(
            reference=reference,
            custom_reference=custom_reference,
        )
        if shipment is None:
            return {
                "success": True,
                "ignored": True,
                "event": event_name,
                "provider_reference": reference,
                "custom_reference": custom_reference,
                "reason": attach_error or "shipment_not_known_to_bt38",
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

    label_url = _first_label_url(labels)
    if event_name == "shipment.label.ready" and not label_url:
        raise PacklinkRequestError(
            "Packlink reported label ready but the label URL is not readable yet.",
            status_code=503,
        )

    result: dict[str, Any] | None = None
    if label_url:
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
            label={
                "type": "LABEL",
                "format": "PDF",
                "url": label_url,
                "storage_ref": reference,
            },
        )

    _apply_lifecycle_state(shipment, event_name, now)
    db.session.commit()

    response = {
        "success": True,
        "event": event_name,
        "shipment_id": shipment.id,
        "provider_reference": reference,
        "custom_reference": custom_reference,
        "label_ready": bool(label_url),
        "tracking_number": tracking,
        "provider_status": provider_state,
        "shipment_status": shipment.status,
    }
    if result:
        response.update(result)
        response["shipment_status"] = shipment.status
    return response


def recover_packlink_shipments_for_day(
    target_day: date,
    *,
    adapter: PacklinkAdapter | None = None,
) -> dict[str, Any]:
    """One-shot recovery for exact Packlink shipments already known to BT38.

    This is deliberately not a polling loop. It hydrates only Packlink shipment
    references created on the requested day and still not confirmed to their
    marketplace. Paid labels flow through the exact same callback/post-purchase
    confirmation path as a live Packlink callback.
    """
    start = datetime.combine(target_day, time.min)
    end = start + timedelta(days=1)
    shipments = (
        FBMShipment.query
        .filter(
            FBMShipment.provider == "packlink",
            FBMShipment.provider_shipment_id.isnot(None),
            FBMShipment.created_at >= start,
            FBMShipment.created_at < end,
            FBMShipment.marketplace_confirmed_at.is_(None),
        )
        .order_by(FBMShipment.id.asc())
        .all()
    )

    adapter = adapter or PacklinkAdapter()
    results: list[dict[str, Any]] = []
    for shipment in shipments:
        try:
            result = process_packlink_callback(
                {
                    "event": "shipment.label.ready",
                    "data": {
                        "shipment_reference": shipment.provider_shipment_id,
                        "shipment_custom_reference": shipment.marketplace_order_id,
                    },
                },
                adapter=adapter,
            )
            results.append({
                "shipment_id": shipment.id,
                "marketplace_order_id": shipment.marketplace_order_id,
                "provider_reference": shipment.provider_shipment_id,
                **result,
            })
        except PacklinkRequestError as exc:
            results.append({
                "success": False,
                "shipment_id": shipment.id,
                "marketplace_order_id": shipment.marketplace_order_id,
                "provider_reference": shipment.provider_shipment_id,
                "message": str(exc),
                "status_code": exc.status_code,
            })
        except Exception as exc:
            results.append({
                "success": False,
                "shipment_id": shipment.id,
                "marketplace_order_id": shipment.marketplace_order_id,
                "provider_reference": shipment.provider_shipment_id,
                "message": str(exc),
            })

    return {
        "success": True,
        "target_day": target_day.isoformat(),
        "checked": len(shipments),
        "results": results,
    }
