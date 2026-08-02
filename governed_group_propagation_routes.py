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
    - Amazon FBA/AFN is skipped before push runtime.
    - MCF remains FBA-only visibility/fulfilment routing, not a stock push path.
    - FBM/MFN and non-Amazon pushable marketplace rows may be pushed.
    - No old workers, schedulers, or legacy routes are used.
    """
    from extensions import db
    from governed_execution import AMAZON_FBM_LIVE_APPROVAL_TYPE, submit_governed_marketplace_action
    from models import (
        AmazonFBAInventory,
        MarketplaceListing,
        MasterProductGroup,
        SyncLog,
        WarehouseStock,
    )
    from sqlalchemy import or_

    body = dict(payload or {})
    dry_run = bool(body.get("dry_run", False))
    requested_quantity = body.get("quantity")
    requested_warehouse_stock_id = body.get("warehouse_stock_id")

    target_quantity = None
    if requested_quantity is not None:
        try:
            target_quantity = int(requested_quantity)
        except (TypeError, ValueError):
            return jsonify(_blocked("quantity must be an integer when provided.", group_id=group_id)), 400
        if target_quantity < 0:
            return jsonify(_blocked("quantity cannot be negative.", group_id=group_id)), 400

    group = db.session.get(MasterProductGroup, group_id)
    if not group:
        return jsonify(_blocked("Master product group was not found.", group_id=group_id)), 404

    target_warehouse_stock_ids = set()

    if requested_warehouse_stock_id not in (None, ""):
        try:
            target_warehouse_stock_ids.add(int(requested_warehouse_stock_id))
        except (TypeError, ValueError):
            return jsonify(_blocked("warehouse_stock_id must be an integer when provided.", group_id=group_id)), 400

    existing_group_listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.master_product_group_id == group_id)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .all()
    )

    group_has_fba_authority = False
    for listing in existing_group_listings:
        if getattr(listing, "warehouse_stock_id", None):
            target_warehouse_stock_ids.add(int(listing.warehouse_stock_id))

        platform = ((listing.store.platform if listing.store else "") or "").strip().lower()
        channel = (getattr(listing, "normalized_amazon_fulfillment_channel", None) or "").strip().upper()
        if bool(getattr(listing, "is_fba", False)) or ("amazon" in platform and channel not in ("MFN", "FBM", "MERCHANT")):
            group_has_fba_authority = True

    warehouse_rows = []

    # Every Warehouse row already belonging to this group participates in
    # the same authority path. A listing may historically point at only one
    # child row, but the group must still expose one shared Warehouse quantity.
    group_stock_ids = {
        int(stock_id)
        for (stock_id,) in (
            db.session.query(WarehouseStock.id)
            .filter(
                WarehouseStock.master_product_group_id
                == group_id
            )
            .all()
        )
    }

    target_warehouse_stock_ids.update(group_stock_ids)

    if target_warehouse_stock_ids:
        attached_listings = (
            db.session.query(MarketplaceListing)
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(
                MarketplaceListing.warehouse_stock_id.in_(
                    target_warehouse_stock_ids
                )
            )
            .all()
        )

        for listing in attached_listings:
            saved_listing_group_id = getattr(
                listing,
                "master_product_group_id",
                None,
            )

            if saved_listing_group_id != group_id:
                return jsonify(
                    _blocked(
                        "Quantity propagation cannot create or repair "
                        "marketplace listing relationships. Link the listing "
                        "explicitly in Product Linking first.",
                        group_id=group_id,
                        listing_id=listing.id,
                        saved_group_id=saved_listing_group_id,
                    )
                ), 409

        warehouse_rows = (
            db.session.query(WarehouseStock)
            .filter(
                WarehouseStock.id.in_(
                    target_warehouse_stock_ids
                )
            )
            .all()
        )

    # Product Linking is only a shortcut into Warehouse authority.
    #
    # For an FBA-led group, AmazonFBAInventory is the read-only input.
    # Copy that quantity into Warehouse once, then Warehouse pushes the
    # same value to every non-FBA marketplace listing.
    if group_has_fba_authority:
        # FBA/AFN is the only quantity source for an FBA-led group.
        # Ignore any listing-local or caller-supplied quantity.
        fba_truth = (
            db.session.query(AmazonFBAInventory)
            .filter(
                AmazonFBAInventory.warehouse_stock_id.in_(
                    list(group_stock_ids)
                ),
                AmazonFBAInventory.is_active == True,  # noqa: E712
                AmazonFBAInventory.is_archived == False,  # noqa: E712
            )
            .order_by(
                AmazonFBAInventory.updated_at.desc(),
                AmazonFBAInventory.id.desc(),
            )
            .first()
        )

        if fba_truth is None:
            return jsonify(
                _blocked(
                    "FBA-led group has no active Amazon FBA "
                    "inventory truth.",
                    group_id=group_id,
                )
            ), 409

        target_quantity = int(
            fba_truth.available_quantity or 0
        )

        warehouse_rows = (
            db.session.query(WarehouseStock)
            .filter(
                WarehouseStock.id.in_(
                    list(target_warehouse_stock_ids)
                )
            )
            .all()
        )

    for stock in warehouse_rows:
        saved_stock_group_id = getattr(
            stock,
            "master_product_group_id",
            None,
        )
        saved_group_controlled = bool(
            getattr(stock, "is_group_controlled", False)
        )

        if (
            saved_stock_group_id != group_id
            or not saved_group_controlled
        ):
            return jsonify(
                _blocked(
                    "Quantity propagation cannot create or repair Warehouse "
                    "group relationships. Link the Warehouse row explicitly "
                    "in Product Linking first.",
                    group_id=group_id,
                    warehouse_stock_id=stock.id,
                    saved_group_id=saved_stock_group_id,
                    saved_is_group_controlled=saved_group_controlled,
                )
            ), 409

        if target_quantity is not None:
            stock_columns = set(
                stock.__table__.columns.keys()
            )

            for col in (
                "sellable_quantity",
                "available_quantity",
                "quantity",
            ):
                if col in stock_columns:
                    setattr(
                        stock,
                        col,
                        target_quantity,
                    )

    db.session.flush()

    listing_filters = [MarketplaceListing.master_product_group_id == group_id]
    if target_warehouse_stock_ids:
        listing_filters.append(MarketplaceListing.warehouse_stock_id.in_(target_warehouse_stock_ids))

    listings = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .filter(or_(*listing_filters))
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

        # Every pushable group child receives one Warehouse truth
        # quantity. Listing-local marketplace quantities must never
        # become a second authority.
        if target_quantity is None:
            return jsonify(
                _blocked(
                    "Warehouse group quantity could not be resolved.",
                    group_id=group_id,
                    listing_id=listing.id,
                )
            ), 409

        quantity = target_quantity

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

    is_group_child = bool(getattr(listing, "master_product_group_id", None))
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
