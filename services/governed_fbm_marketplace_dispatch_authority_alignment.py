"""Align FBM shipment presentation to persisted marketplace dispatch truth.

Promise/service-level facts remain historical marketplace order facts. Once the
marketplace has persisted dispatch on the existing MarketplaceOrder identity, that
same DB row becomes the shipment authority shown by FBM. Pre-dispatch provider
choices must not survive as shipment truth after marketplace dispatch.

This is a read/presentation alignment only: no marketplace/provider reads, writes,
pollers, workers, new tables or duplicate shipment identities are introduced.
"""
from __future__ import annotations

from types import SimpleNamespace


_DISPATCHED_STATES = {
    "shipped",
    "dispatched",
    "partially_shipped",
    "partiallydispatched",
    "fulfilled",
    "completed",
}
_TERMINAL_DELIVERY_STATES = {
    "accepted",
    "carrier_accepted",
    "collected",
    "picked_up",
    "in_transit",
    "out_for_delivery",
    "delivered",
}


def _status(value) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _platform_label(row) -> str:
    store = getattr(row, "store", None)
    value = str(getattr(store, "platform", "") or "Marketplace").strip()
    if value.lower() == "amazon":
        return "Amazon"
    if value.lower() == "ebay":
        return "eBay"
    return value or "Marketplace"


def _marketplace_has_dispatch_truth(row) -> bool:
    """Read the dispatch facts already persisted on MarketplaceOrder.

    This is deliberately marketplace-neutral. It does not care where postage was
    bought and it performs no marketplace/provider read or DB write. Historical,
    current and future rows therefore use the same persisted dispatch authority.
    """
    status = _status(getattr(row, "status", None))
    return bool(
        status in _DISPATCHED_STATES
        or status in _TERMINAL_DELIVERY_STATES
        or getattr(row, "shipped_at", None)
        or str(getattr(row, "tracking_number", None) or "").strip()
    )


def _marketplace_shipment(row):
    """Expose only persisted marketplace shipment facts through the existing view contract.

    A marketplace shipped/fulfilled result proves dispatch, not physical carrier
    pickup. A persisted marketplace Delivered result is stronger terminal truth:
    it completes the three-step lifecycle even when a low-scan service (for
    example Royal Mail 2nd Class) did not expose every intermediate scan. We do
    not invent intermediate timestamps; the marketplace delivery timestamp/state
    is the terminal authority until a later marketplace exception supersedes it.
    """
    if not _marketplace_has_dispatch_truth(row):
        return None

    status = _status(getattr(row, "status", None))
    tracking = str(getattr(row, "tracking_number", None) or "").strip() or None
    carrier = str(getattr(row, "carrier", None) or "").strip() or None
    shipped_at = getattr(row, "shipped_at", None)
    changed_at = getattr(row, "updated_at", None) or shipped_at or getattr(row, "created_at", None)
    platform = _platform_label(row)

    carrier_display = carrier or f"{platform} marketplace shipment"
    service_display = "Marketplace dispatch" if tracking else "Tracking pending"

    # Only actual persisted carrier lifecycle states prove pickup/movement.
    # Delivered is terminal marketplace truth and therefore proves the package
    # completed pickup + movement even if eBay/carrier omitted those scans.
    delivered = status == "delivered"
    pickup_proven = status in {
        "accepted", "carrier_accepted", "collected", "picked_up",
        "in_transit", "out_for_delivery", "delivered",
    }
    movement_proven = status in {"in_transit", "out_for_delivery", "delivered"}

    return SimpleNamespace(
        id=None,
        provider="marketplace",
        provider_shipment_id=None,
        provider_carrier_id=None,
        provider_service_id=None,
        purchase_key=None,
        purchase_status=None,
        carrier=carrier_display,
        service=service_display,
        tracking_number=tracking,
        label_url=None,
        label_format=None,
        label_purchased_at=shipped_at or (changed_at if tracking else None),
        handover_due_at=None,
        carrier_accepted_at=changed_at if pickup_proven and not delivered else None,
        first_movement_at=changed_at if movement_proven and not delivered else None,
        delivered_at=changed_at if delivered else None,
        status=status or "dispatched",
        marketplace_confirmed_at=shipped_at,
        marketplace_confirmation_status="marketplace_authoritative",
        mapping_review=None,
        provider_cases=[],
        _bt38_marketplace_owned=True,
        _bt38_terminal_delivery_proves_prior_milestones=delivered,
    )


def install_governed_fbm_marketplace_dispatch_authority_alignment() -> None:
    import services.governed_fbm_page_alignment as page

    if getattr(page, "_bt38_marketplace_dispatch_authority_aligned", False):
        return

    original_shipment_map = page._shipment_map
    original_shipping_mode = page._workspace_shipping_mode
    original_provider_options = page._workspace_provider_options
    original_route_state = page._route_state

    def aligned_shipment_map(rows):
        existing = original_shipment_map(rows)
        result = dict(existing)
        for row in rows:
            if row.store_id is None or not row.marketplace_order_id:
                continue
            key = (int(row.store_id), str(row.marketplace_order_id))
            marketplace = _marketplace_shipment(row)
            if marketplace is not None:
                result[key] = marketplace
            elif not _marketplace_has_dispatch_truth(row):
                result.pop(key, None) if key in result and getattr(result[key], "_bt38_marketplace_owned", False) else None
        return result

    def aligned_shipping_mode(row, platform, profile):
        mode = dict(original_shipping_mode(row, platform, profile))
        if not _marketplace_has_dispatch_truth(row):
            return mode
        marketplace = _platform_label(row)
        mode.update({
            "recommended": f"{marketplace} marketplace shipment",
            "marketplace_buy_shipping": False,
            "external_provider": False,
            "manual": False,
            "reason": "Marketplace dispatch is persisted on this order and now owns shipment truth. Earlier postage-route choices are no longer shipment authority.",
        })
        return mode

    def aligned_provider_options(row, profile):
        options = [dict(option) for option in original_provider_options(row, profile)]
        if not _marketplace_has_dispatch_truth(row):
            return options
        marketplace = _platform_label(row)
        for option in options:
            option["available"] = False
            option["recommended"] = False
            option["message"] = f"{marketplace} has already returned dispatch truth for this order; do not create another shipment."
        return options

    def aligned_route_state(row):
        if _marketplace_has_dispatch_truth(row):
            status = _status(getattr(row, "status", None))
            if status == "delivered":
                return "Delivered"
            if str(getattr(row, "tracking_number", None) or "").strip():
                return "Tracking recorded"
            return "Marketplace dispatched"
        status = _status(getattr(row, "status", None))
        if status in {"pending"}:
            return "Pending"
        if status in {"processed", "confirmed", "unshipped", "order", "ready"}:
            return "Awaiting dispatch"
        current = original_route_state(row)
        return "Awaiting dispatch" if current == "Ready for FBM routing" else current

    page._shipment_map = aligned_shipment_map
    page._workspace_shipping_mode = aligned_shipping_mode
    page._workspace_provider_options = aligned_provider_options
    page._route_state = aligned_route_state
    page._bt38_marketplace_dispatch_authority_aligned = True
