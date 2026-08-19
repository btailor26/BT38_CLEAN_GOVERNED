"""Map existing BT38 DB orders into provider-neutral FBM shipment input.

MarketplaceOrder remains the order source of truth. This module does not import
orders, call marketplaces, buy postage, or mutate inventory quantities. Missing
marketplace-owned delivery facts may be hydrated through the single exact-order
hydration rule before an external provider is called. Explicit single-unit
parcel facts may be persisted into ProductPackMapping so the same SKU can reuse
those FBM shipping defaults on later orders.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from extensions import db
from models import MarketplaceOrder, ProductPackMapping, WarehouseStock
from services.fbm_marketplace_destination import destination_complete, hydrate_marketplace_destination


DEFAULT_SHIP_FROM = {
    "name": "Bhavin Tailor",
    "company": "B & T OUTLET LTD",
    "address1": "Unit 10, St Mark's Works Foundry Lane",
    "address2": "",
    "city": "Leicester",
    "region": "Leicestershire",
    "postcode": "LE1 3WU",
    "country": "GB",
    "email": "weeklydeals2014@outlook.com",
    "phone": "07903883892",
}


@dataclass(frozen=True)
class ParcelInput:
    weight_kg: float | None
    length_cm: float | None
    width_cm: float | None
    height_cm: float | None
    source: str
    order_ref: Any = field(default=None, repr=False, compare=False)

    @property
    def complete(self) -> bool:
        return all(value is not None and float(value) > 0 for value in (self.weight_kg, self.length_cm, self.width_cm, self.height_cm))

    def to_dict(self) -> dict[str, Any]:
        return {"weight_kg": self.weight_kg, "length_cm": self.length_cm, "width_cm": self.width_cm, "height_cm": self.height_cm, "source": self.source, "complete": self.complete}


def order_lines(order: Any) -> list[MarketplaceOrder]:
    """Return all committed DB lines that belong to the same marketplace order."""
    store_id = getattr(order, "store_id", None)
    marketplace_order_id = _text(getattr(order, "marketplace_order_id", None))
    if store_id is None or not marketplace_order_id:
        return [order]
    rows = (
        MarketplaceOrder.query
        .filter_by(store_id=store_id, marketplace_order_id=marketplace_order_id)
        .order_by(MarketplaceOrder.id.asc())
        .all()
    )
    return rows or [order]


def ship_from() -> dict[str, str]:
    """Return the configured BT38 dispatch address without exposing secrets."""
    return {
        "name": (os.getenv("FBM_SHIP_FROM_NAME") or DEFAULT_SHIP_FROM["name"]).strip(),
        "company": (os.getenv("FBM_SHIP_FROM_COMPANY") or DEFAULT_SHIP_FROM["company"]).strip(),
        "address1": (os.getenv("FBM_SHIP_FROM_ADDRESS1") or DEFAULT_SHIP_FROM["address1"]).strip(),
        "address2": (os.getenv("FBM_SHIP_FROM_ADDRESS2") or DEFAULT_SHIP_FROM["address2"]).strip(),
        "city": (os.getenv("FBM_SHIP_FROM_CITY") or DEFAULT_SHIP_FROM["city"]).strip(),
        "region": (os.getenv("FBM_SHIP_FROM_REGION") or DEFAULT_SHIP_FROM["region"]).strip(),
        "postcode": (os.getenv("FBM_SHIP_FROM_POSTCODE") or DEFAULT_SHIP_FROM["postcode"]).strip(),
        "country": (os.getenv("FBM_SHIP_FROM_COUNTRY") or DEFAULT_SHIP_FROM["country"]).strip().upper(),
        "email": (os.getenv("FBM_SHIP_FROM_EMAIL") or DEFAULT_SHIP_FROM["email"]).strip(),
        "phone": (os.getenv("FBM_SHIP_FROM_PHONE") or DEFAULT_SHIP_FROM["phone"]).strip(),
    }


def ship_to(order: Any) -> dict[str, str | None]:
    """Return marketplace-owned delivery facts, hydrating the exact order if needed.

    Amazon and eBay currently have exact-order readers. Any future marketplace
    follows this same contract by adding its exact reader to the central
    hydration service; BT38 never substitutes or invents an address.
    """
    if order is not None and not destination_complete(order):
        hydrate_marketplace_destination(order)
    return {
        "name": _text(getattr(order, "ship_to_name", None)),
        "address1": _text(getattr(order, "ship_to_address", None)),
        "address2": None,
        "city": _text(getattr(order, "ship_to_city", None)),
        "region": None,
        "postcode": _text(getattr(order, "ship_to_postcode", None)),
        "country": (_text(getattr(order, "ship_to_country", None)) or "GB").upper(),
        "email": _text(getattr(order, "ship_to_email", None)),
        "phone": _text(getattr(order, "ship_to_phone", None)),
    }


def _single_unit_mapping(order: Any, *, create: bool = False) -> ProductPackMapping | None:
    """Resolve the existing reusable parcel mapping for an exact one-unit SKU.

    Multi-line or multi-quantity parcels are deliberately not reusable SKU
    defaults because their packed dimensions can differ from a single unit.
    """
    lines = order_lines(order)
    if len(lines) != 1:
        return None
    line = lines[0]
    try:
        quantity = max(1, int(getattr(line, "quantity", 1) or 1))
    except (TypeError, ValueError):
        quantity = 1
    if quantity != 1:
        return None
    sku = _text(getattr(line, "sku", None))
    if not sku:
        return None

    mapping = (
        ProductPackMapping.query
        .filter_by(single_sku=sku, is_active=True)
        .order_by(ProductPackMapping.updated_at.desc(), ProductPackMapping.id.desc())
        .first()
    )
    if mapping is not None:
        units = getattr(mapping, "units_per_carton", None)
        try:
            units = int(units) if units is not None else 1
        except (TypeError, ValueError):
            units = 1
        if units != 1:
            return None
        return mapping

    if not create:
        return None

    mapping = ProductPackMapping(
        single_sku=sku,
        units_per_carton=1,
        is_active=True,
        notes="FBM shipping parcel defaults",
    )
    db.session.add(mapping)
    return mapping


def _remember_explicit_parcel_defaults(base: ParcelInput, overrides: dict[str, Any]) -> None:
    """Persist explicit safe single-unit parcel values before provider calls.

    Weight and dimensions entered in the FBM desk are committed to the reusable
    ProductPackMapping before Packlink/Amazon rate execution. A later provider
    error therefore does not discard the entered parcel defaults.
    """
    order = base.order_ref
    if order is None:
        return

    values = {
        "carton_weight_kg": _positive_float(overrides.get("weight_kg")),
        "carton_length_cm": _positive_float(overrides.get("length_cm")),
        "carton_width_cm": _positive_float(overrides.get("width_cm")),
        "carton_height_cm": _positive_float(overrides.get("height_cm")),
    }
    values = {key: value for key, value in values.items() if value is not None}
    if not values:
        return

    try:
        mapping = _single_unit_mapping(order, create=True)
        if mapping is None:
            return
        changed = False
        for field_name, value in values.items():
            current = _positive_float(getattr(mapping, field_name, None))
            if current != value:
                setattr(mapping, field_name, value)
                changed = True
        if changed:
            db.session.commit()
    except Exception:
        db.session.rollback()


def parcel_from_db(order: Any) -> ParcelInput:
    """Resolve package defaults from the complete marketplace order.

    Weight normally comes from WarehouseStock.product_weight_kg x line
    quantity. For an exact one-unit SKU, an active ProductPackMapping may
    provide the previously entered FBM parcel weight and dimensions. For
    mixed/multi-line orders BT38 deliberately leaves dimensions blank because
    adding product carton dimensions would be unsafe; the user must enter the
    actual packed parcel dimensions.
    """
    lines = order_lines(order)
    total_weight = 0.0
    all_weights_known = True
    for line in lines:
        sku = _text(getattr(line, "sku", None))
        warehouse = getattr(line, "warehouse_stock", None)
        if warehouse is None and sku:
            warehouse = WarehouseStock.query.filter_by(sku=sku).first()
        unit_weight = _positive_float(getattr(warehouse, "product_weight_kg", None)) if warehouse is not None else None
        if not unit_weight:
            all_weights_known = False
            continue
        quantity = max(1, int(getattr(line, "quantity", 1) or 1))
        total_weight += unit_weight * quantity

    weight = total_weight if all_weights_known and total_weight > 0 else None
    length = width = height = None
    sources = []
    if weight:
        sources.append("warehouse_order_weight")

    if len(lines) == 1:
        sku = _text(getattr(lines[0], "sku", None))
        mapping = None
        if sku:
            mapping = (
                ProductPackMapping.query
                .filter_by(single_sku=sku, is_active=True)
                .order_by(ProductPackMapping.updated_at.desc(), ProductPackMapping.id.desc())
                .first()
            )
        if mapping:
            try:
                quantity = max(1, int(getattr(lines[0], "quantity", 1) or 1))
            except (TypeError, ValueError):
                quantity = 1
            units = getattr(mapping, "units_per_carton", None)
            try:
                units = int(units) if units is not None else 1
            except (TypeError, ValueError):
                units = 1
            if quantity == 1 and units == 1:
                mapped_weight = _positive_float(getattr(mapping, "carton_weight_kg", None))
                if mapped_weight:
                    weight = mapped_weight
                    sources = [source for source in sources if source != "warehouse_order_weight"]
                    sources.append("pack_mapping_weight")
            length = _positive_float(getattr(mapping, "carton_length_cm", None))
            width = _positive_float(getattr(mapping, "carton_width_cm", None))
            height = _positive_float(getattr(mapping, "carton_height_cm", None))
            if any((length, width, height)):
                sources.append("pack_mapping")
    else:
        sources.append("multi_item_dimensions_required")

    return ParcelInput(weight_kg=weight, length_cm=length, width_cm=width, height_cm=height, source="+".join(sources) if sources else "missing", order_ref=order)


def apply_parcel_overrides(base: ParcelInput, overrides: dict[str, Any] | None) -> ParcelInput:
    overrides = overrides or {}
    _remember_explicit_parcel_defaults(base, overrides)
    return ParcelInput(
        weight_kg=_positive_float(overrides.get("weight_kg")) or base.weight_kg,
        length_cm=_positive_float(overrides.get("length_cm")) or base.length_cm,
        width_cm=_positive_float(overrides.get("width_cm")) or base.width_cm,
        height_cm=_positive_float(overrides.get("height_cm")) or base.height_cm,
        source="ui_override" if any(overrides.get(k) not in (None, "") for k in ("weight_kg", "length_cm", "width_cm", "height_cm")) else base.source,
        order_ref=base.order_ref,
    )


def provider_parcel(order: Any, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base = parcel_from_db(order)
    parcel = apply_parcel_overrides(base, overrides)
    origin = ship_from()
    destination = ship_to(order)
    return {**parcel.to_dict(), "from_country": origin["country"], "from_zip": origin["postcode"], "to_country": destination["country"], "to_zip": destination["postcode"]}


def missing_rate_fields(order: Any, parcel: ParcelInput) -> list[str]:
    missing: list[str] = []
    destination = ship_to(order)
    if not destination.get("postcode"):
        missing.append("destination postcode")
    if not destination.get("country"):
        missing.append("destination country")
    if not parcel.weight_kg:
        missing.append("weight")
    if not parcel.length_cm:
        missing.append("length")
    if not parcel.width_cm:
        missing.append("width")
    if not parcel.height_cm:
        missing.append("height")
    return missing


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _positive_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None
