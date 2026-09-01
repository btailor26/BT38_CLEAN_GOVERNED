"""Install SDS eligibility into the existing governed FBM read authority.

This alignment wraps the two existing FBM read helpers. It does not replace the
FBM routes, create shipments, or write marketplaces.
"""
from __future__ import annotations

from services.governed_sds_fbm_alignment import sds_for_fbm_order


def install_governed_sds_fbm_read_alignment() -> None:
    import governed_fbm_routes as fbm

    if getattr(fbm, "_bt38_sds_fbm_read_alignment_installed", False):
        return

    original_mode = fbm._marketplace_shipping_mode
    original_options = fbm._shipping_provider_options

    def shipping_mode(order, platform, profile=None):
        payload = original_mode(order, platform, profile)
        prime_sfp = bool(profile and getattr(profile, "is_prime", None) is True)
        payload["sds"] = sds_for_fbm_order(order, prime_sfp=prime_sfp)
        return payload

    def provider_options(order, profile=None, profile_error=None):
        options = original_options(order, profile, profile_error)
        prime_sfp = bool(profile and getattr(profile, "is_prime", None) is True)
        sds = sds_for_fbm_order(order, prime_sfp=prime_sfp)
        options.append({
            "provider": "sds",
            "label": "SDS",
            "kind": "seller_delivery",
            "configured": sds.get("reason") != "sds_warehouse_unresolved",
            "available": bool(sds.get("eligible")),
            "recommended": False,
            "supports_prime_sfp": False,
            "prime_locked": prime_sfp,
            "label_formats": [],
            "auto_print_supported": False,
            "requires_terms_acceptance": False,
            "distance_miles": sds.get("distance_miles"),
            "radius_miles": sds.get("radius_miles"),
            "warehouse_id": sds.get("warehouse_id"),
            "eligibility_reason": sds.get("reason"),
            "message": (
                "Prime/SFP is locked to Amazon Buy Shipping."
                if prime_sfp
                else "SDS is available for this order within the configured warehouse delivery radius."
                if sds.get("eligible")
                else "SDS is not available for this order."
            ),
        })
        return options

    fbm._marketplace_shipping_mode = shipping_mode
    fbm._shipping_provider_options = provider_options
    fbm._bt38_sds_fbm_read_alignment_installed = True
