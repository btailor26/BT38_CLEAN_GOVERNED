"""Attach automatic SDS eligibility to the existing FBM read path.

No shipment is created and no marketplace is written. The FBM page receives a
server-side result derived from persisted order + warehouse configuration.
"""
from __future__ import annotations

from seller_delivery_models import WarehouseSellerDeliveryConfig
from services.governed_sds_postcode_lookup import lookup_postcode_coordinates
from services.governed_seller_delivery_eligibility import evaluate_seller_delivery, normalise_postcode


def _warehouse_config_for_order(order):
    """Resolve one enabled SDS warehouse configuration.

    Until BT38 persists an order-to-warehouse identity, ambiguity is blocked:
    SDS is only evaluated when exactly one warehouse SDS configuration is enabled.
    """
    configs = WarehouseSellerDeliveryConfig.query.filter_by(enabled=True).all()
    return configs[0] if len(configs) == 1 else None


def sds_for_fbm_order(order, *, prime_sfp=False, coordinate_lookup=lookup_postcode_coordinates):
    config = _warehouse_config_for_order(order)
    if config is None:
        return {
            "service": "SDS",
            "eligible": False,
            "reason": "sds_warehouse_unresolved",
            "distance_miles": None,
        }

    origin = normalise_postcode(config.origin_postcode)
    destination = normalise_postcode(getattr(order, "ship_to_postcode", None))
    origin_coordinates = coordinate_lookup(origin) if origin else None
    destination_coordinates = coordinate_lookup(destination) if destination else None
    result = evaluate_seller_delivery(
        enabled=config.enabled,
        prime_sfp=prime_sfp,
        origin_postcode=origin,
        destination_postcode=destination,
        radius_miles=config.radius_miles,
        origin_coordinates=origin_coordinates,
        destination_coordinates=destination_coordinates,
    )
    return {
        "service": result.service,
        "eligible": result.eligible,
        "reason": result.reason,
        "distance_miles": float(result.distance_miles) if result.distance_miles is not None else None,
        "radius_miles": float(config.radius_miles) if config.radius_miles is not None else None,
        "warehouse_id": config.warehouse_id,
    }
