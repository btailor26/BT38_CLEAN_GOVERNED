"""Install Amazon Seller Central purchased-label recovery on the existing profile path.

The wrapper preserves the existing exact Amazon profile result. Label recovery
is best-effort and read-only against Amazon; failure never breaks the FBM page.
No scheduler, poller, order importer, marketplace write, or parallel shipment
path is introduced.
"""
from __future__ import annotations

from typing import Any

from extensions import db
import services.fbm_amazon_order_profile as amazon_profile
from services.governed_amazon_shipping_label_readback import (
    hydrate_amazon_purchased_label_for_order,
)


_ORIGINAL = amazon_profile.get_or_refresh_amazon_profile
_INSTALLED = False


def _aligned_get_or_refresh_amazon_profile(order: Any, *, force: bool = False):
    profile = _ORIGINAL(order, force=force)
    store = getattr(order, "store", None)
    order_id = str(getattr(order, "marketplace_order_id", "") or "").strip()
    fulfillment_type = str(getattr(order, "fulfillment_type", "") or "").strip().upper()
    status = str(getattr(order, "status", "") or "").strip().lower()

    if (
        store is not None
        and order_id
        and fulfillment_type not in {"FBA", "AFN", "MCF"}
        and not status.startswith("mcf_")
        and status in {"shipped", "picked_up", "accepted", "carrier_accepted", "in_transit", "out_for_delivery", "delivered"}
    ):
        try:
            hydrate_amazon_purchased_label_for_order(
                store=store,
                marketplace_order_id=order_id,
                source="fbm_amazon_order_profile",
            )
        except Exception:
            # A failed best-effort readback may leave the shared SQLAlchemy
            # session aborted. Clear that failed unit before the caller resumes
            # normal DB reads; the exact label read can retry on a later event.
            db.session.rollback()
    return profile


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    amazon_profile.get_or_refresh_amazon_profile = _aligned_get_or_refresh_amazon_profile
    _INSTALLED = True


install()
