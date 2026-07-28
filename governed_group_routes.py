from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, make_response, request

try:
    from flask_login import current_user
except Exception:
    current_user = None

from legacy_product_linking_guard import block_legacy_product_linking_request


governed_group_bp = Blueprint("governed_groups", __name__)


@governed_group_bp.before_app_request
def governed_product_linking_single_writer_guard():
    """Block every retired Product Linking writer before route dispatch."""
    return block_legacy_product_linking_request()


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
        "groups": [
            _serialize_master_group(group, include_children=False)["group"]
            for group in groups
        ],
    })


@governed_group_bp.post("/governed/groups/create")
def governed_group_create():
    from extensions import db
    from models import MasterProductGroup

    body = dict(request.get_json(silent=True) or {})
    title = (
        body.get("display_title")
        or body.get("title")
        or ""
    ).strip() or "Untitled Master Group"
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
            return jsonify(result), 409

    if listing_id:
        result = _link_listing_to_group(group, int(listing_id), actor=_actor())
        if not result.get("ok"):
            db.session.rollback()
            return jsonify(result), 409

    db.session.commit()
    payload = _serialize_master_group(group)
    payload.update(_change_contract(
        changed=True,
        group_ids=[group.id],
        stock_ids=[warehouse_stock_id] if warehouse_stock_id else [],
        listing_ids=[listing_id] if listing_id else [],
    ))
    return _targeted_response(payload, 201)


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
        db.session.rollback()
        return jsonify(result), 409

    changed = bool(result.get("changed"))
    if changed:
        db.session.commit()
    else:
        db.session.rollback()

    payload = _serialize_master_group(group)
    payload.update(_change_contract(
        changed=changed,
        group_ids=[group.id] if changed else [],
        stock_ids=[stock_id] if changed else [],
    ))
    return _targeted_response(payload)


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
        db.session.rollback()
        return jsonify(result), 409

    changed = bool(result.get("changed"))
    if not changed:
        db.session.rollback()
        payload = _serialize_master_group(group)
        payload.update({
            "message": "Listing is already linked to this group.",
            "original_group_id": result.get("original_group_id"),
            "auto_push_attempted": False,
            "auto_push_success": True,
            **_change_contract(changed=False),
        })
        return _targeted_response(payload)

    db.session.commit()
    push_result = _push_group_safely(group.id, source="product_linking_auto_push")
    payload = _serialize_master_group(group)
    payload.update({
        "message": "Listing linked while preserving its permanent original group.",
        "original_group_id": result.get("original_group_id"),
        "auto_push_attempted": True,
        "auto_push_success": _push_succeeded(push_result),
        "push_result": push_result,
        **_change_contract(
            changed=True,
            group_ids=[group.id, result.get("original_group_id")],
            stock_ids=[result.get("warehouse_stock_id")],
            listing_ids=[listing_id],
        ),
    })
    return _targeted_response(payload)


@governed_group_bp.post("/governed/groups/<int:group_id>/unlink")
def governed_group_unlink(group_id: int):
    """Restore one mutable listing to its permanent original group."""
    from extensions import db
    from models import MarketplaceListing, MasterProductGroup

    body = dict(request.get_json(silent=True) or {})
    group = db.session.get(MasterProductGroup, group_id)
    if not group:
        return jsonify(_blocked("Master product group was not found.", group_id=group_id)), 404

    listing_id = body.get("listing_id") or body.get("marketplace_listing_id")
    if not listing_id:
        return jsonify(_blocked("listing_id is required.", group_id=group_id)), 400

    listing = db.session.get(MarketplaceListing, int(listing_id))
    if not listing:
        return jsonify(_blocked(
            "Marketplace listing was not found.",
            group_id=group_id,
            listing_id=listing_id,
        )), 404

    if int(listing.master_product_group_id or 0) != int(group_id):
        return jsonify(_blocked(
            "Marketplace listing is not linked to this group.",
            group_id=group_id,
            listing_id=listing_id,
            current_group_id=listing.master_product_group_id,
        )), 409

    if bool(getattr(listing, "is_fba", False)):
        return jsonify(_blocked(
            "FBA/AFN listings are read-only. Unlink a mutable listing from the group instead.",
            group_id=group_id,
            listing_id=listing_id,
            fba_read_only=True,
        )), 409

    original_stock = listing.warehouse_stock
    if not original_stock:
        return jsonify(_blocked(
            "The listing has no permanent warehouse product identity.",
            group_id=group_id,
            listing_id=listing_id,
        )), 409

    original_group_id = original_stock.master_product_group_id
    if not original_group_id:
        return jsonify(_blocked(
            "Warehouse product has no permanent original group ID.",
            group_id=group_id,
            listing_id=listing_id,
            warehouse_stock_id=original_stock.id,
        )), 409

    original_group = db.session.get(MasterProductGroup, int(original_group_id))
    if not original_group:
        return jsonify(_blocked(
            "The permanent original group no longer exists.",
            group_id=group_id,
            listing_id=listing_id,
            original_group_id=original_group_id,
            warehouse_stock_id=original_stock.id,
        )), 409

    previous_group_id = int(group_id)
    resulting_group_id = int(original_group_id)
    if resulting_group_id == previous_group_id:
        return jsonify(_blocked(
            "Listing is already in its permanent original group; no unlink mutation is required.",
            group_id=group_id,
            listing_id=listing_id,
            original_group_id=resulting_group_id,
            warehouse_stock_id=original_stock.id,
        )), 409

    now = datetime.utcnow()
    listing.master_product_group_id = resulting_group_id
    listing.updated_at = now
    group.updated_at = now
    original_group.updated_at = now
    db.session.commit()

    previous_group_push = _push_group_safely(
        previous_group_id,
        source="product_linking_unlink_previous_group_auto_push",
    )
    restored_group_push = _push_group_safely(
        resulting_group_id,
        source="product_linking_unlink_original_group_auto_push",
    )

    payload = _serialize_master_group(original_group)
    payload.update({
        "message": "Listing restored to its permanent original group ID.",
        "listing_id": int(listing_id),
        "previous_group_id": previous_group_id,
        "group_id": resulting_group_id,
        "original_group_id": resulting_group_id,
        "warehouse_stock_id": original_stock.id,
        "restored_original_group": True,
        "released_to_unlinked": False,
        "auto_push_attempted": True,
        "auto_push_success": (
            _push_succeeded(previous_group_push)
            and _push_succeeded(restored_group_push)
        ),
        "push_results": {
            "previous_group": previous_group_push,
            "restored_original_group": restored_group_push,
        },
        **_change_contract(
            changed=True,
            group_ids=[previous_group_id, resulting_group_id],
            stock_ids=[original_stock.id],
            listing_ids=[listing_id],
        ),
    })
    return _targeted_response(payload)


def _link_stock_to_group(group, stock_id: int, actor: str) -> dict:
    """Assign an original group once. A warehouse product's group ID is immutable."""
    from extensions import db
    from models import WarehouseStock

    stock = db.session.get(WarehouseStock, stock_id)
    if not stock:
        return _blocked("Warehouse stock was not found.", stock_id=stock_id)

    current_group_id = int(stock.master_product_group_id or 0)
    if current_group_id and current_group_id != int(group.id):
        return _blocked(
            "Warehouse product already owns a different permanent original group.",
            stock_id=stock_id,
            warehouse_stock_id=stock_id,
            original_group_id=current_group_id,
            requested_group_id=group.id,
            original_group_immutable=True,
        )

    changed = not current_group_id or not bool(stock.is_group_controlled)
    if changed:
        now = datetime.utcnow()
        stock.master_product_group_id = group.id
        stock.is_group_controlled = True
        stock.group_controlled_at = stock.group_controlled_at or now
        stock.updated_at = now

        if not group.display_title:
            group.display_title = (
                stock.product_name
                or stock.group_title
                or stock.sku
                or "Untitled Master Group"
            )[:500]
        if not group.display_image_url and stock.image_url:
            group.display_image_url = stock.image_url
        group.updated_at = now

    return {
        "success": True,
        "ok": True,
        "governed": True,
        "changed": changed,
        "stock_id": stock_id,
        "warehouse_stock_id": stock_id,
        "group_id": group.id,
        "original_group_id": group.id,
    }


def _link_listing_to_group(group, listing_id: int, actor: str) -> dict:
    """Move only the listing's active group; never move its warehouse original group."""
    from extensions import db
    from models import MarketplaceListing, MasterProductGroup

    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing:
        return _blocked("Marketplace listing was not found.", listing_id=listing_id)

    stock = listing.warehouse_stock
    if not stock:
        return _blocked(
            "Listing must be linked to a warehouse product before group linking.",
            listing_id=listing_id,
        )

    original_group_id = stock.master_product_group_id
    if not original_group_id:
        return _blocked(
            "Warehouse product has no permanent original group ID.",
            listing_id=listing_id,
            warehouse_stock_id=stock.id,
        )

    if not db.session.get(MasterProductGroup, int(original_group_id)):
        return _blocked(
            "Warehouse product's permanent original group does not exist.",
            listing_id=listing_id,
            warehouse_stock_id=stock.id,
            original_group_id=original_group_id,
        )

    changed = int(listing.master_product_group_id or 0) != int(group.id)
    if changed:
        now = datetime.utcnow()
        listing.master_product_group_id = group.id
        listing.updated_at = now
        if not group.display_title:
            group.display_title = (
                listing.title
                or listing.external_sku
                or "Untitled Master Group"
            )[:500]
        group.updated_at = now

    return {
        "success": True,
        "ok": True,
        "governed": True,
        "changed": changed,
        "listing_id": listing_id,
        "warehouse_stock_id": stock.id,
        "group_id": group.id,
        "original_group_id": int(original_group_id),
    }


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


def _normalise_ids(values):
    result = []
    seen = set()
    for value in values or []:
        if value in (None, "", 0, "0"):
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            continue
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _change_contract(*, changed: bool, group_ids=None, stock_ids=None, listing_ids=None) -> dict:
    return {
        "changed": bool(changed),
        "refresh_required": bool(changed),
        "refresh_scope": "affected_rows" if changed else "none",
        "affected_group_ids": _normalise_ids(group_ids),
        "affected_warehouse_stock_ids": _normalise_ids(stock_ids),
        "affected_listing_ids": _normalise_ids(listing_ids),
        "full_page_refresh": False,
        "full_dataset_refresh": False,
        "cache_clear_required": False,
    }


def _targeted_response(payload: dict, status: int = 200):
    response = make_response(jsonify(payload), status)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
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
                "original_group_id": stock.master_product_group_id,
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
                "active_group_id": listing.master_product_group_id,
                "original_group_id": (
                    listing.warehouse_stock.master_product_group_id
                    if listing.warehouse_stock
                    else None
                ),
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
        **_change_contract(changed=False),
    }
    result.update(extra)
    return result
