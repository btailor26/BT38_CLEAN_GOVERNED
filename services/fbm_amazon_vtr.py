"""Strict Amazon UK VTR carrier resolution for external FBM postage.

Packlink is only an external postage provider. Amazon VTR depends on the real
carrier/tracking identity that Amazon recognises, not on a user-defined Packlink
mapping. Unknown carriers are therefore blocked rather than coerced to `Other`.
"""
from __future__ import annotations

from typing import Any


# Canonical Amazon UK carrier names. Keep this deliberately strict: when
# Packlink returns an unknown carrier BT38 must hold the Amazon confirmation
# rather than inventing a carrier identity that could damage VTR.
_AMAZON_UK_CARRIERS = {
    "amazon shipping": "Amazon Shipping",
    "apc": "APC",
    "arrow xl": "Arrow XL",
    "bjs": "BJS",
    "dhl parcel uk": "DHL Parcel UK",
    "dpd": "DPD",
    "dx freight": "DX FREIGHT",
    "evri": "Evri",
    "fedex": "Fedex",
    "gls": "GLS",
    "mhi": "MHI",
    "panther": "Panther",
    "parcelforce": "Parcelforce",
    "royal mail": "Royal Mail",
    "the delivery group (tdg)": "The Delivery Group (TDG)",
    "tnt": "TNT",
    "tuffnells": "TUFFNELLS",
    "ups": "UPS",
    "whistl": "Whistl",
    "yodel": "Yodel",
}

# Packlink/provider display aliases -> Amazon's canonical carrier identity.
# These aliases do not create a persistent marketplace mapping and cannot be
# edited by an operator from the FBM screen.
_PACKLINK_ALIASES = {
    "apc overnight": "APC",
    "dhl": "DHL Parcel UK",
    "dhl parcel": "DHL Parcel UK",
    "dpd uk": "DPD",
    "dx": "DX FREIGHT",
    "fedex uk": "Fedex",
    "hermes": "Evri",
    "hermes uk": "Evri",
    "parcel force": "Parcelforce",
    "parcelforce worldwide": "Parcelforce",
    "royalmail": "Royal Mail",
    "tdg": "The Delivery Group (TDG)",
    "the delivery group": "The Delivery Group (TDG)",
    "ups uk": "UPS",
    "yodel direct": "Yodel",
}


class AmazonVTRCarrierError(ValueError):
    pass


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def resolve_amazon_uk_carrier(provider_carrier: Any) -> str:
    """Return Amazon's canonical UK carrier name or fail closed."""
    normalized = _norm(provider_carrier)
    if not normalized:
        raise AmazonVTRCarrierError("Packlink did not return a carrier name.")
    if normalized in _AMAZON_UK_CARRIERS:
        return _AMAZON_UK_CARRIERS[normalized]
    if normalized in _PACKLINK_ALIASES:
        return _PACKLINK_ALIASES[normalized]
    raise AmazonVTRCarrierError(
        f"Packlink carrier '{str(provider_carrier).strip()}' is not approved for the Amazon UK VTR-safe route."
    )


def amazon_vtr_safe_rate(rate: dict[str, Any]) -> bool:
    """True only when a Packlink rate resolves to a known Amazon UK carrier."""
    carrier = rate.get("carrier_name") or rate.get("carrier")
    try:
        resolve_amazon_uk_carrier(carrier)
    except AmazonVTRCarrierError:
        return False
    return True


def validate_amazon_tracking(carrier: str, tracking_number: Any) -> str:
    """Return normalized tracking or fail closed on known VTR-dangerous forms."""
    tracking = "".join(str(tracking_number or "").strip().split())
    if not tracking:
        raise AmazonVTRCarrierError("Packlink tracking number is missing.")

    # Yodel exposes an internal YOD* reference as well as the scannable parcel
    # barcode on some flows. Amazon UK recognises the scannable parcel tracking
    # identifier; do not send a YOD* internal reference into confirmShipment.
    if carrier == "Yodel" and tracking.upper().startswith("YOD"):
        raise AmazonVTRCarrierError(
            "Packlink returned a Yodel internal YOD reference, not the scannable parcel tracking number required for Amazon VTR."
        )
    return tracking


def amazon_shipping_method(provider_service: Any, carrier: str) -> str:
    service = " ".join(str(provider_service or "").strip().split())
    return service or "Other"
