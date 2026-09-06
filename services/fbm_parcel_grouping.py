"""DB-only FBM packing/grouping decisions.

This module learns exact packed parcel combinations and detects when separate
unshipped marketplace orders have the same persisted delivery address. It never
calls a marketplace/provider and never silently combines orders. The user must
explicitly choose to pack orders together before one physical shipment can be
linked to them.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from extensions import db
from fbm_parcel_models import FBMParcelCombinationMapping, FBMShipmentOrderLink
from models import MarketplaceOrder, WarehouseStock
from services.fbm_order_mapper import order_lines


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _quantity(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def marketplace_order_identity(order: Any) -> tuple[int, str] | None:
    store_id = getattr(order, "store_id", None)
    order_id = _text(getattr(order, "marketplace_order_id", None))
    if store_id is None or not order_id:
        return None
    return int(store_id), order_id


def canonical_order_rows(rows: Iterable[Any]) -> list[Any]:
    """Keep one representative row per persisted marketplace order identity."""
    result: dict[tuple[int, str], Any] = {}
    for row in rows:
        identity = marketplace_order_identity(row)
        if identity is None:
            continue
        current = result.get(identity)
        if current is None or int(getattr(row, "id", 0) or 0) > int(getattr(current, "id", 0) or 0):
            result[identity] = row
    return list(result.values())


def canonical_items(orders: Iterable[Any]) -> list[dict[str, Any]]:
    """Return a deterministic SKU/quantity composition across selected orders."""
    quantities: dict[str, int] = defaultdict(int)
    for order in canonical_order_rows(orders):
        for line in order_lines(order):
            sku = _text(getattr(line, "sku", None))
            if not sku:
                continue
            quantities[sku] += _quantity(getattr(line, "quantity", 1))
    return [
        {"sku": sku, "quantity": quantities[sku]}
        for sku in sorted(quantities, key=lambda value: value.casefold())
    ]


def combination_key(orders: Iterable[Any]) -> str | None:
    items = canonical_items(orders)
    if not items:
        return None
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def persisted_address_key(order: Any) -> tuple[str, str, str, str, str] | None:
    """Conservative same-recipient identity using persisted delivery facts only."""
    name = _text(getattr(order, "ship_to_name", None)).casefold()
    address = _text(getattr(order, "ship_to_address", None)).casefold()
    city = _text(getattr(order, "ship_to_city", None)).casefold()
    postcode = _text(getattr(order, "ship_to_postcode", None)).replace(" ", "").upper()
    country = _text(getattr(order, "ship_to_country", None) or "GB").upper()
    if not all((name, address, postcode, country)):
        return None
    return name, address, city, postcode, country


def same_persisted_address(orders: Iterable[Any]) -> bool:
    keys = [persisted_address_key(order) for order in canonical_order_rows(orders)]
    return bool(keys) and None not in keys and len(set(keys)) == 1


def _order_is_unshipped(order: Any) -> bool:
    status = _text(getattr(order, "status", None)).casefold()
    fulfillment = _text(getattr(order, "fulfillment_type", None)).upper()
    if fulfillment in {"FBA", "AFN", "MCF"} or status.startswith("mcf_"):
        return False
    if getattr(order, "shipped_at", None) is not None:
        return False
    if _text(getattr(order, "tracking_number", None)):
        return False
    return True


def _is_prime(order: Any) -> bool:
    try:
        from fbm_models import FBMOrderProfile
        profile = FBMOrderProfile.query.filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
        ).first()
        return bool(profile and profile.is_prime is True)
    except Exception:
        return False


def consolidation_eligibility(orders: Iterable[Any]) -> dict[str, Any]:
    """Fail closed when one-box consolidation is not safely established."""
    rows = canonical_order_rows(orders)
    blockers: list[str] = []
    if len(rows) < 2:
        blockers.append("select_at_least_two_orders")
    if any(not _order_is_unshipped(row) for row in rows):
        blockers.append("order_already_dispatched")
    if not same_persisted_address(rows):
        blockers.append("delivery_address_not_identical")
    if any(_is_prime(row) for row in rows):
        blockers.append("prime_sfp_cannot_share_external_parcel")

    platforms = {
        _text(getattr(getattr(row, "store", None), "platform", None)).casefold()
        for row in rows
    }
    platforms.discard("")
    if len(platforms) > 1:
        blockers.append("mixed_marketplaces_require_separate_shipments")
    amazon_buy_shipping_compatible = not any("amazon" in platform for platform in platforms)

    return {
        "eligible": not blockers,
        "blockers": blockers,
        "order_count": len(rows),
        "same_address": same_persisted_address(rows),
        "marketplaces": sorted(platforms),
        "amazon_buy_shipping_compatible": amazon_buy_shipping_compatible,
        "requires_user_confirmation": True,
    }


def _warehouse_weight(order: Any) -> tuple[float | None, bool]:
    """Return additive DB weight for one marketplace order without provider calls."""
    total = 0.0
    known = True
    for line in order_lines(order):
        sku = _text(getattr(line, "sku", None))
        stock = getattr(line, "warehouse_stock", None)
        if stock is None and sku:
            stock = WarehouseStock.query.filter_by(sku=sku).first()
        unit_weight = _positive_float(getattr(stock, "product_weight_kg", None)) if stock is not None else None
        if unit_weight is None:
            known = False
            continue
        total += unit_weight * _quantity(getattr(line, "quantity", 1))
    return (total if known and total > 0 else None), known


def resolve_combined_parcel(orders: Iterable[Any], *, record_usage: bool = False) -> dict[str, Any]:
    """Resolve a selected one-box parcel from persisted DB/mapping knowledge.

    Ordinary reads are side-effect free. Usage counters move only when a caller
    explicitly marks the mapping as final-use evidence.
    """
    rows = canonical_order_rows(orders)
    key = combination_key(rows)
    items = canonical_items(rows)
    mapping = None
    if key:
        mapping = FBMParcelCombinationMapping.query.filter_by(
            combination_key=key,
            verification_status="verified",
        ).first()

    weight_total = 0.0
    weight_known = True
    for row in rows:
        weight, known = _warehouse_weight(row)
        if not known or weight is None:
            weight_known = False
        else:
            weight_total += weight

    calculated_weight = weight_total if weight_known and weight_total > 0 else None
    if mapping is not None and mapping.complete:
        if record_usage:
            mapping.usage_count = int(mapping.usage_count or 0) + 1
            mapping.last_used_at = datetime.utcnow()
            db.session.commit()
        return {
            "combination_key": key,
            "items": items,
            "total_units": sum(item["quantity"] for item in items),
            "weight_kg": _positive_float(mapping.weight_kg) or calculated_weight,
            "length_cm": _positive_float(mapping.length_cm),
            "width_cm": _positive_float(mapping.width_cm),
            "height_cm": _positive_float(mapping.height_cm),
            "source": "verified_combination_mapping",
            "mapping_review_required": False,
            "mapping_id": mapping.id,
        }

    return {
        "combination_key": key,
        "items": items,
        "total_units": sum(item["quantity"] for item in items),
        "weight_kg": calculated_weight,
        "length_cm": None,
        "width_cm": None,
        "height_cm": None,
        "source": "combination_mapping_review_required",
        "mapping_review_required": True,
        "mapping_id": None,
    }


def save_combination_mapping(
    orders: Iterable[Any],
    *,
    weight_kg: Any,
    length_cm: Any,
    width_cm: Any,
    height_cm: Any,
    verified_by: str | None = None,
) -> FBMParcelCombinationMapping:
    rows = canonical_order_rows(orders)
    key = combination_key(rows)
    items = canonical_items(rows)
    values = {
        "weight_kg": _positive_float(weight_kg),
        "length_cm": _positive_float(length_cm),
        "width_cm": _positive_float(width_cm),
        "height_cm": _positive_float(height_cm),
    }
    if not key or not items:
        raise ValueError("Packing combination has no SKU/quantity identity.")
    if not all(values.values()):
        raise ValueError("Confirmed packed weight and all dimensions are required.")

    mapping = FBMParcelCombinationMapping.query.filter_by(combination_key=key).first()
    if mapping is None:
        mapping = FBMParcelCombinationMapping(combination_key=key)
        db.session.add(mapping)
    mapping.items = items
    mapping.total_units = sum(item["quantity"] for item in items)
    mapping.weight_kg = values["weight_kg"]
    mapping.length_cm = values["length_cm"]
    mapping.width_cm = values["width_cm"]
    mapping.height_cm = values["height_cm"]
    mapping.verification_status = "verified"
    mapping.verified_at = datetime.utcnow()
    mapping.verified_by = _text(verified_by) or None
    mapping.source = "fbm_mapping_review"
    db.session.commit()
    return mapping


def same_address_candidates(selected_rows: Iterable[Any], *, limit: int = 100) -> dict[tuple[int, str], list[Any]]:
    """Find candidate unshipped orders with one bounded DB query.

    Postcodes provide the SQL bound; exact recipient/address comparison happens
    in Python against persisted values. No marketplace/provider reads occur.
    """
    selected = canonical_order_rows(selected_rows)
    postcodes = sorted({
        _text(getattr(row, "ship_to_postcode", None))
        for row in selected
        if _text(getattr(row, "ship_to_postcode", None))
    })
    if not postcodes:
        return {}

    candidates = (
        MarketplaceOrder.query
        .filter(MarketplaceOrder.ship_to_postcode.in_(postcodes))
        .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
        .limit(max(1, min(int(limit or 100), 250)))
        .all()
    )
    candidates = [row for row in canonical_order_rows(candidates) if _order_is_unshipped(row)]
    result: dict[tuple[int, str], list[Any]] = {}
    for row in selected:
        identity = marketplace_order_identity(row)
        key = persisted_address_key(row)
        if identity is None or key is None:
            continue
        matches = [
            candidate
            for candidate in candidates
            if marketplace_order_identity(candidate) != identity
            and persisted_address_key(candidate) == key
        ]
        if matches:
            result[identity] = matches
    return result


def linked_physical_shipment_for_order(order: Any, *, exclude_shipment_id: int | None = None) -> FBMShipmentOrderLink | None:
    """Return an existing shared-parcel authority for this marketplace order.

    One marketplace order may not silently belong to two different original
    physical parcels. Return/replacement labels remain separate governed flows.
    """
    identity = marketplace_order_identity(order)
    if identity is None:
        return None
    query = FBMShipmentOrderLink.query.filter_by(
        store_id=identity[0],
        marketplace_order_id=identity[1],
    )
    if exclude_shipment_id is not None:
        query = query.filter(FBMShipmentOrderLink.shipment_id != int(exclude_shipment_id))
    return query.order_by(FBMShipmentOrderLink.id.desc()).first()


def link_orders_to_existing_shipment(shipment: Any, orders: Iterable[Any]) -> list[FBMShipmentOrderLink]:
    """Persist explicit user-approved order membership for one physical shipment."""
    rows = canonical_order_rows(orders)
    eligibility = consolidation_eligibility(rows)
    if not eligibility["eligible"]:
        raise ValueError("Orders are not eligible for one-box consolidation: " + ", ".join(eligibility["blockers"]))
    if shipment is None or getattr(shipment, "id", None) is None:
        raise ValueError("Existing physical shipment is required.")

    primary = (int(shipment.store_id), _text(shipment.marketplace_order_id))
    for row in rows:
        existing_other = linked_physical_shipment_for_order(row, exclude_shipment_id=shipment.id)
        if existing_other is not None:
            raise ValueError(
                f"Marketplace order {row.marketplace_order_id} is already linked to physical shipment {existing_other.shipment_id}."
            )

    links: list[FBMShipmentOrderLink] = []
    for row in rows:
        identity = marketplace_order_identity(row)
        if identity is None:
            continue
        link = FBMShipmentOrderLink.query.filter_by(
            shipment_id=shipment.id,
            store_id=identity[0],
            marketplace_order_id=identity[1],
        ).first()
        if link is None:
            link = FBMShipmentOrderLink(
                shipment_id=shipment.id,
                store_id=identity[0],
                marketplace_order_id=identity[1],
            )
            db.session.add(link)
        link.is_primary = identity == primary
        links.append(link)
    db.session.commit()
    return links
