"""Bridge existing StockTransfer records into governed marketplace execution.

This module does not create a new transfer system. It uses the existing
StockTransfer operational state as the source for a governed Amazon FBM/MFN
single-SKU quantity update payload.

No workers, schedulers, queues, public live routes, or direct marketplace calls
are started here.
"""

from __future__ import annotations

from typing import Any

from governed_execution import submit_governed_marketplace_action
from services.governed_approval import create_amazon_fbm_single_sku_approval

FBM_CHANNELS = {"warehouse", "fbm", "mfn"}
FBA_CHANNELS = {"fba", "afn"}


def normalize_transfer_channel(value: Any) -> str:
    return str(value or "").strip().lower()


def channel_to_amazon_fulfillment(value: Any) -> str:
    channel = normalize_transfer_channel(value)
    if channel in FBM_CHANNELS:
        return "MFN"
    if channel in FBA_CHANNELS:
        return "AFN"
    return ""


def transfer_quantity_for_push(transfer: Any) -> int:
    """Use sellable received quantity when present, otherwise planned quantity."""
    qty_sellable = getattr(transfer, "qty_sellable", None)
    if qty_sellable not in (None, ""):
        try:
            sellable = int(qty_sellable)
        except (TypeError, ValueError):
            sellable = 0
        if sellable > 0:
            return sellable

    return int(getattr(transfer, "qty_planned", 0) or 0)


def stock_transfer_governed_payload(
    transfer: Any,
    *,
    store_id: Any,
    listing_id: Any,
    marketplace: str = "amazon",
) -> dict[str, Any]:
    """Build the governed payload from an existing StockTransfer record."""
    warehouse_stock = getattr(transfer, "warehouse_stock", None)
    sku = str(getattr(warehouse_stock, "sku", "") or "").strip()
    from_channel = normalize_transfer_channel(getattr(transfer, "from_location", ""))
    to_channel = normalize_transfer_channel(getattr(transfer, "to_location", ""))
    amazon_fulfillment_channel = channel_to_amazon_fulfillment(to_channel)

    return {
        "marketplace": marketplace,
        "action": "push_inventory",
        "sku": sku,
        "store_id": store_id,
        "listing_id": listing_id,
        "quantity": transfer_quantity_for_push(transfer),
        "amazon_fulfillment_channel": amazon_fulfillment_channel,
        "stock_transfer_id": getattr(transfer, "id", None),
        "stock_transfer_reason": getattr(transfer, "reason", None),
        "stock_transfer_status": getattr(transfer, "status", None),
        "from_channel": from_channel,
        "to_channel": to_channel,
    }


def stock_transfer_governed_approval(
    transfer: Any,
    payload: dict[str, Any],
    *,
    approved_by: str,
) -> dict[str, Any]:
    """Create the approval object for the transfer-derived governed payload."""
    approval = create_amazon_fbm_single_sku_approval(
        sku=payload["sku"],
        store_id=payload["store_id"],
        listing_id=payload["listing_id"],
        quantity=payload["quantity"],
        approved_by=approved_by,
    )
    approval["stock_transfer_id"] = getattr(transfer, "id", None)
    approval["stock_transfer_reason"] = getattr(transfer, "reason", None)
    approval["from_channel"] = payload.get("from_channel")
    approval["to_channel"] = payload.get("to_channel")
    return approval


def submit_stock_transfer_governed_action(
    transfer: Any,
    *,
    store_id: Any,
    listing_id: Any,
    approved_by: str,
    actor: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Submit a StockTransfer through the existing governed execution path."""
    payload = stock_transfer_governed_payload(
        transfer,
        store_id=store_id,
        listing_id=listing_id,
    )
    approval = stock_transfer_governed_approval(
        transfer,
        payload,
        approved_by=approved_by,
    )
    return submit_governed_marketplace_action(
        payload,
        actor=actor or approved_by,
        approval=approval,
        dry_run=dry_run,
    )
