from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, make_response, request
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
    push_result = _push_group_safely(group.id, source="product_linking_auto_push")
    payload = _serialize_master_group(group)
    payload.update({
        "auto_push_attempted": True,
        "auto_push_success": _push_succeeded(push_result),
        "push_result": push_result,
        "refresh_required": True,
    })
    return _fresh_response(payload)


@governed_group_bp.post("/governed/groups/<int:group_id>/unlink")
def governed_group_unlink(group_id: int):
    """Return a listing to its own original product group.

    Every product has a permanent standalone group. Linking temporarily moves a
    listing into another product's active group. Unlinking must therefore restore
    the listing to the group belonging to its own SKU, never NULL and never an
    inferred ``auto`` group.
    """
    from extensions import db
    from models import MarketplaceListing, MasterProductGroup, WarehouseStock

    body = dict(request.get_json(silent=True) or {})
    group = db.session.get(MasterProductGroup, group_id)
    if not group:
        return jsonify(_blocked("Master product group was not found.", group_id=group_id)), 404

    stock_id = body.get("warehouse_stock_id") or body.get("stock_id")
    listing_id = body.get("listing_id") or body.get("marketplace_listing_id")
    if not stock_id and not listing_id:
        return jsonify(_blocked("warehouse_stock_id or listing_id is required.", group_id=group_id)), 400

    now = datetime.utcnow()

    if listing_id:
        listing = db.session.get(MarketplaceListing, int(listing_id))
        if not listing or listing.master_product_group_id != group_id:
            return jsonify(_blocked(
                "Marketplace listing is not linked to this group.",
                group_id=group_id,
                listing_id=listing_id,
            )), 400

        original_stock, original_group = _resolve_original_membership(listing, active_group_id=group_id)
        if not original_stock or not original_group:
            return jsonify(_blocked(
                "The listing's original product group could not be resolved safely.",
                group_id=group_id,
                listing_id=listing_id,
                sku=(listing.external_sku or None),
            )), 409

        listing.warehouse_stock_id = original_stock.id
        listing.master_product_group_id = original_group.id
        listing.updated_at = now

        original_stock.master_product_group_id = original_group.id
        original_stock.is_group_controlled = True
        original_stock.group_controlled_at = original_stock.group_controlled_at or now
        original_stock.updated_at = now
        original_group.updated_at = now
        group.updated_at = now

        db.session.commit()

        old_group_push = _push_group_safely(group_id, source="product_linking_unlink_old_group_auto_push")
        restored_group_push = _push_group_safely(
            original_group.id,
            source="product_linking_unlink_restored_group_auto_push",
        )

        payload = _serialize_master_group(original_group)
        payload.update({
            "message": "Listing restored to its original product group.",
            "listing_id": int(listing_id),
            "previous_group_id": group_id,
            "group_id": original_group.id,
            "warehouse_stock_id": original_stock.id,
            "restored_original_group": True,
            "auto_push_attempted": True,
            "auto_push_success": (
                _push_succeeded(old_group_push)
                and _push_succeeded(restored_group_push)
            ),
            "push_results": {
                "previous_group": old_group_push,
                "restored_group": restored_group_push,
            },
            "refresh_required": True,
        })
        return _fresh_response(payload)

    stock = db.session.get(WarehouseStock, int(stock_id))
    if not stock or stock.master_product_group_id != group_id:
        return jsonify(_blocked(
            "Warehouse stock is not linked to this group.",
            group_id=group_id,
            stock_id=stock_id,
        )), 400

    stock.master_product_group_id = None
    stock.is_group_controlled = False
    stock.updated_at = now
    group.updated_at = now
    db.session.commit()

    payload = _serialize_master_group(group)
    payload.update({
        "message": "Warehouse stock was removed from the product group.",
        "warehouse_stock_id": int(stock_id),
        "refresh_required": True,
    })
    return _fresh_response(payload)


def _resolve_original_membership(listing, *, active_group_id: int):
    from extensions import db
    from models import MasterProductGroup, WarehouseStock

    sku = (getattr(listing, "external_sku", None) or "").strip()
    if not sku:
        return None, None

    stocks = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.sku == sku)
        .order_by(WarehouseStock.id.asc())
        .all()
    )

    for stock in stocks:
        stock_group_id = getattr(stock, "master_product_group_id", None)
        if stock_group_id and int(stock_group_id) != int(active_group_id):
            group = db.session.get(MasterProductGroup, int(stock_group_id))
            if group:
                return stock, group

    escaped_sku = sku.replace("%", "\\%").replace("_", "\\_")
    original_group = (
        db.session.query(MasterProductGroup)
        .filter(MasterProductGroup.id != int(active_group_id))
        .filter(MasterProductGroup.display_title.ilike(f"{escaped_sku}%", escape="\\"))
        .order_by(MasterProductGroup.id.asc())
        .first()
    )

    if original_group and stocks:
        return stocks[0], original_group

    return None, None


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


def _push_group_safely(group_id: int, *, source: str) -> dict:
    try:
        from services.governed_push_execution import push_group_listings

        return push_group_listings(
            group_id=int(group_id),
            actor=_actor(),
            source=source,
            actor_user=(
                current_user
                if current_user and current_user.is_authenticated
                else None
            ),
        )
    except Exception as exc:
        return {
            "success": False,
            "ok": False,
            "governed": True,
            "group_id": int(group_id),
            "reason": "product_linking_auto_push_exception",
            "error": str(exc),
        }


def _push_succeeded(result: dict) -> bool:
    return bool(result.get("ok") or result.get("success"))


def _fresh_response(payload: dict, status: int = 200):
    response = make_response(jsonify(payload), status)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Clear-Site-Data"] = '"storage"'
    return response


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
        "message": reason,
        "error": reason,
    }
    result.update(extra)
    return result
