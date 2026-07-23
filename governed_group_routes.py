from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
try:
    from flask_login import current_user
except Exception:
    current_user = None


governed_group_bp = Blueprint("governed_groups", __name__)


@governed_group_bp.get("/governed/groups/<int:group_id>")
def governed_group_detail(group_id: int):
    from extensions import db
    from models import MasterProductGroup

    group = db.session.get(MasterProductGroup, group_id)
    if not group:
        return jsonify(_blocked("Master product group was not found.", group_id=group_id)), 404
    return jsonify(_serialize_master_group(group))


@governed_group_bp.get("/governed/groups/search")
def governed_group_search():
    from extensions import db
    from models import MasterProductGroup

    q = (request.args.get("q") or "").strip().lower()
    query = db.session.query(MasterProductGroup).order_by(
        MasterProductGroup.updated_at.desc(),
        MasterProductGroup.id.desc(),
    )
    if q:
        query = query.filter(MasterProductGroup.display_title.ilike(f"%{q}%"))

    groups = query.limit(25).all()
    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "groups": [_serialize_master_group(group, include_children=False)["group"] for group in groups],
    })


@governed_group_bp.post("/governed/groups/create")
def governed_group_create():
    from extensions import db
    from models import MasterProductGroup

    body = dict(request.get_json(silent=True) or {})
    title = (body.get("display_title") or body.get("title") or "").strip() or "Untitled Master Group"
    group = MasterProductGroup(
        display_title=title[:500],
        display_image_url=(body.get("display_image_url") or body.get("image_url") or None),
    )
    db.session.add(group)
    db.session.flush()

    warehouse_stock_id = body.get("warehouse_stock_id") or body.get("stock_id")
    listing_id = body.get("listing_id") or body.get("marketplace_listing_id")
    if warehouse_stock_id:
        result = _link_stock_to_group(group, int(warehouse_stock_id), actor=_actor())
        if not result.get("ok"):
            db.session.rollback()
            return jsonify(result), 400
    if listing_id:
        result = _link_listing_to_group(group, int(listing_id), actor=_actor())
        if not result.get("ok"):
            db.session.rollback()
            return jsonify(result), 400

    db.session.commit()
    return jsonify(_serialize_master_group(group)), 201


@governed_group_bp.post("/governed/groups/<int:group_id>/link-stock")
def governed_group_link_stock(group_id: int):
    from extensions import db
    from models import MasterProductGroup

    body = dict(request.get_json(silent=True) or {})
    stock_id = body.get("warehouse_stock_id") or body.get("stock_id")
    if not stock_id:
        return jsonify(_blocked("warehouse_stock_id is required.", group_id=group_id)), 400

    group = db.session.get(MasterProductGroup, group_id)
    if not group:
        return jsonify(_blocked("Master product group was not found.", group_id=group_id)), 404

    result = _link_stock_to_group(group, int(stock_id), actor=_actor())
    if not result.get("ok"):
        return jsonify(result), 400

    db.session.commit()
    return jsonify(_serialize_master_group(group))


@governed_group_bp.post("/governed/groups/<int:group_id>/link-listing")
def governed_group_link_listing(group_id: int):
    from extensions import db
    from models import MasterProductGroup

    body = dict(request.get_json(silent=True) or {})
    listing_id = body.get("listing_id") or body.get("marketplace_listing_id")
    if not listing_id:
        return jsonify(_blocked("listing_id is required.", group_id=group_id)), 400

    group = db.session.get(MasterProductGroup, group_id)
    if not group:
        return jsonify(_blocked("Master product group was not found.", group_id=group_id)), 404

    result = _link_listing_to_group(group, int(listing_id), actor=_actor())
    if not result.get("ok"):
        return jsonify(result), 400

    db.session.commit()
    return jsonify(_serialize_master_group(group))


@governed_group_bp.post("/governed/groups/<int:group_id>/unlink")
def governed_group_unlink(group_id: int):
    from flask import jsonify
    from services.runtime_action_guard import is_execution_allowed
    from models import WarehouseStock, MarketplaceListing, db

    # EXECUTION SAFETY GATE
    if not is_execution_allowed(group_id=group_id):
        return jsonify({
            "ok": False,
            "success": False,
            "execution_blocked": True,
            "group_id": group_id,
            "reason": "Group unlink blocked due to active or pending execution."
        }), 409

    stocks = WarehouseStock.query.filter_by(master_product_group_id=group_id).all()
    listings = MarketplaceListing.query.filter_by(master_product_group_id=group_id).all()

    if not stocks and not listings:
        return jsonify({
            "ok": False,
            "success": False,
            "group_id": group_id,
            "reason": "No linked entities found."
        }), 400

    for s in stocks:
        s.master_product_group_id = None

    for l in listings:
        l.master_product_group_id = None

    db.session.commit()

    return jsonify({
        "ok": True,
        "success": True,
        "group_id": group_id,
        "message": "Group safely unlinked with execution protection.",
        "unlinked_stocks": len(stocks),
        "unlinked_listings": len(listings)
    }), 200
@governed_group_bp.post("/governed/groups/<int:group_id>/unlink")
def governed_group_unlink(group_id: int):
    from flask import jsonify
    from services.runtime_action_guard import is_execution_allowed
    from models import WarehouseStock, MarketplaceListing, db

    if not is_execution_allowed(group_id=group_id):
        return jsonify({
            "ok": False,
            "success": False,
            "execution_blocked": True,
            "group_id": group_id,
            "reason": "Group unlink blocked: active or pending execution detected."
        }), 409

    stocks = WarehouseStock.query.filter_by(master_product_group_id=group_id).all()
    listings = MarketplaceListing.query.filter_by(master_product_group_id=group_id).all()

    if not stocks and not listings:
        return jsonify({
            "ok": False,
            "success": False,
            "group_id": group_id,
            "reason": "No linked stock or listings found for this group."
        }), 400

    for stock in stocks:
        stock.master_product_group_id = None

    for listing in listings:
        listing.master_product_group_id = None

    db.session.commit()

    return jsonify({
        "ok": True,
        "success": True,
        "group_id": group_id,
        "message": "Group safely unlinked after execution validation.",
        "unlinked_stocks": len(stocks),
        "unlinked_listings": len(listings)
    }), 200
def _link_stock_to_group(group, stock_id: int, actor: str) -> dict:
    from extensions import db
    from models import WarehouseStock

    stock = db.session.get(WarehouseStock, stock_id)
    if not stock:
        return _blocked("Warehouse stock was not found.", stock_id=stock_id)

    stock.master_product_group_id = group.id
    stock.is_group_controlled = True
    stock.group_controlled_at = stock.group_controlled_at or datetime.utcnow()
    stock.updated_at = datetime.utcnow()

    if not group.display_title:
        group.display_title = (stock.product_name or stock.group_title or stock.sku or "Untitled Master Group")[:500]
    if not group.display_image_url and stock.image_url:
        group.display_image_url = stock.image_url
    group.updated_at = datetime.utcnow()

    return {"success": True, "ok": True, "governed": True, "stock_id": stock_id, "group_id": group.id}


def _link_listing_to_group(group, listing_id: int, actor: str) -> dict:
    from extensions import db
    from models import MarketplaceListing

    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing:
        return _blocked("Marketplace listing was not found.", listing_id=listing_id)

    listing.master_product_group_id = group.id
    listing.updated_at = datetime.utcnow()

    if listing.warehouse_stock:
        result = _link_stock_to_group(group, listing.warehouse_stock.id, actor=actor)
        if not result.get("ok"):
            return result

    if not group.display_title:
        group.display_title = (listing.title or listing.external_sku or "Untitled Master Group")[:500]
    group.updated_at = datetime.utcnow()

    return {"success": True, "ok": True, "governed": True, "listing_id": listing_id, "group_id": group.id}


def _serialize_master_group(group, include_children: bool = True) -> dict:
    warehouse_stocks = list(group.warehouse_stocks.all()) if include_children else []
    marketplace_listings = list(group.marketplace_listings.all()) if include_children else []
    return {
        "success": True,
        "ok": True,
        "governed": True,
        "group": {
            "id": group.id,
            "display_title": group.display_title,
            "display_image_url": group.display_image_url,
            "warehouse_stock_count": group.warehouse_stocks.count(),
            "marketplace_listing_count": group.marketplace_listings.count(),
            "created_at": group.created_at.isoformat() if group.created_at else None,
            "updated_at": group.updated_at.isoformat() if group.updated_at else None,
        },
        "warehouse_stocks": [
            {
                "id": stock.id,
                "sku": stock.sku,
                "product_name": stock.product_name,
                "sellable_quantity": stock.sellable_quantity,
                "is_group_controlled": stock.is_group_controlled,
            }
            for stock in warehouse_stocks
        ],
        "marketplace_listings": [
            {
                "id": listing.id,
                "store_id": listing.store_id,
                "store_name": listing.store.name if listing.store else None,
                "platform": listing.platform,
                "external_sku": listing.external_sku,
                "external_listing_id": listing.external_listing_id,
                "title": listing.title,
                "is_fba": listing.is_fba,
                "is_pushable": listing.is_pushable,
                "effective_quantity": listing.effective_quantity,
            }
            for listing in marketplace_listings
        ],
    }


def _actor() -> str:
    try:
        if current_user and current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return request.headers.get("X-Actor", "governed-group-action")


def _blocked(reason: str, **extra) -> dict:
    result = {
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "reason": reason,
    }
    result.update(extra)
    return result
