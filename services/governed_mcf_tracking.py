"""Shipment-level tracking persistence for the existing governed MCF path.

Amazon MCF may split one fulfilment order across multiple shipments/packages.
The existing MCF order scalar carrier/tracking fields remain as the backwards-
compatible primary tracking view; this helper preserves the complete package
set so the same governed marketplace dispatch path can forward every tracking
number without creating a second worker or polling path.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from extensions import db


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        ).replace(tzinfo=None)
    except Exception:
        return None


def amazon_tracking_details(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return every unique trackable Amazon MCF package in response order."""
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
                "shipment_status": str(
                    shipment.get("fulfillmentShipmentStatus")
                    or shipment.get("shipmentStatus")
                    or shipment.get("status")
                    or ""
                ).strip() or None,
            })

    return details


def sync_amazon_tracking_details(
    mcf_order_id: int,
    payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Upsert only changed package rows, then return the full saved set."""
    now = datetime.utcnow()

    for detail in amazon_tracking_details(payload):
        db.session.execute(
            text(
                """
                INSERT INTO mcf_order_shipments (
                    mcf_order_id,
                    tracking_number,
                    carrier,
                    ship_date,
                    estimated_arrival_date,
                    shipment_status,
                    created_at,
                    updated_at
                ) VALUES (
                    :mcf_order_id,
                    :tracking_number,
                    :carrier,
                    :ship_date,
                    :estimated_arrival_date,
                    :shipment_status,
                    :now,
                    :now
                )
                ON CONFLICT (mcf_order_id, tracking_number)
                DO UPDATE SET
                    carrier = EXCLUDED.carrier,
                    ship_date = EXCLUDED.ship_date,
                    estimated_arrival_date = EXCLUDED.estimated_arrival_date,
                    shipment_status = EXCLUDED.shipment_status,
                    updated_at = EXCLUDED.updated_at
                WHERE
                    mcf_order_shipments.carrier IS DISTINCT FROM EXCLUDED.carrier
                    OR mcf_order_shipments.ship_date IS DISTINCT FROM EXCLUDED.ship_date
                    OR mcf_order_shipments.estimated_arrival_date IS DISTINCT FROM EXCLUDED.estimated_arrival_date
                    OR mcf_order_shipments.shipment_status IS DISTINCT FROM EXCLUDED.shipment_status
                """
            ),
            {
                "mcf_order_id": int(mcf_order_id),
                "tracking_number": detail["tracking_number"],
                "carrier": detail["carrier"],
                "ship_date": detail["ship_date"],
                "estimated_arrival_date": detail["estimated_arrival_date"],
                "shipment_status": detail["shipment_status"],
                "now": now,
            },
        )

    return load_tracking_details(mcf_order_id)


def load_tracking_details(mcf_order_id: int) -> list[dict[str, Any]]:
    rows = db.session.execute(
        text(
            """
            SELECT
                tracking_number,
                carrier,
                ship_date,
                estimated_arrival_date,
                shipment_status,
                marketplace_forwarded_at
            FROM mcf_order_shipments
            WHERE mcf_order_id = :mcf_order_id
            ORDER BY COALESCE(ship_date, created_at), id
            """
        ),
        {"mcf_order_id": int(mcf_order_id)},
    ).mappings().all()

    return [dict(row) for row in rows]


def has_unforwarded_tracking(mcf_order_id: int) -> bool:
    return bool(
        db.session.execute(
            text(
                """
                SELECT 1
                FROM mcf_order_shipments
                WHERE mcf_order_id = :mcf_order_id
                  AND marketplace_forwarded_at IS NULL
                LIMIT 1
                """
            ),
            {"mcf_order_id": int(mcf_order_id)},
        ).first()
    )


def mark_tracking_forwarded(mcf_order_id: int) -> None:
    db.session.execute(
        text(
            """
            UPDATE mcf_order_shipments
            SET marketplace_forwarded_at = :now,
                updated_at = :now
            WHERE mcf_order_id = :mcf_order_id
              AND marketplace_forwarded_at IS NULL
            """
        ),
        {
            "mcf_order_id": int(mcf_order_id),
            "now": datetime.utcnow(),
        },
    )
