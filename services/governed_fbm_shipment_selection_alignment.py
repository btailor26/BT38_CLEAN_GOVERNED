"""Align the FBM read selector to persisted physical shipment authority.

The existing FBMShipment table remains the only shipment store.  This module
changes only which already-persisted row is selected for an order when several
rows exist (for example a marketplace proxy plus a Packlink purchase).

Rules:
- original outbound shipment stays ahead of return/replacement shipments;
- within the original outbound set, a purchased physical provider shipment
  outranks a marketplace proxy even when the proxy matches MarketplaceOrder
  tracking;
- marketplace tracking remains a fallback when no stronger purchased physical
  authority exists;
- no marketplace/provider call or write occurs here.
"""
from __future__ import annotations

from fbm_models import FBMShipment


_INSTALLED = False


def _text(value) -> str:
    return str(value or "").strip()


def _aligned_shipment_map(rows) -> dict[tuple[int, str], FBMShipment]:
    keys = {
        (row.store_id, row.marketplace_order_id)
        for row in rows
        if getattr(row, "store_id", None) is not None
        and getattr(row, "marketplace_order_id", None)
    }
    if not keys:
        return {}

    order_tracking_by_key: dict[tuple[int, str], str] = {}
    for row in rows:
        if getattr(row, "store_id", None) is None or not getattr(row, "marketplace_order_id", None):
            continue
        tracking = _text(getattr(row, "tracking_number", None)).upper()
        if tracking:
            order_tracking_by_key.setdefault(
                (int(row.store_id), str(row.marketplace_order_id)),
                tracking,
            )

    store_ids = sorted({int(key[0]) for key in keys})
    order_ids = sorted({str(key[1]) for key in keys})
    shipments = (
        FBMShipment.query
        .filter(FBMShipment.store_id.in_(store_ids))
        .filter(FBMShipment.marketplace_order_id.in_(order_ids))
        .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
        .all()
    )

    def rank(shipment: FBMShipment) -> tuple[int, ...]:
        key = (int(shipment.store_id), str(shipment.marketplace_order_id))
        provider = _text(getattr(shipment, "provider", None)).lower()
        purchase_status = _text(getattr(shipment, "purchase_status", None)).lower()
        purchase_key = _text(getattr(shipment, "purchase_key", None)).lower()
        tracking = _text(getattr(shipment, "tracking_number", None)).upper()
        order_tracking = order_tracking_by_key.get(key, "")

        additional = purchase_key.startswith((
            "packlink_return:",
            "packlink_replacement:",
        ))
        physical_provider = provider not in {"", "marketplace"}
        purchased = bool(
            physical_provider
            and (
                getattr(shipment, "label_purchased_at", None) is not None
                or purchase_status == "purchased"
            )
        )
        exact_tracking_match = bool(order_tracking and tracking and order_tracking == tracking)

        # Original outbound identity is the first boundary.  Inside that set,
        # paid physical authority must beat a later marketplace proxy carrying
        # the same tracking number.  This is the key correction from the legacy
        # selector, which ranked exact MarketplaceOrder tracking first.
        return (
            1 if not additional else 0,
            1 if purchased else 0,
            1 if exact_tracking_match else 0,
            1 if physical_provider else 0,
            1 if getattr(shipment, "label_purchased_at", None) is not None else 0,
            1 if purchase_status == "purchased" else 0,
            1 if getattr(shipment, "provider_shipment_id", None) else 0,
            1 if tracking else 0,
            int(getattr(shipment, "id", 0) or 0),
        )

    result: dict[tuple[int, str], FBMShipment] = {}
    for shipment in shipments:
        key = (int(shipment.store_id), str(shipment.marketplace_order_id))
        if key not in keys:
            continue
        current = result.get(key)
        if current is None or rank(shipment) > rank(current):
            result[key] = shipment
    return result


def install_governed_fbm_shipment_selection_alignment(app) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    import governed_fbm_routes as routes
    from services import governed_fbm_page_alignment as page_alignment

    routes._shipment_map = _aligned_shipment_map
    # governed_fbm_page_alignment imported the function by name, so update its
    # request-local binding too.  Health alignment dereferences this binding.
    page_alignment._shipment_map = _aligned_shipment_map

    _INSTALLED = True
    app.logger.info(
        "BT38 FBM shipment selector aligned: purchased physical outbound authority precedes marketplace tracking fallback"
    )
