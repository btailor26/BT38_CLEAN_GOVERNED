"""Keep every FBM read path on one persisted physical-shipment authority.

Marketplace/provider reads persist first; FBM then resolves exactly one
FBMShipment from the database. This alignment does not create shipments, call
providers, write marketplaces, or synthesize marketplace proxy shipments.
"""
from __future__ import annotations

from sqlalchemy import tuple_

from extensions import db
from fbm_models import FBMShipment


_MARKETPLACE_DISPATCH_STATES = {
    "shipped",
    "dispatched",
    "partially_shipped",
    "partiallydispatched",
    "fulfilled",
    "completed",
    "accepted",
    "carrier_accepted",
    "collected",
    "picked_up",
    "in_transit",
    "out_for_delivery",
    "delivered",
}


def _normal(value) -> str:
    return str(value or "").strip()


def _tracking(value) -> str:
    return _normal(value).upper()


def _status(value) -> str:
    return _normal(value).lower().replace("-", "_").replace(" ", "_")


def _canonical_rank(shipment: FBMShipment, persisted_tracking: str) -> tuple[int, ...]:
    provider = _normal(getattr(shipment, "provider", None)).lower()
    purchase_key = _normal(getattr(shipment, "purchase_key", None)).lower()
    purchase_status = _normal(getattr(shipment, "purchase_status", None)).lower()
    shipment_tracking = _tracking(getattr(shipment, "tracking_number", None))
    shipment_status = _status(getattr(shipment, "status", None))

    exact_tracking_match = bool(
        persisted_tracking
        and shipment_tracking
        and shipment_tracking == persisted_tracking
    )
    additional_shipment = purchase_key.startswith((
        "packlink_return:",
        "packlink_replacement:",
    ))
    physical_provider = provider not in {"", "marketplace"}
    purchased_provider = bool(
        physical_provider
        and (
            getattr(shipment, "label_purchased_at", None) is not None
            or purchase_status == "purchased"
        )
    )
    marketplace_dispatch = bool(
        provider == "marketplace"
        and shipment_status in _MARKETPLACE_DISPATCH_STATES
    )

    # The actual purchased label/provider is the physical shipment authority.
    # Buyer-selected marketplace postage and later marketplace proxy tracking
    # must never replace that purchase. Original outbound identity is settled
    # before persisted tracking is used as a tie-breaker, so return/replacement
    # labels cannot silently become the order's main journey.
    return (
        1 if purchased_provider else 0,
        1 if not additional_shipment else 0,
        1 if exact_tracking_match else 0,
        1 if marketplace_dispatch else 0,
        1 if physical_provider else 0,
        1 if getattr(shipment, "provider_shipment_id", None) else 0,
        1 if shipment_tracking else 0,
    )


def _canonical_persisted_shipment_map(rows):
    """Return one DB-persisted FBMShipment per exact store/order identity."""
    identities = sorted({
        (int(row.store_id), str(row.marketplace_order_id))
        for row in rows
        if row.store_id is not None and row.marketplace_order_id
    })
    if not identities:
        return {}

    tracking_by_key: dict[tuple[int, str], str] = {}
    for row in rows:
        if row.store_id is None or not row.marketplace_order_id:
            continue
        key = (int(row.store_id), str(row.marketplace_order_id))
        candidate = _tracking(getattr(row, "tracking_number", None))
        if candidate and key not in tracking_by_key:
            tracking_by_key[key] = candidate

    shipments = (
        db.session.query(FBMShipment)
        .filter(tuple_(FBMShipment.store_id, FBMShipment.marketplace_order_id).in_(identities))
        .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
        .all()
    )

    result = {}
    identity_set = set(identities)
    for shipment in shipments:
        key = (int(shipment.store_id), str(shipment.marketplace_order_id))
        if key not in identity_set:
            continue
        current = result.get(key)
        persisted_tracking = tracking_by_key.get(key, "")
        if current is None or _canonical_rank(shipment, persisted_tracking) > _canonical_rank(current, persisted_tracking):
            result[key] = shipment
    return result


def install_governed_fbm_db_authority_alignment() -> None:
    """Install one persisted shipment authority for every existing FBM consumer."""
    import governed_fbm_routes as routes
    import services.governed_fbm_page_alignment as page
    import services.governed_fbm_global_search_alignment as global_search
    import services.governed_fbm_dispatch_queue_alignment as dispatch_queue

    # The original blueprint helper historically ranked marketplace tracking
    # before purchased physical authority. Keep the blueprint/page/stats/Cofi
    # paths on this same canonical resolver so no consumer can disagree about
    # the physical shipment merely because it imported a different helper.
    routes._shipment_map = _canonical_persisted_shipment_map

    if not getattr(page, "_bt38_single_db_shipment_authority_installed", False):
        page._shipment_map = _canonical_persisted_shipment_map
        page._bt38_single_db_shipment_authority_installed = True

    global_search._shipment_map = _canonical_persisted_shipment_map
    dispatch_queue._shipment_map = _canonical_persisted_shipment_map

    # This installer is already the single startup hook for the canonical FBM
    # DB authority. Attach the spend ledger here rather than creating another
    # startup/deployment path.
    from app import app as flask_app
    from services.governed_shipping_spend_alignment import (
        install_governed_shipping_spend_alignment,
    )

    install_governed_shipping_spend_alignment(flask_app)
