"""Live FBM feed alignment guards.

MarketplaceOrder remains the source of truth. This module only makes provider
handoffs refresh the exact live marketplace order first; it never creates an
order, buys postage, dispatches, or mutates inventory.
"""
from __future__ import annotations

from typing import Any


def install_live_packlink_alignment() -> None:
    """Force exact eBay hydration before every Packlink quote request.

    This is deliberately at the provider boundary, not the page render path.
    It means old and future eBay orders use the canonical Fulfillment API line
    identity and current buyer destination before Packlink sees the order.
    Safe legacy aliases are collapsed by hydrate_exact_ebay_order, preventing
    duplicate quantities from leaking into the shipment handoff.
    """
    from services import fbm_packlink_adapter as packlink

    current = packlink.PacklinkAdapter.get_rates
    if getattr(current, "_bt38_live_ebay_alignment", False):
        return

    def aligned_get_rates(self, *, order: Any, parcel: dict):
        store = getattr(order, "store", None)
        platform = str(getattr(store, "platform", "") or "").strip().lower()
        order_id = str(getattr(order, "marketplace_order_id", "") or "").strip()
        if store is not None and order_id and "ebay" in platform:
            from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

            result = hydrate_exact_ebay_order(
                store=store,
                marketplace_order_id=order_id,
                source="packlink_live_handoff",
            )
            if not result.get("success"):
                reason = str(result.get("reason") or "exact_ebay_order_hydration_failed")
                detail = str(result.get("error") or "").strip()
                message = f"eBay live order refresh failed: {reason}"
                if detail:
                    message += f" · {detail[:500]}"
                raise packlink.PacklinkRequestError(message)

            # Hydration may have removed a stale alias row. Re-resolve the
            # surviving canonical row so Packlink never continues with a
            # deleted SQLAlchemy object selected by an older UI request.
            from models import MarketplaceOrder

            canonical = (
                MarketplaceOrder.query
                .filter_by(store_id=store.id, marketplace_order_id=order_id)
                .order_by(MarketplaceOrder.id.asc())
                .first()
            )
            if canonical is None:
                raise packlink.PacklinkRequestError("eBay live order disappeared during exact refresh.")
            order = canonical

            # Rebuild the parcel after canonicalisation. This is important for
            # order-level weight/quantity calculations and keeps the provider
            # handoff aligned with the surviving live order rows.
            from services.fbm_order_mapper import provider_parcel

            entered = {
                key: parcel.get(key)
                for key in ("weight_kg", "length_cm", "width_cm", "height_cm")
                if parcel.get(key) not in (None, "")
            }
            parcel = provider_parcel(order, entered)

        return current(self, order=order, parcel=parcel)

    aligned_get_rates._bt38_live_ebay_alignment = True
    aligned_get_rates.__name__ = current.__name__
    aligned_get_rates.__doc__ = current.__doc__
    packlink.PacklinkAdapter.get_rates = aligned_get_rates


install_live_packlink_alignment()
