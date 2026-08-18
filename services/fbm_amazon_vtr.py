"""Strict Amazon UK VTR carrier/service resolution for external FBM postage.

Packlink is only an external postage provider. Amazon VTR depends on the real
carrier/tracking identity and an appropriate delivery service. Unknown carrier
or service identities are therefore held rather than guessed.
"""
from __future__ import annotations

from typing import Any


_AMAZON_UK_CARRIERS = {
    "amazon shipping": "Amazon Shipping",
    "apc": "APC",
    "arrow xl": "Arrow XL",
    "bjs": "BJS",
    "dhl parcel uk": "DHL Parcel UK",
    "dpd": "DPD",
    "dx freight": "DX FREIGHT",
    # Amazon's own UK Shipping API examples still expose the legacy Hermes
    # identity for this exact carrier/service family.
    "evri": "Hermes UK",
    "hermes": "Hermes UK",
    "hermes uk": "Hermes UK",
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

_PACKLINK_ALIASES = {
    "apc overnight": "APC",
    "dhl": "DHL Parcel UK",
    "dhl parcel": "DHL Parcel UK",
    "dpd uk": "DPD",
    "dx": "DX FREIGHT",
    "fedex uk": "Fedex",
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
    """Return Amazon's canonical UK carrier display name or fail closed."""
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


def amazon_carrier_code(carrier_name: str) -> str:
    """Return the Amazon carrier code used for shipment confirmation."""
    if carrier_name == "Hermes UK":
        return "HERMES_UK"
    return carrier_name


def amazon_vtr_safe_rate(rate: dict[str, Any]) -> bool:
    """True only when a Packlink rate resolves to a proven Amazon UK identity."""
    carrier = rate.get("carrier_name") or rate.get("carrier")
    service = rate.get("service_name") or rate.get("service")
    try:
        canonical = resolve_amazon_uk_carrier(carrier)
        amazon_shipping_method(service, canonical)
    except AmazonVTRCarrierError:
        return False
    return True


def validate_amazon_tracking(carrier: str, tracking_number: Any) -> str:
    """Return normalized tracking or fail closed on known VTR-dangerous forms."""
    tracking = "".join(str(tracking_number or "").strip().split())
    if not tracking:
        raise AmazonVTRCarrierError("Packlink tracking number is missing.")

    if carrier == "Yodel" and tracking.upper().startswith("YOD"):
        raise AmazonVTRCarrierError(
            "Packlink returned a Yodel internal YOD reference, not the scannable parcel tracking number required for Amazon VTR."
        )
    if carrier == "Yodel":
        tracking = tracking.replace("-", "")
    return tracking


def amazon_shipping_method(provider_service: Any, carrier: str) -> str:
    """Resolve the Packlink product into Amazon's UK ship-confirm service."""
    service = " ".join(str(provider_service or "").strip().split())
    normalized = _norm(service)

    if carrier == "Hermes UK":
        if not normalized:
            raise AmazonVTRCarrierError(
                "Packlink did not return the Hermes/Evri service. BT38 will not guess the Amazon service."
            )
        two_day = any(token in normalized for token in ("2nd day", "2 day", "2-day", "two day", "two-day", "48"))
        drop_off = any(token in normalized for token in ("drop off", "drop-off", "dropoff", "parcelshop", "parcel shop"))
        if two_day and drop_off:
            return "Hermes Two Day - Drop Off"
        raise AmazonVTRCarrierError(
            f"Packlink Hermes/Evri service '{service}' is not the Amazon Hermes Two Day - Drop Off service; shipment confirmation is held for VTR safety."
        )

    if carrier == "Yodel":
        if not normalized:
            raise AmazonVTRCarrierError(
                "Packlink did not return the Yodel service. BT38 will not guess an Amazon VTR service."
            )
        is_xpect = "xpect" in normalized
        is_48 = any(token in normalized for token in ("48", "2 day", "2-day", "two day", "two-day"))
        if is_xpect and is_48:
            return "Yodel Xpect 48"
        if is_xpect and any(token in normalized for token in ("24", "next day", "next-day")):
            return "Yodel Xpect 24"
        raise AmazonVTRCarrierError(
            f"Packlink Yodel service '{service}' is not proven to match an Amazon Xpect service; shipment confirmation is held for VTR safety."
        )

    return service or "Other"
