"""Governed warehouse configuration for SDS (Seller's Delivery Service).

This is configuration only: it does not select orders, create shipments,
manufacture tracking scans, or write to a marketplace.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import jsonify, request
from flask_login import login_required

from extensions import db
from models import Warehouse
from seller_delivery_models import WarehouseSellerDeliveryConfig


SDS_NAME = "SDS"
SDS_FULL_NAME = "Seller's Delivery Service"


def _decimal(value, *, positive=False):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid number.")
    if positive and number <= 0:
        raise ValueError("Value must be greater than zero.")
    if number < 0:
        raise ValueError("Value cannot be negative.")
    return number


def _payload(row):
    return {
        "warehouse_id": row.warehouse_id,
        "enabled": bool(row.enabled),
        "origin_postcode": row.origin_postcode,
        "radius_miles": float(row.radius_miles) if row.radius_miles is not None else None,
        "service_name": SDS_NAME,
        "service_full_name": SDS_FULL_NAME,
        "cost_mode": row.cost_mode,
        "flat_cost": float(row.flat_cost) if row.flat_cost is not None else None,
        "per_mile_cost": float(row.per_mile_cost) if row.per_mile_cost is not None else None,
        "currency": row.currency,
        "prime_sfp_allowed": False,
        "marketplace_store_owns_origin": False,
    }


def install_governed_seller_delivery_config(app) -> None:
    if getattr(app, "_bt38_seller_delivery_config_installed", False):
        return
    with app.app_context():
        WarehouseSellerDeliveryConfig.__table__.create(bind=db.engine, checkfirst=True)

    @login_required
    def seller_delivery_config(warehouse_id):
        warehouse = db.session.get(Warehouse, int(warehouse_id))
        if warehouse is None:
            return jsonify({"success": False, "message": "Warehouse not found."}), 404
        row = WarehouseSellerDeliveryConfig.query.filter_by(warehouse_id=warehouse.id).first()
        if request.method == "GET":
            return jsonify({
                "success": True,
                "warehouse": {"id": warehouse.id, "name": warehouse.name},
                "config": _payload(row) if row else None,
            })

        data = request.get_json(silent=True) or {}
        enabled = bool(data.get("enabled", False))
        origin_postcode = str(data.get("origin_postcode") or "").strip().upper() or None
        try:
            radius = _decimal(data.get("radius_miles"), positive=True)
            flat_cost = _decimal(data.get("flat_cost"))
            per_mile_cost = _decimal(data.get("per_mile_cost"))
        except ValueError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400
        cost_mode = str(data.get("cost_mode") or "manual").strip().lower()
        if cost_mode not in {"manual", "flat", "per_mile"}:
            return jsonify({"success": False, "message": "Unsupported SDS cost mode."}), 400
        if enabled and (not origin_postcode or radius is None):
            return jsonify({"success": False, "message": "Origin postcode and delivery radius are required before enabling SDS."}), 400
        if cost_mode == "flat" and flat_cost is None:
            return jsonify({"success": False, "message": "Flat delivery cost is required for SDS flat cost mode."}), 400
        if cost_mode == "per_mile" and per_mile_cost is None:
            return jsonify({"success": False, "message": "Per-mile delivery cost is required for SDS per-mile cost mode."}), 400

        if row is None:
            row = WarehouseSellerDeliveryConfig(warehouse_id=warehouse.id)
            db.session.add(row)
        row.enabled = enabled
        row.origin_postcode = origin_postcode
        row.radius_miles = radius
        row.service_name = SDS_NAME
        row.cost_mode = cost_mode
        row.flat_cost = flat_cost
        row.per_mile_cost = per_mile_cost
        row.currency = str(data.get("currency") or "GBP").strip().upper()[:3] or "GBP"
        db.session.commit()
        return jsonify({"success": True, "config": _payload(row)})

    app.add_url_rule(
        "/governed/warehouses/<int:warehouse_id>/seller-delivery",
        endpoint="bt38_seller_delivery_config",
        view_func=seller_delivery_config,
        methods=["GET", "POST"],
    )
    app._bt38_seller_delivery_config_installed = True
