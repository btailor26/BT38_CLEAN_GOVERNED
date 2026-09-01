"""Pure policy for the BT38 fulfilment workspace.

This module deliberately owns no marketplace/provider calls and performs no writes.
It locks the presentation/reporting boundaries agreed for the existing governed
order and shipment authorities before UI wiring is changed:

* FBM and FBA are separate workspaces and separate spend reports.
* The default FBM queue is dispatch work, not historical dispatched orders.
* Dispatched orders remain real records and move to the dispatched view.
* FBA pending remains in the FBA pending/dispatch view until Amazon truth says
  it is dispatched.
* Shipping spend is per physical dispatch/shipment, never multiplied by units.
* Seller-operated courier is a distinct FBM route. Prime/SFP is hard blocked.
* Marketplace seller-courier use requires a metrics warning; owned-channel
  orders do not require that marketplace warning.

It is intentionally dependency-free so routes, templates and tests can share one
rule without creating another order, shipment or marketplace authority.
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
    """One recorded physical-dispatch cost used by reporting.

    `dispatch_key` must identify the shipment/label/dispatch, not an order line.
    Two units travelling under one label therefore remain one spend entry.
    """

    dispatch_key: str
    family: str
    amount: Decimal
    currency: str = "GBP"
    source: str = "manual"


def _norm(value: object) -> str:
    return str(value or "").strip().upper()


def fulfillment_family(fulfillment_type: object, profile_channel: object = None) -> str:
    """Return FBM/FBA/MCF without allowing one family into another workspace."""
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


def fulfillment_section(
    *,
    fulfillment_type: object,
    status: object,
    shipped_at: object = None,
    profile_channel: object = None,
) -> FulfillmentSection:
    """Place every persisted order into one fulfilment section.

    This does not decide carrier pickup. `shipped_at`/terminal marketplace status
    is only the persisted dispatch boundary used to move an order out of the
    dispatch-due working queue and into its dispatched/history view.
    """
    family = fulfillment_family(fulfillment_type, profile_channel)
    state = _norm(status)

    if state in CANCELLED_STATUSES:
        return FulfillmentSection(family=family, view="cancelled")

    dispatched = bool(shipped_at) or state in TERMINAL_DISPATCH_STATUSES
    if family == "FBM":
        return FulfillmentSection(family="FBM", view="dispatched" if dispatched else "dispatch_due")
    if family == "FBA":
        return FulfillmentSection(family="FBA", view="dispatched" if dispatched else "pending")
    if family == "MCF":
        return FulfillmentSection(family="MCF", view="dispatched" if dispatched else "pending")
    return FulfillmentSection(family="OTHER", view="dispatched" if dispatched else "pending")


def seller_courier_policy(*, is_prime_or_sfp: bool, owned_channel: bool) -> dict[str, object]:
    """Return the seller-courier gate without exposing buyer address data."""
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
        "reason": (
            "Seller-operated courier is available. Marketplace orders require a tracking/metrics warning."
            if not owned_channel
            else "Seller-operated courier is available for the merchant-owned order channel."
        ),
    }


def spend_totals(entries: Iterable[SpendEntry]) -> dict[str, Decimal | int]:
    """Deduplicate by physical dispatch and keep FBM/FBA spend independent."""
    by_key: dict[tuple[str, str], SpendEntry] = {}
    for entry in entries:
        family = _norm(entry.family)
        key = (family, str(entry.dispatch_key).strip())
        if family not in {"FBM", "FBA"} or not key[1]:
            continue
        # Reconciliation should resolve source priority before this reporting
        # boundary. Repeated representations of the same dispatch count once.
        by_key[key] = entry

    result: dict[str, Decimal | int] = {
        "fbm_dispatches": 0,
        "fbm_spend": Decimal("0"),
        "fba_dispatches": 0,
        "fba_spend": Decimal("0"),
    }
    for (family, _), entry in by_key.items():
        if family == "FBM":
            result["fbm_dispatches"] = int(result["fbm_dispatches"]) + 1
            result["fbm_spend"] = Decimal(result["fbm_spend"]) + Decimal(entry.amount)
        elif family == "FBA":
            result["fba_dispatches"] = int(result["fba_dispatches"]) + 1
            result["fba_spend"] = Decimal(result["fba_spend"]) + Decimal(entry.amount)
    return result
