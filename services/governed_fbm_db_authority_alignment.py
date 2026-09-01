"""Keep the FBM page on one persisted physical-shipment authority.

This is a read-path alignment only. Marketplace/provider reads persist first; the
FBM page then resolves exactly one FBMShipment from the database. It does not
create shipments, call providers, write marketplaces, or synthesize marketplace
proxy shipments.
"""
from __future__ import annotations

from sqlalchemy import tuple_

from extensions import db
from fbm_models import FBMShipment


def _normal(value) -> str:
    return str(value or "").strip()


def _tracking(value) -> str:
    return _normal(value).upper()


def _canonical_rank(shipment: FBMShipment, persisted_tracking: str) -> tuple[int, ...]:
    provider = _normal(getattr(shipment, "provider", None)).lower()
    purchase_key = _normal(getattr(shipment, "purchase_key", None)).lower()
    purchase_status = _normal(getattr(shipment, "purchase_status", None)).lower()
    shipment_tracking = _tracking(getattr(shipment, "tracking_number", None))

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

    return (
        1 if exact_tracking_match else 0,
        1 if not additional_shipment else 0,
        1 if physical_provider else 0,
        1 if getattr(shipment, "label_purchased_at", None) is not None else 0,
        1 if purchase_status == "purchased" else 0,
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
    """Replace only the FBM page shipment selector with DB-persisted authority."""
    import services.governed_fbm_page_alignment as page

    if getattr(page, "_bt38_single_db_shipment_authority_installed", False):
        return

    page._shipment_map = _canonical_persisted_shipment_map
    page._bt38_single_db_shipment_authority_installed = True
