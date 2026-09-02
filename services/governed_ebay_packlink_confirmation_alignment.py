"""Align paid Packlink labels with the existing governed eBay dispatch path.

Amazon keeps the strict verified carrier/service mapping gate because that
mapping is part of its VTR-safe confirmation contract. eBay does not need that
Amazon-specific gate: once Packlink has returned a paid label with tracking,
BT38 can use the existing governed eBay CompleteSale path with the provider
carrier and tracking.

This module does not purchase postage, invent tracking, infer physical pickup,
or create another marketplace dispatch implementation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from models import MarketplaceOrder


def install_governed_ebay_packlink_confirmation_alignment() -> None:
    """Release tracked Packlink purchases to eBay without weakening Amazon."""
    import governed_fbm_routes
    import services.fbm_packlink_callback as packlink_callback
    import services.fbm_post_purchase as post_purchase

    if getattr(post_purchase, "_governed_ebay_packlink_alignment_installed", False):
        return

    original = post_purchase.persist_external_label

    def aligned_persist_external_label(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(*args, **kwargs)

        shipment = kwargs.get("shipment")
        marketplace = str(kwargs.get("marketplace") or "").strip().casefold()
        provider = str(kwargs.get("provider") or "").strip().casefold()
        if shipment is None or marketplace != "ebay" or provider != "packlink":
            return result

        tracking = str(getattr(shipment, "tracking_number", "") or "").strip()
        if not tracking or getattr(shipment, "marketplace_confirmed_at", None):
            return result

        # The original path has already persisted the paid label/tracking and
        # created the exact provider mapping/review. For eBay only, a pending
        # mapping must not hold dispatch: CompleteSale accepts the actual
        # Packlink carrier plus tracking. Amazon remains on the original gate.
        mapping = None
        mapping_payload = result.get("mapping") if isinstance(result, dict) else None
        mapping_id = mapping_payload.get("id") if isinstance(mapping_payload, dict) else None
        if mapping_id:
            from fbm_models import FBMCarrierServiceMapping
            mapping = db.session.get(FBMCarrierServiceMapping, int(mapping_id))
        if mapping is None:
            return result

        order = MarketplaceOrder.query.filter_by(
            store_id=shipment.store_id,
            marketplace_order_id=shipment.marketplace_order_id,
        ).order_by(MarketplaceOrder.id.asc()).first()
        if order is None:
            return result

        try:
            confirmation = post_purchase.confirm_external_shipment(
                shipment=shipment,
                mapping=mapping,
            ) if mapping.verification_status == "verified" and mapping.marketplace_carrier_code else None

            if confirmation is None:
                from services.fbm_marketplace_confirmation import (
                    _confirm_ebay_external,
                    _persist_confirmed_order_lines,
                    _record_dispatch_bell_event,
                )
                confirmation = _confirm_ebay_external(
                    order=order,
                    shipment=shipment,
                    mapping=mapping,
                    tracking=tracking,
                )
                now = datetime.utcnow()
                shipment.marketplace_confirmed_at = now
                shipment.marketplace_confirmation_status = "confirmed"
                shipment.marketplace_confirmation_error = None
                carrier = str(
                    mapping.marketplace_carrier_name
                    or mapping.marketplace_carrier_code
                    or shipment.carrier
                    or "Other"
                ).strip() or "Other"
                _persist_confirmed_order_lines(
                    order=order,
                    shipment=shipment,
                    carrier=carrier,
                    tracking=tracking,
                    now=now,
                )
                db.session.commit()
                _record_dispatch_bell_event(
                    order=order,
                    shipment=shipment,
                    marketplace="ebay",
                    carrier=carrier,
                    service=str(shipment.service or "").strip(),
                    tracking=tracking,
                    now=now,
                )

            result["marketplace_confirmation_allowed"] = True
            result["marketplace_confirmation"] = confirmation
            result["mapping_message"] = (
                "eBay dispatch confirmed from the paid Packlink label and tracking. "
                "Provider mapping review remains informational for this eBay service."
            )
        except Exception as exc:
            shipment.marketplace_confirmation_status = "confirmation_failed"
            shipment.marketplace_confirmation_error = str(exc)
            db.session.commit()
            result["marketplace_confirmation"] = {
                "success": False,
                "reason": "confirmation_failed",
                "error": str(exc),
            }

        return result

    post_purchase.persist_external_label = aligned_persist_external_label
    governed_fbm_routes.persist_external_label = aligned_persist_external_label
    packlink_callback.persist_external_label = aligned_persist_external_label
    post_purchase._governed_ebay_packlink_alignment_installed = True
