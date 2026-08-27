from __future__ import annotations

from datetime import datetime

from flask import jsonify, request


def install_product_linking_unlink_alignment(app):
    """Restore a mutable listing to its persisted pre-link Warehouse identity.

    Product Linking is temporary relationship membership.  A listing that was
    linked onto another product's WarehouseStock must be returned to the exact
    WarehouseStock row that already exists for its marketplace SKU; no group ID
    or stock row is invented during unlink.
    """
    endpoint = "governed_groups.governed_group_unlink"
    if endpoint not in app.view_functions:
        raise RuntimeError("governed Product Linking unlink endpoint is not registered")

    def aligned_governed_group_unlink(group_id: int):
        from extensions import db
        from governed_group_routes import (
            _actor,
            _blocked,
            _change_contract,
            _queue_group_push_after_commit,
            _serialize_master_group,
            _targeted_response,
        )
        from models import (
            MarketplaceListing,
            MasterProductGroup,
            SyncLog,
            WarehouseStock,
        )

        body = dict(request.get_json(silent=True) or {})
        group = db.session.get(MasterProductGroup, int(group_id))
        if not group:
            return jsonify(_blocked(
                "Master product group was not found.",
                group_id=group_id,
            )), 404

        listing_id = body.get("listing_id") or body.get("marketplace_listing_id")
        if not listing_id:
            return jsonify(_blocked(
                "listing_id is required.",
                group_id=group_id,
            )), 400

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

        # FBA/AFN is the read-only group authority and must never be detached by
        # this UI action.  Mutable marketplace listings are allowed to leave.
        if bool(getattr(listing, "is_fba", False)):
            return jsonify(_blocked(
                "FBA/AFN listings are read-only. Unlink a mutable listing from the group instead.",
                group_id=group_id,
                listing_id=listing_id,
                fba_read_only=True,
            )), 409

        current_stock = listing.warehouse_stock
        if current_stock is None:
            return jsonify(_blocked(
                "The listing has no current Warehouse stock identity.",
                group_id=group_id,
                listing_id=listing_id,
            )), 409

        listing_sku = str(listing.external_sku or "").strip()
        if not listing_sku:
            return jsonify(_blocked(
                "The listing has no marketplace SKU, so its persisted original Warehouse identity cannot be resolved.",
                group_id=group_id,
                listing_id=listing_id,
                warehouse_stock_id=current_stock.id,
            )), 409

        # Resolve only from persisted Warehouse truth.  This specifically repairs
        # legacy linked rows where warehouse_stock_id was changed to the shared
        # FBA/master stock.  Exact SKU identity is required; titles are never used
        # to guess the original product.
        candidates = (
            db.session.query(WarehouseStock)
            .filter(WarehouseStock.sku == listing_sku)
            .order_by(WarehouseStock.id)
            .all()
        )

        valid_candidates = []
        for stock in candidates:
            original_group_id = getattr(stock, "master_product_group_id", None)
            if original_group_id is None:
                continue
            if db.session.get(MasterProductGroup, int(original_group_id)) is None:
                continue
            valid_candidates.append(stock)

        # The current stock is already the original identity only when its exact
        # SKU matches and its permanent group differs from the shared group.
        current_is_original = (
            str(getattr(current_stock, "sku", "") or "").strip() == listing_sku
            and getattr(current_stock, "master_product_group_id", None) is not None
            and int(current_stock.master_product_group_id) != int(group_id)
        )

        if current_is_original:
            original_stock = current_stock
        else:
            recoverable = [
                stock
                for stock in valid_candidates
                if int(stock.id) != int(current_stock.id)
                and int(stock.master_product_group_id) != int(group_id)
            ]

            if len(recoverable) == 0:
                return jsonify(_blocked(
                    "No persisted original Warehouse stock was found for this marketplace SKU. Unlink was not changed.",
                    group_id=group_id,
                    listing_id=listing_id,
                    sku=listing_sku,
                    current_warehouse_stock_id=current_stock.id,
                    current_warehouse_group_id=current_stock.master_product_group_id,
                )), 409

            if len(recoverable) > 1:
                return jsonify(_blocked(
                    "More than one persisted original Warehouse stock matches this marketplace SKU. Unlink was not changed because the original identity is ambiguous.",
                    group_id=group_id,
                    listing_id=listing_id,
                    sku=listing_sku,
                    candidate_stock_ids=[int(stock.id) for stock in recoverable],
                    candidate_group_ids=[
                        int(stock.master_product_group_id)
                        for stock in recoverable
                    ],
                )), 409

            original_stock = recoverable[0]

        original_group_id = int(original_stock.master_product_group_id)
        original_group = db.session.get(MasterProductGroup, original_group_id)
        if original_group is None:
            return jsonify(_blocked(
                "The persisted original Product Linking group no longer exists.",
                group_id=group_id,
                listing_id=listing_id,
                original_group_id=original_group_id,
                original_warehouse_stock_id=original_stock.id,
            )), 409

        if (
            int(original_stock.id) == int(current_stock.id)
            and original_group_id == int(group_id)
        ):
            return jsonify(_blocked(
                "Listing is already in its persisted original Product Linking group; no unlink mutation is required.",
                group_id=group_id,
                listing_id=listing_id,
                warehouse_stock_id=original_stock.id,
            )), 409

        previous_group_id = int(group_id)
        previous_stock_id = int(current_stock.id)
        now = datetime.utcnow()

        # Restore both halves of the original relationship atomically.  Changing
        # only master_product_group_id would leave the listing attached to the
        # shared FBA Warehouse row and recreate the master/unlink defect.
        listing.warehouse_stock_id = int(original_stock.id)
        listing.master_product_group_id = original_group_id
        listing.updated_at = now
        group.updated_at = now
        original_group.updated_at = now

        db.session.add(SyncLog(
            store_id=getattr(listing, "store_id", None),
            status="success",
            message=(
                "event_type=product_linking_unlink "
                f"automatic=False actor={_actor()} "
                "source=product_linking_ui "
                f"listing_id={int(listing.id)} "
                f"sku={listing_sku} "
                f"previous_warehouse_stock_id={previous_stock_id} "
                f"warehouse_stock_id={int(original_stock.id)} "
                f"previous_group_id={previous_group_id} "
                f"group_id={original_group_id} "
                "restored_original=True"
            )[:500],
            items_synced=1,
            created_at=now,
        ))

        db.session.commit()
        db.session.expire_all()

        committed_listing = db.session.get(MarketplaceListing, int(listing_id))
        if (
            committed_listing is None
            or int(committed_listing.warehouse_stock_id or 0) != int(original_stock.id)
            or int(committed_listing.master_product_group_id or 0) != original_group_id
        ):
            return jsonify(_blocked(
                "Neon did not confirm the committed original Product Linking relationship.",
                group_id=previous_group_id,
                listing_id=listing_id,
                expected_warehouse_stock_id=int(original_stock.id),
                committed_warehouse_stock_id=(
                    committed_listing.warehouse_stock_id
                    if committed_listing is not None
                    else None
                ),
                expected_group_id=original_group_id,
                committed_group_id=(
                    committed_listing.master_product_group_id
                    if committed_listing is not None
                    else None
                ),
            )), 409

        # Preserve the existing post-unlink behaviour: relationship truth is
        # committed first, then only the two affected groups are queued.
        push_queue_result = _queue_group_push_after_commit(
            [previous_group_id, original_group_id],
            source="product_linking_unlink_auto_push",
        )

        committed_group = db.session.get(MasterProductGroup, original_group_id)
        payload = _serialize_master_group(committed_group)
        payload.update({
            "event_type": "product_linking_unlink",
            "event_source": "product_linking_ui",
            "message": "Listing removed from the shared Product Linking group and restored to its persisted original group.",
            "listing_id": int(listing_id),
            "sku": listing_sku,
            "previous_group_id": previous_group_id,
            "group_id": original_group_id,
            "original_group_id": original_group_id,
            "previous_warehouse_stock_id": previous_stock_id,
            "warehouse_stock_id": int(original_stock.id),
            "restored_original_group": True,
            "auto_push_attempted": False,
            "auto_push_queued": bool(push_queue_result.get("queued")),
            "auto_push_success": None,
            "push_result": push_queue_result,
            **_change_contract(
                changed=True,
                group_ids=[previous_group_id, original_group_id],
                stock_ids=[previous_stock_id, int(original_stock.id)],
                listing_ids=[listing_id],
            ),
        })
        return _targeted_response(payload)

    app.view_functions[endpoint] = aligned_governed_group_unlink
    app.logger.info(
        "BT38 Product Linking unlink alignment installed: restore exact persisted original Warehouse identity"
    )
