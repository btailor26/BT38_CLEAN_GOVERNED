from __future__ import annotations

from flask import Blueprint, jsonify, request

governed_runtime_visibility_bp = Blueprint("governed_runtime_visibility", __name__)


@governed_runtime_visibility_bp.get("/governed/warehouse/runtime-state")
def governed_warehouse_runtime_state():
    """Visibility-only runtime state for Master Stock rows."""
    from extensions import db
    from models import MarketplaceListing, WarehouseStock

    q = (request.args.get("q") or "").strip().lower()
    limit = min(int(request.args.get("limit", 500)), 1000)

    rows = []
    listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        .limit(limit)
        .all()
    )

    for listing in listings:
        row = _listing_state(listing)
        if not q or q in _haystack(row):
            rows.append(row)

    linked_stock_ids = {x.warehouse_stock_id for x in listings if x.warehouse_stock_id}

    stocks = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.is_active == True)  # noqa: E712
        .filter(WarehouseStock.is_deleted == False)  # noqa: E712
        .order_by(WarehouseStock.updated_at.desc(), WarehouseStock.id.desc())
        .limit(limit)
        .all()
    )

    for stock in stocks:
        if stock.id in linked_stock_ids:
            continue
        row = _stock_state(stock)
        if not q or q in _haystack(row):
            rows.append(row)
        if len(rows) >= limit:
            break

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "visibility_only": True,
        "total": len(rows),
        "rows": rows,
        "legend": {
            "authority": "Warehouse stock is the quantity authority.",
            "receiver": "Marketplace listing receives quantity when eligible.",
            "fba_read_only": "Amazon FBA/AFN is visible but never pushed.",
            "fbm_pushable": "Amazon FBM/MFN pushes only through governed execution.",
            "mcf_eligible": "MCF is eligible only on Amazon FBA/AFN rows.",
            "group_controlled": "Grouped rows are controlled through MasterProductGroup.",
        },
    })


def _listing_state(listing):
    stock = listing.warehouse_stock
    platform = (listing.store.platform or "").strip() if listing.store else "Marketplace"
    platform_lower = platform.lower()
    channel = (listing.normalized_amazon_fulfillment_channel or "").upper()

    is_amazon = "amazon" in platform_lower
    is_fbm = is_amazon and channel in ("MFN", "FBM", "MERCHANT")
    is_fba = is_amazon and not is_fbm
    marketplace = "amazon" if is_amazon else "ebay" if "ebay" in platform_lower else platform_lower

    group_id = listing.master_product_group_id or (stock.master_product_group_id if stock else None)
    pushable = bool((not is_fba) and listing.is_pushable and stock)

    if is_fba:
        role = "fba_read_only"
        action = "skip_before_push"
        reason = "Amazon FBA/AFN is read-only. MCF may use FBA stock, but quantity propagation must not push FBA."
    elif not stock:
        role = "unlinked_receiver"
        action = "blocked"
        reason = "Listing is not linked to warehouse stock."
    elif pushable:
        role = "marketplace_receiver"
        action = "governed_pushable"
        reason = "Listing can receive warehouse quantity through governed execution."
    else:
        role = "marketplace_receiver"
        action = "blocked"
        reason = "Listing is not pushable under current listing state."

    return {
        "row_type": "marketplace_listing",
        "runtime_role": role,
        "action_state": action,
        "reason": reason,
        "marketplace": marketplace,
        "platform": platform,
        "store_id": listing.store_id,
        "store_name": listing.store.name if listing.store else None,
        "listing_id": listing.id,
        "warehouse_stock_id": stock.id if stock else None,
        "sku": stock.sku if stock else listing.external_sku,
        "external_sku": listing.external_sku,
        "external_listing_id": listing.external_listing_id,
        "title": listing.title,
        "asin": listing.asin,
        "fnsku": listing.fnsku,
        "master_product_group_id": group_id,
        "is_group_controlled": bool(stock.is_group_controlled) if stock else bool(group_id),
        "is_fba": bool(is_fba),
        "is_fbm": bool(is_fbm),
        "is_mcf_eligible": bool(is_fba),
        "is_pushable": pushable,
        "quantity_authority": "warehouse_stock" if stock else "not_linked",
        "sellable_quantity": stock.sellable_quantity if stock else 0,
        "effective_quantity": listing.effective_quantity,
        "last_push_status": listing.last_push_status,
        "last_push_quantity": listing.last_push_quantity,
        "last_push_error": listing.last_push_error,
    }


def _stock_state(stock):
    return {
        "row_type": "warehouse_stock",
        "runtime_role": "quantity_authority",
        "action_state": "authority_only",
        "reason": "Warehouse stock is the source of truth for sellable quantity.",
        "marketplace": "warehouse",
        "platform": "Warehouse",
        "store_id": None,
        "store_name": stock.warehouse.name if stock.warehouse else "Warehouse",
        "listing_id": None,
        "warehouse_stock_id": stock.id,
        "sku": stock.sku,
        "external_sku": None,
        "external_listing_id": None,
        "title": stock.product_name,
        "asin": None,
        "fnsku": None,
        "master_product_group_id": stock.master_product_group_id,
        "is_group_controlled": bool(stock.is_group_controlled),
        "is_fba": False,
        "is_fbm": False,
        "is_mcf_eligible": False,
        "is_pushable": False,
        "quantity_authority": "warehouse_stock",
        "sellable_quantity": stock.sellable_quantity,
        "effective_quantity": stock.sellable_quantity,
        "last_push_status": None,
        "last_push_quantity": None,
        "last_push_error": None,
    }


def _haystack(row):
    fields = [
        "sku", "external_sku", "external_listing_id", "title", "asin", "fnsku",
        "store_name", "platform", "marketplace", "runtime_role", "action_state",
        "master_product_group_id",
    ]
    return " ".join(str(row.get(x) or "") for x in fields).lower()
