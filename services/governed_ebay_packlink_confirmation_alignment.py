"""Align paid Packlink labels with the existing governed eBay dispatch path.

Hard boundary:
- Amazon keeps the verified carrier + service mapping gate required by the
  existing VTR-safe confirmation path.
- eBay uses its own marketplace-specific carrier identity. A paid Packlink
  label with tracking must not be held by the Amazon-style mapping gate.

The existing marketplace-specific mapping record is still created for audit
and future reuse, but an unseen eBay Packlink service does not block eBay
CompleteSale. No postage purchase, tracking invention, pickup inference or
parallel dispatch implementation is introduced here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from extensions import db
from models import MarketplaceOrder


def _platform_for_shipment(shipment) -> tuple[MarketplaceOrder | None, str]:
    order = MarketplaceOrder.query.filter_by(
        store_id=shipment.store_id,
        marketplace_order_id=shipment.marketplace_order_id,
    ).order_by(MarketplaceOrder.id.asc()).first()
    store = getattr(order, "store", None) if order is not None else None
    platform = str(getattr(store, "platform", "") or "").strip().casefold()
    return order, platform


def install_governed_ebay_packlink_confirmation_alignment() -> None:
    """Hard-lock eBay Packlink confirmation away from Amazon mapping rules."""
    import services.fbm_post_purchase as post_purchase

    if getattr(post_purchase, "_governed_ebay_packlink_alignment_installed", False):
        return

    original_ensure_mapping_review = post_purchase.ensure_mapping_review
    original_confirm_external_shipment = post_purchase.confirm_external_shipment

    def aligned_ensure_mapping_review(*args: Any, **kwargs: Any):
        mapping, review, mapping_ready = original_ensure_mapping_review(*args, **kwargs)
        marketplace = str(kwargs.get("marketplace") or "").strip().casefold()
        provider = str(kwargs.get("provider") or "").strip().casefold()
        shipment = kwargs.get("shipment")

        if marketplace != "ebay" or provider != "packlink" or shipment is None:
            return mapping, review, mapping_ready

        # eBay and Amazon mappings are deliberately different. The mapping row
        # remains marketplace-specific and can still be reviewed, but eBay's
        # existing CompleteSale contract only needs the actual carrier identity
        # plus tracking. Do not reuse Amazon's verified carrier/service gate.
        carrier_present = bool(str(getattr(shipment, "carrier", "") or "").strip())
        return mapping, review, carrier_present

    def aligned_confirm_external_shipment(*, shipment, mapping):
        provider = str(getattr(shipment, "provider", "") or "").strip().casefold()
        order, platform = _platform_for_shipment(shipment)
        if platform != "ebay" or provider != "packlink":
            return original_confirm_external_shipment(shipment=shipment, mapping=mapping)

        if getattr(shipment, "marketplace_confirmed_at", None):
            return {
                "success": True,
                "already_confirmed": True,
                "confirmed_at": shipment.marketplace_confirmed_at.isoformat(),
            }

        tracking = str(getattr(shipment, "tracking_number", "") or "").strip()
        if not tracking:
            shipment.marketplace_confirmation_status = "tracking_required"
            shipment.marketplace_confirmation_error = (
                "Tracking number is required before eBay confirmation."
            )
            db.session.commit()
            return {"success": False, "held": True, "reason": "tracking_required"}

        if order is None:
            shipment.marketplace_confirmation_status = "order_missing"
            shipment.marketplace_confirmation_error = "eBay marketplace order is missing from BT38."
            db.session.commit()
            return {"success": False, "held": True, "reason": "order_missing"}

        from services.fbm_marketplace_confirmation import (
            FBMMarketplaceConfirmationError,
            _confirm_ebay_external,
            _persist_confirmed_order_lines,
            _record_dispatch_bell_event,
        )

        try:
            confirmation = _confirm_ebay_external(
                order=order,
                shipment=shipment,
                mapping=mapping,
                tracking=tracking,
            )
        except FBMMarketplaceConfirmationError as exc:
            shipment.marketplace_confirmation_status = "confirmation_failed"
            shipment.marketplace_confirmation_error = str(exc)
            db.session.commit()
            return {
                "success": False,
                "held": False,
                "reason": "confirmation_failed",
                "error": str(exc),
            }

        now = datetime.utcnow()
        shipment.marketplace_confirmed_at = now
        shipment.marketplace_confirmation_status = "confirmed"
        shipment.marketplace_confirmation_error = None

        # eBay carrier identity is marketplace-specific. Prefer an explicitly
        # verified eBay mapping when one exists; otherwise use the actual
        # Packlink carrier returned for this shipment. Never borrow Amazon data.
        carrier = str(
            getattr(mapping, "marketplace_carrier_name", None)
            or getattr(mapping, "marketplace_carrier_code", None)
            or getattr(shipment, "carrier", None)
            or "Other"
        ).strip() or "Other"
        service = str(getattr(shipment, "service", "") or "").strip()

        _persist_confirmed_order_lines(
            order=order,
            shipment=shipment,
            carrier=carrier,
            tracking=tracking,
            now=now,
        )
        db.session.commit()
        bell_recorded = _record_dispatch_bell_event(
            order=order,
            shipment=shipment,
            marketplace="ebay",
            carrier=carrier,
            service=service,
            tracking=tracking,
            now=now,
        )
        return {
            "success": True,
            "already_confirmed": False,
            "confirmed_at": now.isoformat(),
            "marketplace": "ebay",
            "carrier": carrier,
            "tracking_number": tracking,
            "mapping_verified": getattr(mapping, "verification_status", None) == "verified",
            "mapping_required_for_confirmation": False,
            "bell_recorded": bell_recorded,
            "complete_sale": confirmation,
        }

    # persist_external_label resolves these globals at call time, so patching
    # only the two decision points keeps all existing Packlink callback/status
    # entry points on the same post-purchase path.
    post_purchase.ensure_mapping_review = aligned_ensure_mapping_review
    post_purchase.confirm_external_shipment = aligned_confirm_external_shipment
    post_purchase._governed_ebay_packlink_alignment_installed = True
