"""Strict Amazon UK VTR carrier/service resolution for external FBM postage.

Packlink is only an external postage provider. Amazon VTR depends on the real
carrier/tracking identity and an appropriate delivery service. Unknown carrier
or service identities are therefore held rather than guessed.
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
    # Packlink may still surface the pre-rebrand Hermes carrier name. Amazon UK
    # currently expects the carrier identity Evri for VTR measurement.
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

    # Yodel exposes an internal YOD* reference as well as the scannable parcel
    # barcode on some flows. Amazon UK needs the parcel tracking identifier.
    if carrier == "Yodel" and tracking.upper().startswith("YOD"):
        raise AmazonVTRCarrierError(
            "Packlink returned a Yodel internal YOD reference, not the scannable parcel tracking number required for Amazon VTR."
        )
    # Hyphens/spaces have caused Yodel IDs to fail Amazon/Yodel recognition in
    # real UK VTR cases. Preserve only the compact identifier sent to tracking.
    if carrier == "Yodel":
        tracking = tracking.replace("-", "")
    return tracking


def amazon_shipping_method(provider_service: Any, carrier: str) -> str:
    """Resolve the Packlink product into the Amazon UK ship-confirm service.

    Yodel is strict because generic/mismatched Yodel service reporting has caused
    VTR failures. Evri is intentionally different: Amazon UK currently exposes
    Evri with an ``Other`` service and states that this does not reduce VTR.
    """
    service = " ".join(str(provider_service or "").strip().split())
    normalized = _norm(service)

    if carrier == "Evri":
        # Packlink can still expose legacy Hermes product names such as a
        # 2nd-day/drop-off service. Amazon wants the rebranded carrier Evri; its
        # supported ship-confirm sub-option is Other.
        return "Other"

    if carrier == "Yodel":
        if not normalized:
            raise AmazonVTRCarrierError(
                "Packlink did not return the Yodel service. BT38 will not guess an Amazon VTR service."
            )
        # Normalise known Xpect two-day forms to the Amazon service wording that
        # has been recommended for UK Yodel VTR issues.
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
