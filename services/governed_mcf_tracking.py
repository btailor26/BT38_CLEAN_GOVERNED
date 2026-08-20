"""Durable multi-tracking helper for the existing governed MCF path.

Amazon MCF may split one fulfilment order across any number of shipments or
packages. This module extracts the complete tracking set from the current
Amazon response and retains it through the existing SystemEvent audit table so
Fly sleep/restart cannot discard tracking before marketplace enrichment.

No new table, migration, worker, scheduler or execution path is introduced.
The existing MCF order scalar carrier/tracking fields remain the backwards-
compatible primary display values.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


_latest_tracking: dict[int, list[dict[str, Any]]] = {}
_forwarded_tracking: dict[int, tuple[str, ...]] = {}


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except Exception:
        return None


def _serialise_details(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in details or []:
        row = dict(item)
        for key in ("ship_date", "estimated_arrival_date"):
            value = row.get(key)
            if isinstance(value, datetime):
                row[key] = value.isoformat()
        rows.append(row)
    return rows


def _deserialise_details(details: Any) -> list[dict[str, Any]]:
    rows = []
    for item in details or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["ship_date"] = _as_datetime(row.get("ship_date"))
        row["estimated_arrival_date"] = _as_datetime(
            row.get("estimated_arrival_date")
        )
        tracking_number = str(row.get("tracking_number") or "").strip()
        if tracking_number:
            row["tracking_number"] = tracking_number
            rows.append(row)
    return rows


def _durable_state(mcf_order_id: int):
    """Return the latest existing durable MCF tracking audit row, if any."""
    try:
        from models import SystemEvent

        return (
            SystemEvent.query
            .filter(
                SystemEvent.category == "mcf_tracking_state",
                SystemEvent.entity_type == "mcf_order",
                SystemEvent.entity_id == int(mcf_order_id),
            )
            .order_by(SystemEvent.id.desc())
            .first()
        )
    except Exception:
        return None


def _persist_state(
    mcf_order_id: int,
    details: list[dict[str, Any]],
    forwarded: tuple[str, ...] | None = None,
) -> None:
    """Persist tracking state using the existing audit table; no schema change."""
    try:
        from extensions import db
        from models import SystemEvent

        order_id = int(mcf_order_id)
        existing = _durable_state(order_id)
        previous = dict(getattr(existing, "details_json", None) or {})
        forwarded_numbers = (
            list(forwarded)
            if forwarded is not None
            else list(previous.get("forwarded_tracking_numbers") or [])
        )
        payload = {
            "tracking_details": _serialise_details(details),
            "tracking_numbers": [
                str(item.get("tracking_number") or "").strip()
                for item in details
                if str(item.get("tracking_number") or "").strip()
            ],
            "forwarded_tracking_numbers": forwarded_numbers,
            "updated_at": datetime.utcnow().isoformat(),
        }

        if existing is None:
            existing = SystemEvent(
                actor="system",
                category="mcf_tracking_state",
                entity_id=order_id,
                entity_type="mcf_order",
                description="Amazon MCF tracking state",
                details_json=payload,
            )
            db.session.add(existing)
        else:
            existing.details_json = payload
            existing.timestamp = datetime.utcnow()

        db.session.commit()
    except Exception:
        # Tracking extraction must never fail merely because audit persistence
        # is temporarily unavailable. The caller still has the live Amazon set.
        try:
            from extensions import db
            db.session.rollback()
        except Exception:
            pass


def amazon_tracking_details(
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return every unique tracking number from all Amazon MCF packages."""
    details: list[dict[str, Any]] = []
    seen: set[str] = set()

    for shipment in (payload or {}).get("fulfillmentShipments") or []:
        packages = (
            shipment.get("fulfillmentShipmentPackage")
            or shipment.get("fulfillmentShipmentPackages")
            or []
        )
        package_rows = packages or [shipment]

        for package in package_rows:
            tracking_number = str(
                package.get("trackingNumber")
                or shipment.get("trackingNumber")
                or ""
            ).strip()
            if not tracking_number or tracking_number in seen:
                continue

            seen.add(tracking_number)
            details.append({
                "tracking_number": tracking_number,
                "carrier": str(
                    package.get("carrierCode")
                    or shipment.get("carrierCode")
                    or "Other"
                ).strip() or "Other",
                "ship_date": _as_datetime(
                    shipment.get("shippingDate")
                    or shipment.get("shipDate")
                ),
                "estimated_arrival_date": _as_datetime(
                    shipment.get("estimatedArrivalDate")
                ),
            })

    return details


def sync_amazon_tracking_details(
    mcf_order_id: int,
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract and durably retain the complete current Amazon tracking set."""
    order_id = int(mcf_order_id)
    details = amazon_tracking_details(payload)
    _latest_tracking[order_id] = details
    if details:
        _persist_state(order_id, details)
    return details


def load_tracking_details(mcf_order_id: int) -> list[dict[str, Any]]:
    order_id = int(mcf_order_id)
    current = list(_latest_tracking.get(order_id, []))
    if current:
        return current

    state = _durable_state(order_id)
    payload = dict(getattr(state, "details_json", None) or {}) if state else {}
    details = _deserialise_details(payload.get("tracking_details"))
    if details:
        _latest_tracking[order_id] = details
    return list(details)


def _forwarded_numbers(mcf_order_id: int) -> tuple[str, ...]:
    order_id = int(mcf_order_id)
    if order_id in _forwarded_tracking:
        return _forwarded_tracking[order_id]

    state = _durable_state(order_id)
    payload = dict(getattr(state, "details_json", None) or {}) if state else {}
    forwarded = tuple(
        str(value or "").strip()
        for value in payload.get("forwarded_tracking_numbers") or []
        if str(value or "").strip()
    )
    _forwarded_tracking[order_id] = forwarded
    return forwarded


def has_unforwarded_tracking(mcf_order_id: int) -> bool:
    order_id = int(mcf_order_id)
    details = load_tracking_details(order_id)
    current = tuple(
        str(item.get("tracking_number") or "").strip()
        for item in details
        if str(item.get("tracking_number") or "").strip()
    )
    return bool(current) and _forwarded_numbers(order_id) != current


def mark_tracking_forwarded(mcf_order_id: int) -> None:
    order_id = int(mcf_order_id)
    details = load_tracking_details(order_id)
    forwarded = tuple(
        str(item.get("tracking_number") or "").strip()
        for item in details
        if str(item.get("tracking_number") or "").strip()
    )
    _forwarded_tracking[order_id] = forwarded
    _persist_state(order_id, details, forwarded=forwarded)
