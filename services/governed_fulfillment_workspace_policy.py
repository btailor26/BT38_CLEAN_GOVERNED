"""Pure policy for the BT38 fulfilment workspace.

No marketplace/provider calls and no writes. This is the shared presentation and
reporting boundary for FBM/FBA separation, dispatch state, spend and seller-operated
courier eligibility.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

FBA_CHANNELS = {"FBA", "AFN"}
MCF_CHANNELS = {"MCF"}
FBM_CHANNELS = {"FBM", "MFN"}
TERMINAL_DISPATCH_STATUSES = {"SHIPPED", "DISPATCHED", "DELIVERED"}
CANCELLED_STATUSES = {"CANCELLED", "CANCELED"}

@dataclass(frozen=True)
class FulfillmentSection:
    family: str
    view: str

@dataclass(frozen=True)
class SpendEntry:
    """One physical-dispatch cost. dispatch_key is shipment/label, never order line."""
    dispatch_key: str
    family: str
    amount: Decimal
    currency: str = "GBP"
    source: str = "manual"

def _norm(value: object) -> str:
    return str(value or "").strip().upper()

def fulfillment_family(fulfillment_type: object, profile_channel: object = None) -> str:
    profile = _norm(profile_channel)
    persisted = _norm(fulfillment_type)
    channel = profile or persisted
    if channel in FBA_CHANNELS:
        return "FBA"
    if channel in MCF_CHANNELS:
        return "MCF"
    if channel in FBM_CHANNELS:
        return "FBM"
    return "OTHER"

def fulfillment_section(*, fulfillment_type: object, status: object, shipped_at: object = None, profile_channel: object = None) -> FulfillmentSection:
    family = fulfillment_family(fulfillment_type, profile_channel)
    state = _norm(status)
    if state in CANCELLED_STATUSES:
        return FulfillmentSection(family=family, view="cancelled")
    dispatched = bool(shipped_at) or state in TERMINAL_DISPATCH_STATUSES
    if family == "FBM":
        return FulfillmentSection("FBM", "dispatched" if dispatched else "dispatch_due")
    if family == "FBA":
        return FulfillmentSection("FBA", "dispatched" if dispatched else "pending")
    if family == "MCF":
        return FulfillmentSection("MCF", "dispatched" if dispatched else "pending")
    return FulfillmentSection("OTHER", "dispatched" if dispatched else "pending")

def seller_courier_policy(*, is_prime_or_sfp: bool, owned_channel: bool) -> dict[str, object]:
    if is_prime_or_sfp:
        return {
            "allowed": False,
            "hard_blocked": True,
            "marketplace_metrics_warning": False,
            "reason": "Prime / Seller Fulfilled Prime orders cannot use seller-operated courier.",
        }
    return {
        "allowed": True,
        "hard_blocked": False,
        "marketplace_metrics_warning": not owned_channel,
        "reason": "Seller-operated courier is available; marketplace tracking/performance recognition is not guaranteed." if not owned_channel else "Seller-operated courier is available for the merchant-owned order channel.",
    }

def seller_courier_radius_eligibility(*, enabled: bool, radius_miles: object, distance_miles: object, is_prime_or_sfp: bool, owned_channel: bool) -> dict[str, object]:
    """Apply radius only after a server-side postcode distance has been resolved.

    This function deliberately does not expose or geocode the buyer postcode. A
    marketplace destination can therefore be checked server-side without making
    restricted address/postcode data visible in the browser.
    """
    base = seller_courier_policy(is_prime_or_sfp=is_prime_or_sfp, owned_channel=owned_channel)
    if not enabled:
        return {**base, "eligible": False, "within_radius": False, "reason": "Seller-operated courier is disabled."}
    if not base["allowed"]:
        return {**base, "eligible": False, "within_radius": False}
    try:
        radius = float(radius_miles)
        distance = float(distance_miles)
    except (TypeError, ValueError):
        return {**base, "eligible": False, "within_radius": False, "reason": "Delivery radius or postcode distance is unavailable."}
    if radius <= 0 or distance < 0:
        return {**base, "eligible": False, "within_radius": False, "reason": "Delivery radius or postcode distance is invalid."}
    within = distance <= radius
    return {
        **base,
        "eligible": within,
        "within_radius": within,
        "distance_miles": distance,
        "radius_miles": radius,
        "reason": "Order is within the seller delivery radius." if within else "Order is outside the seller delivery radius.",
    }

def spend_totals(entries: Iterable[SpendEntry]) -> dict[str, Decimal | int]:
    by_key: dict[tuple[str, str], SpendEntry] = {}
    for entry in entries:
        family = _norm(entry.family)
        key = (family, str(entry.dispatch_key).strip())
        if family not in {"FBM", "FBA"} or not key[1]:
            continue
        by_key[key] = entry
    result: dict[str, Decimal | int] = {
        "fbm_dispatches": 0, "fbm_spend": Decimal("0"),
        "fba_dispatches": 0, "fba_spend": Decimal("0"),
    }
    for (family, _), entry in by_key.items():
        if family == "FBM":
            result["fbm_dispatches"] = int(result["fbm_dispatches"]) + 1
            result["fbm_spend"] = Decimal(result["fbm_spend"]) + Decimal(entry.amount)
        elif family == "FBA":
            result["fba_dispatches"] = int(result["fba_dispatches"]) + 1
            result["fba_spend"] = Decimal(result["fba_spend"]) + Decimal(entry.amount)
    return result
