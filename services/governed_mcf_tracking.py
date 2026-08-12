"""Small multi-tracking helper for the existing governed MCF path.

Amazon MCF may split one fulfilment order across any number of shipments or
packages. This module only extracts the complete tracking set from the current
Amazon response and remembers the latest set inside the running process so the
existing marketplace dispatch path can forward it once per change.

No database table, migration, worker, scheduler or second execution path is
introduced. The existing MCF order scalar carrier/tracking fields remain the
backwards-compatible primary display values.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


_latest_tracking: dict[int, list[dict[str, Any]]] = {}
_forwarded_tracking: dict[int, tuple[str, ...]] = {}


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except Exception:
        return None


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
    """Extract and retain the current response tracking set in process only."""
    details = amazon_tracking_details(payload)
    _latest_tracking[int(mcf_order_id)] = details
    return details


def load_tracking_details(mcf_order_id: int) -> list[dict[str, Any]]:
    return list(_latest_tracking.get(int(mcf_order_id), []))


def has_unforwarded_tracking(mcf_order_id: int) -> bool:
    order_id = int(mcf_order_id)
    current = tuple(
        str(item.get("tracking_number") or "").strip()
        for item in _latest_tracking.get(order_id, [])
        if str(item.get("tracking_number") or "").strip()
    )
    return bool(current) and _forwarded_tracking.get(order_id) != current


def mark_tracking_forwarded(mcf_order_id: int) -> None:
    order_id = int(mcf_order_id)
    _forwarded_tracking[order_id] = tuple(
        str(item.get("tracking_number") or "").strip()
        for item in _latest_tracking.get(order_id, [])
        if str(item.get("tracking_number") or "").strip()
    )
