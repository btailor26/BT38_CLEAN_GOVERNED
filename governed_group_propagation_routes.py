from __future__ import annotations

from datetime import datetime

from flask import Blueprint, jsonify, request
try:
    from flask_login import current_user
except Exception:
    current_user = None


governed_group_propagation_bp = Blueprint("governed_group_propagation", __name__)


# Disabled duplicate destructive unlink route.
# Single unlink authority lives in governed_group_routes.py.
# This blueprint owns propagation only.

@governed_group_propagation_bp.post(
    "/governed/groups/<int:group_id>/unlink-disabled"
)
def governed_group_unlink_listing_disabled(group_id: int):
    """Retired duplicate relationship writer.

    Link and unlink relationship mutations are owned only by
    governed_group_routes.py. This blueprint owns quantity propagation.
    """
    return jsonify({
        "success": False,
        "ok": False,
        "governed": True,
        "execution_blocked": True,
        "reason": "legacy_product_linking_disabled",
        "group_id": group_id,
        "message": (
            "Duplicate unlink route is retired. "
            "Use POST /governed/groups/<group_id>/unlink."
        ),
        "full_page_refresh": False,
        "full_dataset_refresh": False,
    }), 409

def run_governed_group_propagation(
    group_id: int,
    *,
    payload=None,
):
    """Propagate warehouse truth quantity to pushable marketplace listings.

    Locked rules:
    - WarehouseStock.sellable_quantity is the authority.
    - A validated Warehouse shortcut row supplies one target quantity for the
      entire requested Product Linking group.
    - Amazon FBA/AFN is skipped before push runtime.
    - MCF remains FBA-only visibility/fulfilment routing, not a stock push path.
    - FBM/MFN and non-Amazon pushable marketplace rows may be pushed.
    - No old workers, schedulers, or legacy routes are used.
    """
    from extensions import db
    from governed_execution import AMAZON_FBM_LIVE_APPROVAL_TYPE, submit_governed_marketplace_action
    from models import (
        MarketplaceListing,
        MasterProductGroup,
        SyncLog,
        WarehouseStock,
    )

    body = dict(payload or {})
    dry_run = bool(body.get("dry_run", False))
    requested_warehouse_stock_id = body.get("warehouse_stock_id")

    group = db.session.get(MasterProductGroup, group_id)
    if not group:
        return jsonify(_blocked("Master product group was not found.", group_id=group_id)), 404

    requested_stock = None
    target_quantity = None
    if requested_warehouse_stock_id not in (None, ""):
        try:
            requested_stock_id = int(requested_warehouse_stock_id)
        except (TypeError, ValueError):
            return jsonify(_blocked("warehouse_stock_id must be an integer when provided.", group_id=group_id)), 400

        requested_stock = db.session.get(
            WarehouseStock,
            requested_stock_id,
        )

        if requested_stock is None:
            return jsonify(
                _blocked(
                    "Warehouse shortcut row was not found.",
                    group_id=group_id,
                    warehouse_stock_id=requested_stock_id,
                )
            ), 404

        if not bool(getattr(requested_stock, "is_active", False)):
            return jsonify(
                _blocked(
                    "Warehouse shortcut row is inactive.",
                    group_id=group_id,
                    warehouse_stock_id=requested_stock_id,
                )
            ), 409

        requested_membership = (
            db.session.query(MarketplaceListing.id)
            .filter(MarketplaceListing.master_product_group_id == group_id)
            .filter(
                MarketplaceListing.warehouse_stock_id
                == requested_stock_id
            )
            .first()
        )

        if requested_membership is None:
            return jsonify(
                _blocked(
                    "Warehouse shortcut row does not belong to the "
                    "requested Product Linking group.",
                    group_id=group_id,
                    warehouse_stock_id=requested_stock_id,
                    original_group_id=(
                        requested_stock.master_product_group_id
                    ),
                )
            ), 409

        # The shortcut reports relationship identity only. Once the selected
        # Warehouse row is proven to belong to the current Product Linking
        # group, its saved sellable quantity becomes the single group target.
        target_quantity = int(
            getattr(requested_stock, "sellable_quantity", 0) or 0
        )

    # Current Product Linking membership belongs to MarketplaceListing.
    # Permanent Warehouse identity remains warehouse_stock_id.
    listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .filter(
            MarketplaceListing.master_product_group_id
            == group_id
        )
        .filter(
            MarketplaceListing.warehouse_stock_id.isnot(None)
        )
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

            # Read-only marketplace sources are not failures. Record the
            # classification clearly and never send a marketplace action.
            if classification["is_fba"]:
                listing.last_push_at = datetime.utcnow()
                listing.last_push_status = "read_only"
                listing.last_push_error = None
                listing.push_attempts = 0
                listing.consecutive_failures = 0

            results.append({
                "listing_id": listing.id,
                "warehouse_stock_id": listing.warehouse_stock_id,
                "sku": listing.external_sku,
                "status": (
                    "read_only"
                    if classification["is_fba"]
                    else "skipped"
                ),
                "reason": classification["reason"],
                "is_fba": classification["is_fba"],
                "is_pushable": False,
            })
            continue

        # Product Linking/Warehouse shortcut: every writable member receives
        # the one quantity resolved from the validated selected Warehouse row.
        # Other callers without a selected row retain their exact-row fallback.
        quantity = (
            int(target_quantity)
            if target_quantity is not None
            else int(
                getattr(
                    listing.warehouse_stock,
                    "sellable_quantity",
                    0,
                )
                or 0
            )
        )

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
            payload=payload,
            actor=_actor(),
            approval_type=(approval or {}).get("approval_type"),
            approval_id=(approval or {}).get("approval_id"),
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
            "warehouse_stock_id": listing.warehouse_stock_id,
            "sku": sku,
            "marketplace": marketplace,
            "quantity": quantity,
            "status": "pushed" if ok else "failed",
            "dry_run": dry_run,
            "result": result,
        })

    sync_store_id = None
    for listing in listings:
        if getattr(listing, "store_id", None):
            sync_store_id = listing.store_id
            break

    if sync_store_id is not None:
        db.session.add(SyncLog(
            store_id=sync_store_id,
            status="success" if failed == 0 else "error",
            message=(
                f"governed_group_propagation group_id={group_id} "
                f"warehouse_stock_id={requested_warehouse_stock_id} "
                f"target_quantity={target_quantity} "
                f"pushed={pushed} skipped={skipped} failed={failed} dry_run={dry_run}"
            )[:500],
            items_synced=pushed,
            created_at=datetime.utcnow(),
        ))
    db.session.commit()

    affected_listing_ids = [int(listing.id) for listing in listings]
    affected_warehouse_stock_ids = sorted({
        int(listing.warehouse_stock_id)
        for listing in listings
        if listing.warehouse_stock_id is not None
    })

    return jsonify({
        "success": failed == 0,
        "ok": failed == 0,
        "governed": True,
        "changed": True,
        "group_id": group_id,
        "warehouse_stock_id": (
            int(requested_warehouse_stock_id)
            if requested_warehouse_stock_id not in (None, "")
            else None
        ),
        "target_quantity": target_quantity,
        "dry_run": dry_run,
        "total_listings": len(listings),
        "pushed": pushed,
        "skipped": skipped,
        "failed": failed,
        "affected_group_ids": [int(group_id)],
        "affected_listing_ids": affected_listing_ids,
        "affected_warehouse_stock_ids": affected_warehouse_stock_ids,
        "results": results,
    }), 200 if failed == 0 else 400

@governed_group_propagation_bp.post(
    "/governed/groups/<int:group_id>/propagate-quantity"
)
def governed_group_propagate_quantity(group_id: int):
    """Thin HTTP adapter into the shared Warehouse group process."""
    return run_governed_group_propagation(
        group_id,
        payload=request.get_json(silent=True) or {},
    )



def _classify_listing(listing) -> dict:
    platform = (listing.store.platform or "").strip().lower() if listing.store else ""
    channel = (listing.normalized_amazon_fulfillment_channel or "").upper()
    is_amazon = "amazon" in platform
    explicit_fba = bool(getattr(listing, "is_fba", False))
    is_fbm = (
        is_amazon
        and not explicit_fba
        and channel in ("MFN", "FBM", "MERCHANT")
    )
    is_fba = is_amazon and (explicit_fba or not is_fbm)
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

    is_group_child = bool(
        listing.master_product_group_id
    )
    is_non_amazon_group_child = bool(is_group_child and not is_amazon)

    if not listing.is_pushable and not is_non_amazon_group_child:
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
    from flask import has_request_context

    try:
        if current_user and current_user.is_authenticated:
            return f"user:{current_user.id}"
    except Exception:
        pass

    if not has_request_context():
        return "governed-group-propagation"

    return request.headers.get(
        "X-Actor",
        "governed-group-propagation",
    )


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
