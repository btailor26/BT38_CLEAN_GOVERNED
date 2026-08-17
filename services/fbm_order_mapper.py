"""Map existing BT38 DB orders into provider-neutral FBM shipment input.

MarketplaceOrder remains the order source of truth. This module does not import
orders, call marketplaces, buy postage, or mutate inventory. It only resolves
committed order/address data plus warehouse parcel data already in BT38.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from models import ProductPackMapping, WarehouseStock


DEFAULT_SHIP_FROM = {
    "name": "B & T Outlet",
    "company": "B & T Outlet",
    "address1": "Unit 10 Foundry Lane",
    "city": "Leicester",
    "postcode": "LE1 3WU",
    "country": "GB",
    "email": "bandtoutlet@gmail.com",
    "phone": "07903883892",
}


@dataclass(frozen=True)
class ParcelInput:
    weight_kg: float | None
    length_cm: float | None
    width_cm: float | None
    height_cm: float | None
    source: str

    @property
    def complete(self) -> bool:
        return all(
            value is not None and float(value) > 0
            for value in (self.weight_kg, self.length_cm, self.width_cm, self.height_cm)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "weight_kg": self.weight_kg,
            "length_cm": self.length_cm,
            "width_cm": self.width_cm,
            "height_cm": self.height_cm,
            "source": self.source,
            "complete": self.complete,
        }


def ship_from() -> dict[str, str]:
    """Return the configured BT38 dispatch address without exposing secrets."""
    return {
        "name": (os.getenv("FBM_SHIP_FROM_NAME") or DEFAULT_SHIP_FROM["name"]).strip(),
        "company": (os.getenv("FBM_SHIP_FROM_COMPANY") or DEFAULT_SHIP_FROM["company"]).strip(),
        "address1": (os.getenv("FBM_SHIP_FROM_ADDRESS1") or DEFAULT_SHIP_FROM["address1"]).strip(),
        "city": (os.getenv("FBM_SHIP_FROM_CITY") or DEFAULT_SHIP_FROM["city"]).strip(),
        "postcode": (os.getenv("FBM_SHIP_FROM_POSTCODE") or DEFAULT_SHIP_FROM["postcode"]).strip(),
        "country": (os.getenv("FBM_SHIP_FROM_COUNTRY") or DEFAULT_SHIP_FROM["country"]).strip().upper(),
        "email": (os.getenv("FBM_SHIP_FROM_EMAIL") or DEFAULT_SHIP_FROM["email"]).strip(),
        "phone": (os.getenv("FBM_SHIP_FROM_PHONE") or DEFAULT_SHIP_FROM["phone"]).strip(),
    }


def ship_to(order: Any) -> dict[str, str | None]:
    return {
        "name": _text(getattr(order, "ship_to_name", None)),
        "address1": _text(getattr(order, "ship_to_address", None)),
        "city": _text(getattr(order, "ship_to_city", None)),
        "postcode": _text(getattr(order, "ship_to_postcode", None)),
        "country": (_text(getattr(order, "ship_to_country", None)) or "GB").upper(),
        "email": _text(getattr(order, "ship_to_email", None)),
        "phone": _text(getattr(order, "ship_to_phone", None)),
    }


def parcel_from_db(order: Any) -> ParcelInput:
    """Resolve parcel defaults from existing warehouse/pack data only.

    Weight uses WarehouseStock.product_weight_kg multiplied by order quantity.
    Dimensions use an active ProductPackMapping when one exists. Missing values
    remain missing so the UI can request them rather than inventing dimensions.
    """
    sku = _text(getattr(order, "sku", None))
    quantity = max(1, int(getattr(order, "quantity", 1) or 1))

    warehouse = getattr(order, "warehouse_stock", None)
    if warehouse is None and sku:
        warehouse = WarehouseStock.query.filter_by(sku=sku).first()

    unit_weight = _positive_float(getattr(warehouse, "product_weight_kg", None)) if warehouse is not None else None
    total_weight = unit_weight * quantity if unit_weight else None

    mapping = None
    if sku:
        mapping = (
            ProductPackMapping.query
            .filter_by(single_sku=sku, is_active=True)
            .order_by(ProductPackMapping.updated_at.desc(), ProductPackMapping.id.desc())
            .first()
        )

    length = _positive_float(getattr(mapping, "carton_length_cm", None)) if mapping else None
    width = _positive_float(getattr(mapping, "carton_width_cm", None)) if mapping else None
    height = _positive_float(getattr(mapping, "carton_height_cm", None)) if mapping else None

    sources = []
    if total_weight:
        sources.append("warehouse_weight")
    if any((length, width, height)):
        sources.append("pack_mapping")

    return ParcelInput(
        weight_kg=total_weight,
        length_cm=length,
        width_cm=width,
        height_cm=height,
        source="+".join(sources) if sources else "missing",
    )


def apply_parcel_overrides(base: ParcelInput, overrides: dict[str, Any] | None) -> ParcelInput:
    overrides = overrides or {}
    return ParcelInput(
        weight_kg=_positive_float(overrides.get("weight_kg")) or base.weight_kg,
        length_cm=_positive_float(overrides.get("length_cm")) or base.length_cm,
        width_cm=_positive_float(overrides.get("width_cm")) or base.width_cm,
        height_cm=_positive_float(overrides.get("height_cm")) or base.height_cm,
        source="ui_override" if any(overrides.get(k) not in (None, "") for k in ("weight_kg", "length_cm", "width_cm", "height_cm")) else base.source,
    )


def provider_parcel(order: Any, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    base = parcel_from_db(order)
    parcel = apply_parcel_overrides(base, overrides)
    origin = ship_from()
    destination = ship_to(order)
    return {
        **parcel.to_dict(),
        "from_country": origin["country"],
        "from_zip": origin["postcode"],
        "to_country": destination["country"],
        "to_zip": destination["postcode"],
    }


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
