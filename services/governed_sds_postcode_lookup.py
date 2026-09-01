"""Bounded postcode-coordinate lookup for SDS auto eligibility.

Postcodes.io is used only to resolve a UK postcode to coordinates. Successful
and unavailable results are cached per process; lookup failure never makes an
order eligible.
"""
from __future__ import annotations

from functools import lru_cache

import requests

from services.governed_seller_delivery_eligibility import normalise_postcode


POSTCODES_IO_URL = "https://api.postcodes.io/postcodes/{postcode}"
LOOKUP_TIMEOUT_SECONDS = 3


@lru_cache(maxsize=2048)
def lookup_postcode_coordinates(postcode):
    normalised = normalise_postcode(postcode)
    if not normalised:
        return None
    try:
        response = requests.get(
            POSTCODES_IO_URL.format(postcode=normalised.replace(" ", "%20")),
            timeout=LOOKUP_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return None
        payload = response.json() if response.content else {}
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            return None
        latitude = result.get("latitude")
        longitude = result.get("longitude")
        if latitude is None or longitude is None:
            return None
        return float(latitude), float(longitude)
    except (requests.RequestException, TypeError, ValueError):
        return None
