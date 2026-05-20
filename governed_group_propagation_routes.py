from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
try:
    from flask_login import current_user
except Exception:
    current_user = None


governed_group_propagation_bp = Blueprint("governed_group_propagation", __name__)


@governed_group_propagation_bp.post("/governed/groups/<int:group_id>/propagate-quantity")
def governed_group_propagate_quantity(group_id: int):
    """Propagate warehouse truth quantity to pushable marketplace listings.

    Locked rules:
    - WarehouseStock.sellable_quantity is the authority.
    - Amazon FBA/AFN is skipped before push runtime.
    - MCF remains FBA-only visibility/fulfilment routing, not a stock push path.
    - FBM/MFN and non-Amazon pushable marketplace rows may be pushed.
    - No old workers, schedulers, or legacy routes are used.
    """
    from extensions import db
    from governed_execution import AMAZON_FBM_LIVE_APPROVAL_TYPE, submit_governed_marketplace_action
    from models import MarketplaceListing, MasterProductGroup, SyncLog

    body = dict(request.get_json(silent=True) or {})
    dry_run = bool(body.get("dry_run", False))
    requested_quantity = body.get("quantity")

    group = db.session.get(MasterProductGroup, group_id)
    if not group:
        return jsonify(_blocked("Master product group was not found.", group_id=group_id)), 404

    listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.master_product_group_id == group_id)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .order_by(MarketplaceListing.id)
        .all()
    )

    results = []
    pushed = 0
    skipped = 0
    failed = 0

    for listing in listings:
        classification = _classify_listing(listing)
        if classification["skip"]:
            skipped += 1
            results.append({
                "listing_id": listing.id,
                "sku": listing.external_sku,
                "status": "skipped",
                "reason": classification["reason"],
                "is_fba": classification["is_fba"],
                "is_pushable": False,
            })
            continue

        if requested_quantity is None:
            quantity = listing.effective_quantity
        else:
            try:
                quantity = int(requested_quantity)
            except (TypeError, ValueError):
                return jsonify(_blocked("quantity must be an integer when provided.", group_id=group_id)), 400

        sku = (listing.external_sku or (listing.warehouse_stock.sku if listing.warehouse_stock else "") or "").strip()
        marketplace = classification["marketplace"]

        payload = {
            "marketplace": marketplace,
            "action": "push_inventory",
            "sku": sku,
            "store_id": listing.store_id,
            "listing_id": listing.id,
            "quantity": quantity,
            "amazon_fulfillment_channel": listing.amazon_fulfillment_channel or "MFN",
            "source": "governed_group_propagation",
            "group_id": group_id,
        }
        approval = {
            "approved": True,
            "approval_type": AMAZON_FBM_LIVE_APPROVAL_TYPE,
            "source": "governed_group_propagation",
            "approved_by": _actor(),
            "approved_at": datetime.utcnow().isoformat(),
            "scope": {
                "group_id": group_id,
                "listing_id": listing.id,
                "sku": sku,
                "store_id": listing.store_id,
                "quantity": quantity,
            },
        }

        result = submit_governed_marketplace_action(
            payload,
            actor=_actor(),
            approval=approval,
            dry_run=dry_run,
        )
        ok = bool(result.get("ok") or result.get("success"))

        listing.last_push_at = datetime.utcnow()
        listing.last_push_quantity = quantity if ok else listing.last_push_quantity
        listing.last_push_status = "success" if ok else "error"
        listing.last_push_error = None if ok else str(result.get("reason") or result.get("failure_reason") or result)[:1000]
        listing.push_attempts = 0 if ok else (listing.push_attempts or 0) + 1
        listing.consecutive_failures = 0 if ok else (listing.consecutive_failures or 0) + 1

        if ok:
            pushed += 1
        else:
            failed += 1

        results.append({
            "listing_id": listing.id,
            "sku": sku,
            "marketplace": marketplace,
            "quantity": quantity,
            "status": "pushed" if ok else "failed",
            "dry_run": dry_run,
            "result": result,
        })

    db.session.add(SyncLog(
        store_id=None,
        status="success" if failed == 0 else "error",
        message=(
            f"governed_group_propagation group_id={group_id} "
            f"pushed={pushed} skipped={skipped} failed={failed} dry_run={dry_run}"
        )[:500],
        items_synced=pushed,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()

    return jsonify({
        "success": failed == 0,
        "ok": failed == 0,
        "governed": True,
        "group_id": group_id,
        "dry_run": dry_run,
        "total_listings": len(listings),
        "pushed": pushed,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }), 200 if failed == 0 else 400


def _classify_listing(listing) -> dict:
    platform = (listing.store.platform or "").strip().lower() if listing.store else ""
    channel = (listing.normalized_amazon_fulfillment_channel or "").upper()
    is_amazon = "amazon" in platform
    is_fbm = is_amazon and channel in ("MFN", "FBM", "MERCHANT")
    is_fba = is_amazon and not is_fbm
    marketplace = "amazon" if is_amazon else "ebay" if "ebay" in platform else platform

    if is_fba:
        return {
            "marketplace": marketplace,
            "is_fba": True,
            "skip": True,
            "reason": "Amazon FBA/AFN is read-only. MCF may use FBA stock, but propagation must not push FBA quantity.",
        }
    if not listing.warehouse_stock:
        return {
            "marketplace": marketplace,
            "is_fba": False,
            "skip": True,
            "reason": "Listing is not linked to warehouse stock, so warehouse truth quantity cannot be propagated.",
        }
    if not listing.is_pushable:
        return {
            "marketplace": marketplace,
            "is_fba": False,
            "skip": True,
            "reason": "Listing is not pushable under current listing state.",
        }

    return {
        "marketplace": marketplace,
        "is_fba": False,
        "skip": False,
        "reason": "pushable",
    }


def _actor() -> str:
    try:
        if current_user and current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass
    return request.headers.get("X-Actor", "governed-group-propagation")


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
