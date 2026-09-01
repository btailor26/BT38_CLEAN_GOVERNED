"""Governed Seller Delivery Service (SDS) dispatch selection.

SDS remains an explicit seller choice. This route re-evaluates eligibility from
persisted order + warehouse data immediately before creating one persisted
FBMShipment. It does not write the marketplace, manufacture tracking scans, or
mark the order delivered.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import jsonify, request
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from fbm_models import FBMShipment
from shipping_spend_models import ShippingSpendLedger
from services.governed_sds_fbm_alignment import sds_for_fbm_order


def _money(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount >= 0 else None


def _actual_dispatch_cost(config, distance_miles, body) -> tuple[Decimal | None, str | None]:
    mode = str(getattr(config, "cost_mode", "manual") or "manual").strip().lower()
    if mode == "flat":
        return _money(getattr(config, "flat_cost", None)), "sds_flat_dispatch_cost"
    if mode == "per_mile":
        rate = _money(getattr(config, "per_mile_cost", None))
        distance = _money(distance_miles)
        if rate is None or distance is None:
            return None, None
        return (rate * distance).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "sds_per_mile_dispatch_cost"
    if mode == "manual":
        amount = _money(body.get("actual_cost"))
        return (amount, "sds_manual_dispatch_cost") if amount is not None else (None, None)
    return None, None


def _persist_spend(shipment, *, amount, currency, source):
    if amount is None:
        return None
    dispatch_key = shipment.purchase_key or f"shipment:{shipment.id}"
    row = ShippingSpendLedger.query.filter_by(dispatch_key=dispatch_key).first()
    if row is None:
        row = ShippingSpendLedger(dispatch_key=dispatch_key)
        db.session.add(row)
    row.shipment_id = shipment.id
    row.store_id = shipment.store_id
    row.marketplace_order_id = shipment.marketplace_order_id
    row.fulfillment_family = "FBM"
    row.provider = "sds"
    row.amount = amount
    row.currency = str(currency or "GBP").upper()[:3]
    row.source = source
    row.source_reference = shipment.provider_shipment_id
    row.confirmed = True
    row.recorded_at = shipment.label_purchased_at or datetime.utcnow()
    return row


def install_governed_sds_dispatch_alignment(app) -> None:
    if getattr(app, "_bt38_sds_dispatch_alignment_installed", False):
        return

    import governed_fbm_routes as fbm
    from seller_delivery_models import WarehouseSellerDeliveryConfig

    endpoint = "bt38_sds_dispatch_select"
    if endpoint in app.view_functions:
        app._bt38_sds_dispatch_alignment_installed = True
        return

    @app.post("/fbm/orders/<int:order_id>/sds/select", endpoint=endpoint)
    @login_required
    def select_sds(order_id: int):
        order = fbm._get_fbm_order(order_id)
        if order is None:
            return jsonify({"success": False, "message": "FBM order not found."}), 404

        body = request.get_json(silent=True) or {}
        if body.get("confirm_selection") != "SELECT_SDS":
            return jsonify({"success": False, "message": "Explicit SELECT_SDS confirmation is required."}), 400

        profile, profile_error = (
            fbm._amazon_profile(order, refresh=True)
            if fbm._platform(order).strip().lower() == "amazon"
            else (fbm._profile_for(order), None)
        )
        if fbm._platform(order).strip().lower() == "amazon" and profile_error and profile is None:
            return jsonify({"success": False, "message": "Amazon shipping profile could not be verified; SDS remains blocked."}), 409
        prime_sfp = bool(profile and getattr(profile, "is_prime", None) is True)
        eligibility = sds_for_fbm_order(order, prime_sfp=prime_sfp)
        if not eligibility.get("eligible"):
            return jsonify({
                "success": False,
                "message": "SDS is not eligible for this order.",
                "eligibility_reason": eligibility.get("reason"),
            }), 409

        warehouse_id = eligibility.get("warehouse_id")
        config = db.session.get(WarehouseSellerDeliveryConfig, int(warehouse_id)) if warehouse_id else None
        if config is None or not config.enabled:
            return jsonify({"success": False, "message": "SDS warehouse configuration is no longer enabled."}), 409

        purchase_key = f"sds:{order.store_id}:{order.marketplace_order_id}"
        shipment = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
        if shipment is not None:
            return jsonify({
                "success": True,
                "already_selected": True,
                "shipment_id": shipment.id,
                "provider": "sds",
                "status": shipment.status,
                "message": "SDS was already selected for this order. No second dispatch was created.",
            })

        now = datetime.utcnow()
        shipment = FBMShipment(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
            provider="sds",
            provider_service_id="seller_delivery_service",
            carrier="Seller Delivery Service",
            service=str(getattr(config, "service_name", None) or "Seller's Delivery Service"),
            purchase_key=purchase_key,
            purchase_status="selected",
            status="awaiting_seller_handover",
            label_source="sds",
            label_storage_ref=f"SDS-{order.store_id}-{order.marketplace_order_id}",
        )
        db.session.add(shipment)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            existing = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
            if existing is not None:
                return jsonify({"success": True, "already_selected": True, "shipment_id": existing.id, "provider": "sds", "status": existing.status})
            return jsonify({"success": False, "message": "SDS selection state changed; reload the order before retrying."}), 409

        amount, source = _actual_dispatch_cost(config, eligibility.get("distance_miles"), body)
        if str(getattr(config, "cost_mode", "manual") or "manual").lower() == "manual" and amount is None:
            db.session.rollback()
            return jsonify({"success": False, "message": "Actual SDS dispatch cost is required for manual cost mode."}), 400
        if amount is None or source is None:
            db.session.rollback()
            return jsonify({"success": False, "message": "SDS dispatch cost could not be resolved from the configured cost mode."}), 409

        shipment.label_purchased_at = now
        _persist_spend(
            shipment,
            amount=amount,
            currency=getattr(config, "currency", "GBP"),
            source=source,
        )
        db.session.commit()
        return jsonify({
            "success": True,
            "already_selected": False,
            "shipment_id": shipment.id,
            "provider": "sds",
            "status": shipment.status,
            "distance_miles": eligibility.get("distance_miles"),
            "dispatch_cost": {"amount": str(amount), "currency": str(getattr(config, "currency", "GBP") or "GBP").upper()[:3]},
            "marketplace_written": False,
            "tracking_created": False,
            "message": "SDS selected and persisted. No marketplace dispatch or tracking event has been manufactured.",
        })

    app._bt38_sds_dispatch_alignment_installed = True
