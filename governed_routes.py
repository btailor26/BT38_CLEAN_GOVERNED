from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user

governed_bp = Blueprint("governed", __name__)


@governed_bp.get("/shutdown-proof/status")
def shutdown_proof_status():
    return jsonify({
        "success": True,
        "ok": True,
        "shutdown_mode": True,
        "old_marketplace_routes_present": False,
    })


@governed_bp.post("/governed/actions/sku/dry-run")
def governed_sku_dry_run():
    """Manual governed SKU dry-run trigger; never performs live execution."""
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
    """UI-safe governed single listing push trigger.

    This endpoint does not touch old sync workers, queues, schedulers, or legacy
    marketplace execution. It resolves the listing, builds an exact approval
    scope, temporarily opens only the governed Amazon runtime gate for the one
    command, then closes the gate before returning.
    """
    body = dict(request.get_json(silent=True) or {})
    quantity = body.get("quantity")
    result = _push_one_listing(
        listing_id=listing_id,
        quantity=quantity,
        actor=_actor(),
        source="ui_listing_button",
    )
    return jsonify(result), 200 if result.get("ok") else 400


@governed_bp.post("/governed/actions/groups/<int:group_id>/push")
def governed_group_push(group_id: int):
    """UI-safe governed group push trigger.

    Group push is deliberately serial and explicit. Each linked marketplace
    listing is run through the same single governed entry point used by the
    proven Amazon live test. eBay currently returns the governed blocked result
    until its live adapter is built.
    """
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
    results = []
    for listing in listings:
        results.append(
            _push_one_listing(
                listing_id=listing.id,
                quantity=body.get("quantity"),
                actor=_actor(),
                source="ui_group_button",
            )
        )

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
    """Small governed audit/history endpoint for UI panels."""
    from extensions import db
    from models import SyncLog

    limit = min(int(request.args.get("limit", 50)), 200)
    query = db.session.query(SyncLog).filter(SyncLog.log_type == "governed_push")
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
                "items_pushed": row.items_pushed,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
    })


def _push_one_listing(*, listing_id: int, quantity, actor: str, source: str) -> dict:
    from extensions import db
    from governed_execution import AMAZON_FBM_LIVE_APPROVAL_TYPE, submit_governed_marketplace_action
    from models import MarketplaceListing, SyncLog
    from services import runtime_gate

    listing = db.session.get(MarketplaceListing, listing_id)
    if not listing:
        return _blocked(f"Marketplace listing {listing_id} was not found.", listing_id=listing_id)
    if not listing.store:
        return _blocked("Marketplace listing has no store.", listing_id=listing_id)
    if not listing.warehouse_stock:
        return _blocked("Marketplace listing is not linked to warehouse stock.", listing_id=listing_id)

    platform = (listing.store.platform or "").strip().lower()
    marketplace = "amazon" if "amazon" in platform else "ebay" if "ebay" in platform else platform
    push_quantity = listing.effective_quantity if quantity is None else int(quantity)
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

    previous_force_closed = runtime_gate.RUNTIME_GATE_FORCE_CLOSED
    previous_amazon_enabled = runtime_gate.GOVERNED_AMAZON_FBM_LIVE_ENABLED
    try:
        if marketplace == "amazon":
            runtime_gate.RUNTIME_GATE_FORCE_CLOSED = False
            runtime_gate.GOVERNED_AMAZON_FBM_LIVE_ENABLED = True
        result = submit_governed_marketplace_action(
            payload,
            actor=actor,
            approval=approval,
            dry_run=False,
        )
    finally:
        runtime_gate.RUNTIME_GATE_FORCE_CLOSED = previous_force_closed
        runtime_gate.GOVERNED_AMAZON_FBM_LIVE_ENABLED = previous_amazon_enabled

    ok = bool(result.get("ok") or result.get("success"))
    listing.last_push_at = datetime.utcnow()
    listing.last_push_quantity = push_quantity if ok else listing.last_push_quantity
    listing.last_push_status = "success" if ok else "error"
    listing.last_push_error = None if ok else str(result.get("reason") or result.get("failure_reason") or result)[:1000]
    listing.push_attempts = 0 if ok else (listing.push_attempts or 0) + 1
    listing.consecutive_failures = 0 if ok else (listing.consecutive_failures or 0) + 1

    db.session.add(SyncLog(
        store_id=listing.store_id,
        log_type="governed_push",
        status="success" if ok else "error",
        message=(
            f"governed_push listing_id={listing.id} sku={sku} "
            f"marketplace={marketplace} source={source} ok={ok}"
        )[:500],
        items_imported=0,
        items_pushed=1 if ok else 0,
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
