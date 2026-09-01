"""Deterministic Seller Delivery eligibility scanning.

This module only evaluates persisted order/warehouse data. It does not create
shipments, write marketplaces, or manufacture location/tracking information.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import asin, cos, radians, sin, sqrt
import re


POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I)


@dataclass(frozen=True)
class SellerDeliveryEligibility:
    eligible: bool
    reason: str
    distance_miles: Decimal | None = None


def normalise_postcode(value) -> str | None:
    compact = re.sub(r"\s+", "", str(value or "").strip().upper())
    if not compact or not POSTCODE_RE.match(compact):
        return None
    return f"{compact[:-3]} {compact[-3:]}"


def distance_miles(origin_lat, origin_lng, destination_lat, destination_lng) -> Decimal:
    """Great-circle distance for already-resolved postcode coordinates."""
    lat1, lon1, lat2, lon2 = map(float, (origin_lat, origin_lng, destination_lat, destination_lng))
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    miles = 3958.7613 * 2 * asin(sqrt(a))
    return Decimal(str(round(miles, 2)))


def evaluate_seller_delivery(*, enabled, prime_sfp, origin_postcode, destination_postcode,
                             radius_miles, origin_coordinates=None, destination_coordinates=None):
    """Return one explicit eligibility result; uncertainty is never eligible."""
    if not enabled:
        return SellerDeliveryEligibility(False, "seller_delivery_disabled")
    if prime_sfp:
        return SellerDeliveryEligibility(False, "prime_sfp_blocked")
    if normalise_postcode(origin_postcode) is None:
        return SellerDeliveryEligibility(False, "origin_postcode_invalid")
    if normalise_postcode(destination_postcode) is None:
        return SellerDeliveryEligibility(False, "destination_postcode_invalid")
    try:
        radius = Decimal(str(radius_miles))
    except Exception:
        return SellerDeliveryEligibility(False, "radius_invalid")
    if radius <= 0:
        return SellerDeliveryEligibility(False, "radius_invalid")
    if not origin_coordinates or not destination_coordinates:
        return SellerDeliveryEligibility(False, "postcode_coordinates_unavailable")
    try:
        distance = distance_miles(*origin_coordinates, *destination_coordinates)
    except (TypeError, ValueError):
        return SellerDeliveryEligibility(False, "postcode_coordinates_invalid")
    if distance > radius:
        return SellerDeliveryEligibility(False, "outside_delivery_radius", distance)
    return SellerDeliveryEligibility(True, "within_delivery_radius", distance)


def scan_orders(orders, *, config, coordinate_lookup, prime_sfp_resolver, postcode_resolver):
    """Auto-scan every supplied FBM order and return deterministic results.

    The caller supplies persisted-data resolvers. No browser location, carrier
    guessing, marketplace write, or silent fallback is allowed here.
    """
    origin_postcode = normalise_postcode(getattr(config, "origin_postcode", None))
    origin_coordinates = coordinate_lookup(origin_postcode) if origin_postcode else None
    results = []
    for order in orders:
        destination_postcode = normalise_postcode(postcode_resolver(order))
        destination_coordinates = coordinate_lookup(destination_postcode) if destination_postcode else None
        result = evaluate_seller_delivery(
            enabled=bool(getattr(config, "enabled", False)),
            prime_sfp=bool(prime_sfp_resolver(order)),
            origin_postcode=origin_postcode,
            destination_postcode=destination_postcode,
            radius_miles=getattr(config, "radius_miles", None),
            origin_coordinates=origin_coordinates,
            destination_coordinates=destination_coordinates,
        )
        results.append((order, result))
    return results
