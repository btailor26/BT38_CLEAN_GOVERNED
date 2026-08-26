from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request

governed_runtime_visibility_bp = Blueprint("governed_runtime_visibility", __name__)


def _non_negative_float(value, field_name: str):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid_{field_name}")
    if number < 0:
        raise ValueError(f"negative_{field_name}_not_allowed")
    return number


def _stock_economics_payload(stock):
    unit_cost = float(getattr(stock, "unit_cost", 0) or 0)
    weight_kg = float(getattr(stock, "product_weight_kg", 0) or 0)
    shipping_rate = float(getattr(stock, "shipping_cost_per_kg", 0) or 0)
    fee_rate = float(getattr(stock, "commission_rate", 0) or 0)
    return {
        "warehouse_stock_id": int(stock.id),
        "sku": stock.sku,
        "unit_cost": round(unit_cost, 2),
        "product_weight_kg": round(weight_kg, 4),
        "shipping_cost_per_kg": round(shipping_rate, 4),
        "shipping_cost": round(weight_kg * shipping_rate, 2),
        "commission_rate": round(fee_rate, 4),
    }


@governed_runtime_visibility_bp.get("/governed/warehouse/runtime-state")
def governed_warehouse_runtime_state():
    """Lightweight warehouse runtime heartbeat.

    This endpoint must not export MarketplaceListing rows, WarehouseStock rows,
    diagnostics dumps, or hidden overlay payloads. Pages can use it only to know
    whether the governed runtime/fuse-box layer is alive.
    """
    from models import SystemConfig

    fuse_keys = (
        "read_only_mode",
        "dry_run_mode",
        "queue_frozen",
        "sync_enabled",
        "runtime_sync_enabled",
        "marketplace_sync_enabled",
        "manual_sync_enabled",
        "push_enabled",
        "runtime_push_enabled",
        "marketplace_push_enabled",
        "manual_push_enabled",
    )

    config_rows = (
        SystemConfig.query
        .filter(SystemConfig.key.in_(fuse_keys))
        .all()
    )
    config_values = {
        str(row.key): str(row.value).strip().lower()
        for row in config_rows
    }

    def enabled(key: str) -> bool:
        return config_values.get(key, "") in {"1", "true", "yes", "on", "enabled"}

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "visibility_only": True,
        "mode": "heartbeat",
        "runtime_state_lightweight": True,
        "timestamp": datetime.utcnow().isoformat(),
        "fuse_box": {
            "read_only_mode": enabled("read_only_mode"),
            "dry_run_mode": enabled("dry_run_mode"),
            "queue_frozen": enabled("queue_frozen"),
            "sync_enabled": enabled("sync_enabled"),
            "runtime_sync_enabled": enabled("runtime_sync_enabled"),
            "marketplace_sync_enabled": enabled("marketplace_sync_enabled"),
            "manual_sync_enabled": enabled("manual_sync_enabled"),
            "push_enabled": enabled("push_enabled"),
            "runtime_push_enabled": enabled("runtime_push_enabled"),
            "marketplace_push_enabled": enabled("marketplace_push_enabled"),
            "manual_push_enabled": enabled("manual_push_enabled"),
        },
        "message": "Runtime heartbeat only. No listing or warehouse rows are exported from this endpoint.",
    })


@governed_runtime_visibility_bp.get("/governed/warehouse/economics-batch")
def governed_warehouse_economics_batch():
    """Return local costing defaults for visible Master Stock rows in one read."""
    from extensions import db
    from models import WarehouseStock

    raw_ids = (request.args.get("stock_ids") or "").strip()
    stock_ids = []
    for raw in raw_ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value > 0 and value not in stock_ids:
            stock_ids.append(value)
        if len(stock_ids) >= 250:
            break

    if not stock_ids:
        return jsonify(success=True, ok=True, governed=True, local_only=True, economics=[])

    rows = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.id.in_(stock_ids))
        .filter(WarehouseStock.is_deleted == False)  # noqa: E712
        .all()
    )

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "local_only": True,
        "marketplace_calls": False,
        "economics": [_stock_economics_payload(stock) for stock in rows],
    })


@governed_runtime_visibility_bp.get("/governed/warehouse/<int:stock_id>/economics")
def governed_warehouse_economics(stock_id: int):
    """Read warehouse-owned unit economics for one Master Stock identity.

    This is local database truth only. It never calls Amazon/eBay and never
    triggers a marketplace write.
    """
    from extensions import db
    from models import MarketplaceListing, WarehouseStock

    stock = db.session.get(WarehouseStock, stock_id)
    if not stock or bool(getattr(stock, "is_deleted", False)):
        return jsonify(success=False, ok=False, error="warehouse_stock_not_found"), 404

    listing = None
    listing_id = request.args.get("listing_id")
    if listing_id:
        try:
            candidate = db.session.get(MarketplaceListing, int(listing_id))
        except (TypeError, ValueError):
            candidate = None
        if candidate and int(getattr(candidate, "warehouse_stock_id", 0) or 0) == int(stock.id):
            listing = candidate

    sale_price = float(getattr(listing, "price", 0) or 0)
    base = _stock_economics_payload(stock)
    unit_cost = base["unit_cost"]
    shipping_cost = base["shipping_cost"]
    fee_rate = base["commission_rate"]
    estimated_fee = round(sale_price * (fee_rate / 100.0), 2) if sale_price > 0 else 0.0

    complete = bool(sale_price > 0 and unit_cost > 0)
    estimated_profit = round(sale_price - unit_cost - shipping_cost - estimated_fee, 2) if complete else None
    margin = round((estimated_profit / sale_price) * 100.0, 1) if estimated_profit is not None and sale_price > 0 else None

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "local_only": True,
        **base,
        "listing_id": getattr(listing, "id", None),
        "sale_price": round(sale_price, 2),
        "estimated_marketplace_fee": estimated_fee,
        "fee_source": "warehouse_estimate_rate",
        "actual_marketplace_fees_wired": False,
        "estimated_profit": estimated_profit,
        "estimated_margin": margin,
        "complete": complete,
        "message": "Warehouse economics loaded. Marketplace fees are estimated from the stored rate until marketplace fee extraction is wired.",
    })


@governed_runtime_visibility_bp.post("/governed/warehouse/<int:stock_id>/economics")
def governed_warehouse_economics_save(stock_id: int):
    """Save warehouse-owned costing assumptions only.

    Explicitly does not push price, quantity, stock, fulfilment or any other
    state to a marketplace.
    """
    from extensions import db
    from models import WarehouseStock

    stock = db.session.get(WarehouseStock, stock_id)
    if not stock or bool(getattr(stock, "is_deleted", False)):
        return jsonify(success=False, ok=False, error="warehouse_stock_not_found"), 404

    payload = request.get_json(silent=True) or {}

    updates = {}
    fields = {
        "unit_cost": "unit_cost",
        "product_weight_kg": "product_weight_kg",
        "shipping_cost_per_kg": "shipping_cost_per_kg",
        "commission_rate": "commission_rate",
    }

    try:
        for payload_key, model_field in fields.items():
            if payload_key not in payload:
                continue
            value = _non_negative_float(payload.get(payload_key), payload_key)
            setattr(stock, model_field, value)
            updates[payload_key] = value
    except ValueError as exc:
        return jsonify(success=False, ok=False, error=str(exc)), 400

    if not updates:
        return jsonify(success=False, ok=False, error="no_economics_fields_supplied"), 400

    db.session.commit()
    base = _stock_economics_payload(stock)

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "local_only": True,
        "marketplace_write": False,
        **base,
        "updated_fields": sorted(updates.keys()),
        "message": "Warehouse costing defaults saved locally. No marketplace write was performed.",
    })