"""Pure FBM shipment-state helpers.

No API calls and no writes live here. Provider adapters may use these helpers to
turn provider events/statuses into governed BT38 shipment states.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


CONFIRMED_STATES = {"accepted", "in_transit", "out_for_delivery", "delivered"}
_PACKLINK_PICKUP_PROOF = {
    "ACCEPTED",
    "CARRIER_ACCEPTED",
    "COLLECTED",
    "PICKED_UP",
    "PICKEDUP",
    "RECEIVED_BY_CARRIER",
    "IN_TRANSIT",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
}


def _normalise_provider_status(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .upper()
        .replace(" ", "_")
        .replace("-", "_")
        .replace(".", "_")
        .replace("/", "_")
    )


def _packlink_pickup_is_proven(shipment: Any) -> bool:
    """Treat Packlink booking/label success as pre-pickup until a carrier scan exists.

    Packlink's ``shipment.carrier.success`` means the carrier/service handoff was
    accepted by the platform; it is not proof that the physical parcel was
    collected. Only an explicit persisted carrier journey state may turn Picked
    up green.
    """
    provider = str(getattr(shipment, "provider", "") or "").strip().lower()
    if provider != "packlink":
        return bool(getattr(shipment, "carrier_accepted_at", None))

    status = _normalise_provider_status(getattr(shipment, "last_provider_status", None))
    return status in _PACKLINK_PICKUP_PROOF


def shipment_confirmation_state(shipment: Any, *, now: datetime | None = None) -> str:
    """Return a stable BT38 shipment confirmation state."""
    current = now or datetime.utcnow()

    if getattr(shipment, "delivered_at", None):
        return "delivered"
    if getattr(shipment, "first_movement_at", None):
        return "in_transit"
    if getattr(shipment, "carrier_accepted_at", None) and _packlink_pickup_is_proven(shipment):
        return "accepted"

    due = getattr(shipment, "handover_due_at", None)
    if due and current > due:
        return "acceptance_overdue"

    if getattr(shipment, "label_purchased_at", None):
        return "awaiting_carrier_acceptance"

    return "awaiting_label"


def provider_case_eligibility(shipment: Any, *, now: datetime | None = None) -> dict:
    """Decide whether BT38 should expose an Open case action.

    Eligibility is deliberately based on the DB shipment state, not a browser
    timer. A provider adapter still decides whether the case can be opened by API
    or whether BT38 should send the user to the provider's own support flow.
    """
    state = shipment_confirmation_state(shipment, now=now)

    if state != "acceptance_overdue":
        return {
            "eligible": False,
            "reason": state,
            "case_type": None,
        }

    existing = [
        case for case in (getattr(shipment, "provider_cases", None) or [])
        if str(getattr(case, "status", "") or "").lower() not in {"closed", "resolved", "cancelled"}
    ]
    if existing:
        return {
            "eligible": False,
            "reason": "case_already_open",
            "case_type": "no_carrier_acceptance",
        }

    return {
        "eligible": True,
        "reason": "carrier_acceptance_overdue",
        "case_type": "no_carrier_acceptance",
    }


def normalise_provider_event(event_name: str) -> str | None:
    """Map provider-specific event classes onto BT38's small canonical state set."""
    value = str(event_name or "").strip().lower().replace("-", "_").replace(" ", "_")

    aliases = {
        "accepted": "accepted",
        "carrier_accepted": "accepted",
        "collected": "accepted",
        "picked_up": "accepted",
        "received_by_carrier": "accepted",
        "in_transit": "in_transit",
        "moving": "in_transit",
        "out_for_delivery": "out_for_delivery",
        "delivered": "delivered",
    }
    return aliases.get(value)
