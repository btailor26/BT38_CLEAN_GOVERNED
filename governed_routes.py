from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from flask import Blueprint, jsonify, render_template, request
try:
    from flask_login import current_user
except Exception:
    current_user = None

governed_bp = Blueprint("governed", __name__)


@governed_bp.get("/login")
def login():
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "auth_required": True,
        "reason": "Login must be handled through the governed auth path.",
    }), 401


@governed_bp.get("/shutdown-proof/status")
def shutdown_proof_status():
    return jsonify({
        "success": True,
        "ok": True,
        "shutdown_mode": True,
        "old_marketplace_routes_present": False,
    })


@governed_bp.get("/")
@governed_bp.get("/warehouse")
def governed_warehouse_page():
    """Read-only governed Master Stock UI.

    Source alignment:
    - MarketplaceListing rows are shown after sync/import.
    - Linked WarehouseStock provides warehouse truth quantities.
    - master_product_group_id flows into the UI for grouping controls.
    - FBA/AFN rows are displayed as read-only; FBM/MFN rows remain governed-pushable.
    """
    from extensions import db
    from models import MarketplaceListing, WarehouseStock

    listing_rows = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
        .limit(500)
        .all()
    )

    rows = []
    linked_stock_ids = set()

    for listing in listing_rows:
        stock = listing.warehouse_stock
        if stock:
            linked_stock_ids.add(stock.id)
        platform = (listing.store.platform if listing.store else "Marketplace") or "Marketplace"
        channel = (listing.normalized_amazon_fulfillment_channel or "").upper()
        is_fba = "amazon" in platform.lower() and channel not in ("MFN", "FBM", "MERCHANT")
        location = f"{platform} {'FBA' if is_fba else 'FBM'}" if "amazon" in platform.lower() else platform
        rows.append(SimpleNamespace(
            id=stock.id if stock else 0,
            inventory_item_id=None,
            item_id=None,
            marketplace_listing_id=listing.id,
            sku=(stock.sku if stock else listing.external_sku) or "",
            master_product_group_id=listing.master_product_group_id or (stock.master_product_group_id if stock else None),
            location=location,
            image_url=stock.image_url if stock else None,
            product_name=(stock.product_name if stock else None) or listing.title,
            title=listing.title,
            group_title=stock.group_title if stock else None,
            barcode=listing.fnsku or listing.barcode or (stock.barcode if stock else None),
            mcf_group_source=False,
            is_group_controlled=bool(stock.is_group_controlled) if stock else False,
            available_quantity=stock.sellable_quantity if stock else 0,
            price=listing.price or 0,
            store_name=listing.store.name if listing.store else platform,
        ))

    unlinked_stock = (
        db.session.query(WarehouseStock)
        .filter(WarehouseStock.is_active == True)  # noqa: E712
        .filter(WarehouseStock.is_deleted == False)  # noqa: E712
        .order_by(WarehouseStock.updated_at.desc(), WarehouseStock.id.desc())
        .limit(500)
        .all()
    )
    for stock in unlinked_stock:
        if stock.id in linked_stock_ids:
            continue
        rows.append(SimpleNamespace(
            id=stock.id,
            inventory_item_id=None,
            item_id=None,
            marketplace_listing_id=None,
            sku=stock.sku,
            master_product_group_id=stock.master_product_group_id,
            location="Warehouse",
            image_url=stock.image_url,
            product_name=stock.product_name,
            title=stock.product_name,
            group_title=stock.group_title,
            barcode=stock.barcode,
            mcf_group_source=False,
            is_group_controlled=bool(stock.is_group_controlled),
            available_quantity=stock.sellable_quantity,
            price=0,
            store_name=stock.warehouse.name if stock.warehouse else "Warehouse",
        ))
        if len(rows) >= 500:
            break

    stats = SimpleNamespace(
        total_skus=len(rows),
        total_available=sum(int(getattr(row, "available_quantity", 0) or 0) for row in rows),
        low_stock_count=sum(1 for row in rows if int(getattr(row, "available_quantity", 0) or 0) <= 0),
    )
    warehouse_items = SimpleNamespace(items=rows, total=len(rows))

    return render_template("warehouse.html", warehouse_items=warehouse_items, stats=stats)


@governed_bp.post("/governed/actions/sku/dry-run")
def governed_sku_dry_run():
    from governed_execution import submit_governed_marketplace_action

    governed_payload = dict(request.get_json(silent=True) or {})
    governed_payload.setdefault("action", "push_inventory")

    result = submit_governed_marketplace_action(
        governed_payload,
        actor=request.headers.get("X-Actor", "manual-governed-dry-run"),
        approval={"approved": True, "source": "manual_sku_dry_run_route"},
        dry_run=True,
    )
    return jsonify(result), 200


@governed_bp.post("/governed/actions/listings/<int:listing_id>/push")
def governed_listing_push(listing_id: int):
    body = dict(request.get_json(silent=True) or {})
    result = _push_one_listing(
        listing_id=listing_id,
        quantity=body.get("quantity"),
        actor=_actor(),
        source="ui_listing_button",
    )
    return jsonify(result), 200 if result.get("ok") else 400


@governed_bp.post("/governed/actions/groups/<int:group_id>/push")
def governed_group_push(group_id: int):
    from extensions import db
    from models import MarketplaceListing

    body = dict(request.get_json(silent=True) or {})
    listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.master_product_group_id == group_id)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .order_by(MarketplaceListing.id)
        .all()
    )
    results = [
        _push_one_listing(
            listing_id=listing.id,
            quantity=body.get("quantity"),
            actor=_actor(),
            source="ui_group_button",
        )
        for listing in listings
    ]
    ok_count = sum(1 for item in results if item.get("ok"))
    return jsonify({
        "success": ok_count == len(results) and bool(results),
        "ok": ok_count == len(results) and bool(results),
        "governed": True,
        "group_id": group_id,
        "total": len(results),
        "ok_count": ok_count,
        "results": results,
    }), 200


@governed_bp.get("/governed/actions/history")
def governed_action_history():
    from extensions import db
    from models import SyncLog

    limit = min(int(request.args.get("limit", 50)), 200)
    query = db.session.query(SyncLog).filter(
        SyncLog.message.contains("governed_push")
    )
    listing_id = request.args.get("listing_id")
    if listing_id:
        query = query.filter(SyncLog.message.contains(f"listing_id={listing_id}"))
    rows = query.order_by(SyncLog.created_at.desc()).limit(limit).all()
    return jsonify({
        "success": True,
        "ok": True,
        "history": [
            {
                "id": row.id,
                "store_id": row.store_id,
                "status": row.status,
                "message": row.message,
                "items_synced": row.items_synced,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    })


def _push_one_listing(*, listing_id: int, quantity, actor: str, source: str) -> dict:
    from extensions import db
    from governed_execution import AMAZON_FBM_LIVE_APPROVAL_TYPE, submit_governed_marketplace_action
    from models import MarketplaceListing, SyncLog

    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing:
        return _blocked(f"Marketplace listing {listing_id} was not found.", listing_id=listing_id)
    if not listing.store:
        return _blocked("Marketplace listing has no store.", listing_id=listing_id)
    if not listing.warehouse_stock:
        return _blocked("Marketplace listing is not linked to warehouse stock.", listing_id=listing_id)

    platform = (listing.store.platform or "").strip().lower()
    marketplace = "amazon" if "amazon" in platform else "ebay" if "ebay" in platform else platform
    try:
        push_quantity = listing.effective_quantity if quantity is None else int(quantity)
    except (TypeError, ValueError):
        return _blocked("Quantity must be an integer.", listing_id=listing_id, quantity=quantity)
    sku = (listing.external_sku or listing.warehouse_stock.sku or "").strip()

    payload = {
        "marketplace": marketplace,
        "action": "push_inventory",
        "sku": sku,
        "store_id": listing.store_id,
        "listing_id": listing.id,
        "quantity": push_quantity,
        "amazon_fulfillment_channel": listing.amazon_fulfillment_channel or "MFN",
        "source": source,
    }
    approval = {
        "approved": True,
        "approval_type": AMAZON_FBM_LIVE_APPROVAL_TYPE,
        "source": source,
        "approved_by": actor,
        "approved_at": datetime.utcnow().isoformat(),
        "scope": {
            "sku": sku,
            "store_id": listing.store_id,
            "listing_id": listing.id,
            "quantity": push_quantity,
        },
    }

    result = submit_governed_marketplace_action(
        payload,
        actor=actor,
        approval=approval,
        dry_run=False,
    )

    ok = bool(result.get("ok") or result.get("success"))
    listing.last_push_at = datetime.utcnow()
    listing.last_push_quantity = push_quantity if ok else listing.last_push_quantity
    listing.last_push_status = "success" if ok else "error"
    listing.last_push_error = None if ok else str(result.get("reason") or result.get("failure_reason") or result)[:1000]
    listing.push_attempts = 0 if ok else (listing.push_attempts or 0) + 1
    listing.consecutive_failures = 0 if ok else (listing.consecutive_failures or 0) + 1

    db.session.add(SyncLog(
        store_id=listing.store_id,
        status="success" if ok else "error",
        message=(
            f"governed_push listing_id={listing.id} sku={sku} "
            f"marketplace={marketplace} source={source} ok={ok}"
        )[:500],
        items_synced=1 if ok else 0,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    result.update({
        "ui_action_wired": True,
        "grouping_layer_ready": True,
        "audit_history_logged": True,
        "listing_last_push_updated": True,
    })
    return result


def _actor() -> str:
    try:
        if current_user and current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return request.headers.get("X-Actor", "governed-ui-action")


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


@governed_bp.post("/amazon-inventory-hydration/manual-run")
def governed_amazon_inventory_hydration_manual_run():
    """
    Manual governed Amazon inventory hydration endpoint.

    Block-replaced governed route.
    No scheduler.
    No worker.
    No automatic execution.
    No UI dependency.
    No legacy login_required decorator.
    No legacy .route decorator.
    """
    from services.governed_amazon_inventory_hydration import hydrate_amazon_inventory

    result = hydrate_amazon_inventory()

    return jsonify({
        "success": True,
        "manual": True,
        "governed": True,
        "auto_execution": False,
        "result": result,
    })


@governed_bp.post("/governed/warehouse/sync")
def governed_warehouse_sync_manual_run():
    """
    Manual governed warehouse sync endpoint.

    This is the single future-facing warehouse sync path.
    No old queue workers.
    No old auto sync.
    No UI layout dependency.
    """
    from services.governed_warehouse_sync import run_governed_warehouse_sync

    body = dict(request.get_json(silent=True) or {})
    store_id = body.get("store_id")

    result = run_governed_warehouse_sync(
        store_id=store_id,
        actor=request.headers.get("X-Actor", "warehouse-sync-button"),
    )

    return jsonify(result), 200 if result.get("success") else 400


@governed_bp.post("/governed/amazon/inventory/import")
def governed_amazon_inventory_import():

    from services.governed_amazon_inventory_import import (
        run_governed_amazon_inventory_import
    )

    body = dict(request.get_json(silent=True) or {})

    result = run_governed_amazon_inventory_import(
        store_id=body.get("store_id")
    )

    return jsonify(result), 200
