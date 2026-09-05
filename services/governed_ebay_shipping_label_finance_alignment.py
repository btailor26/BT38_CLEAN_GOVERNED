"""Attach exact eBay label-finance truth to the existing exact hydration path.

This is a narrow governed wrapper around the already-installed exact eBay order
hydration. It does not create a second importer, worker, poller or marketplace
write path. The Finances read is attempted only after the exact eBay hydration
has found shipment fulfilment evidence for that same order. Confirmed purchase
truth is then joined to the exact eBay fulfillment identity in the existing
FBMShipment table so stale order-level carrier metadata cannot become physical
shipment authority.
"""
from __future__ import annotations

from services import governed_exact_ebay_order_hydration as _exact
from services.governed_ebay_shipping_label_finance import (
    read_and_persist_exact_ebay_shipping_label_purchase,
)
from services.governed_ebay_shipping_label_readback import (
    persist_exact_ebay_purchased_shipment_authority,
)


_ORIGINAL_HYDRATE = _exact.hydrate_exact_ebay_order


def _hydrate_with_shipping_label_finance(*, store, marketplace_order_id: str, source: str):
    result = _ORIGINAL_HYDRATE(
        store=store,
        marketplace_order_id=marketplace_order_id,
        source=source,
    )

    finance_result = {
        "success": False,
        "skipped": True,
        "reason": "exact_ebay_shipment_not_present",
    }
    authority_result = {
        "success": False,
        "skipped": True,
        "reason": "ebay_shipping_label_purchase_not_confirmed",
    }
    if (
        isinstance(result, dict)
        and not result.get("skipped")
        and int(result.get("fulfillment_lifecycle_rows") or 0) > 0
    ):
        finance_result = read_and_persist_exact_ebay_shipping_label_purchase(
            store=store,
            marketplace_order_id=marketplace_order_id,
        )
        if isinstance(finance_result, dict) and finance_result.get("purchase_confirmed") is True:
            authority_result = persist_exact_ebay_purchased_shipment_authority(
                store=store,
                marketplace_order_id=marketplace_order_id,
            )

    if isinstance(result, dict):
        result["shipping_label_finance"] = finance_result
        result["shipping_label_shipment_authority"] = authority_result
    return result


def install_ebay_shipping_label_finance_alignment() -> None:
    """Install once; all existing exact-hydration callers keep the same function."""
    if getattr(_exact, "_bt38_shipping_label_finance_aligned", False):
        return
    _exact.hydrate_exact_ebay_order = _hydrate_with_shipping_label_finance
    _exact._bt38_shipping_label_finance_aligned = True


install_ebay_shipping_label_finance_alignment()
