from __future__ import annotations

from flask import jsonify, request
from flask_login import login_required


def install_governed_notification_read_alignment(app):
    """Replace only the existing notification bell read implementation.

    The registered URL and endpoint stay unchanged. This is read-only UI
    alignment: MarketplaceOrder, MarketplaceListing and SyncLog remain the
    persisted sources. No marketplace call, sync, import, push or DB write is
    introduced here.
    """

    @login_required
    def fast_governed_ui_notifications():
        from extensions import db
        from models import MarketplaceOrder, MarketplaceListing, SyncLog
        from sqlalchemy import and_, or_
        from sqlalchemy.orm import joinedload

        try:
            limit = int(request.args.get("limit") or 20)
        except Exception:
            limit = 20
        limit = max(1, min(limit, 50))

        records = []

        # Recent sales plus Store in one read. The previous route could lazy-load
        # Store once per row and then queried MarketplaceListing once per order.
        orders = (
            db.session.query(MarketplaceOrder)
            .options(joinedload(MarketplaceOrder.store))
            .order_by(
                MarketplaceOrder.created_at.desc(),
                MarketplaceOrder.id.desc(),
            )
            .limit(limit)
            .all()
        )

        order_listing_filters = []
        seen_order_listing_keys = set()

        for order in orders:
            store_id = getattr(order, "store_id", None)
            sku = str(getattr(order, "sku", None) or "").strip()
            key = (store_id, sku)
            if store_id is None or not sku or key in seen_order_listing_keys:
                continue
            seen_order_listing_keys.add(key)
            order_listing_filters.append(
                and_(
                    MarketplaceListing.store_id == int(store_id),
                    MarketplaceListing.external_sku == sku,
                )
            )

        # Resolve all sale titles together instead of one query per order.
        order_title_by_key = {}
        if order_listing_filters:
            title_rows = (
                db.session.query(MarketplaceListing)
                .filter(
                    MarketplaceListing.is_active == True,  # noqa: E712
                    or_(*order_listing_filters),
                )
                .order_by(MarketplaceListing.id.desc())
                .all()
            )
            for listing in title_rows:
                key = (
                    getattr(listing, "store_id", None),
                    str(getattr(listing, "external_sku", None) or "").strip(),
                )
                if key not in order_title_by_key:
                    order_title_by_key[key] = str(
                        getattr(listing, "title", None) or ""
                    ).strip()

        for order in orders:
            store = getattr(order, "store", None)
            platform = getattr(store, "platform", None) or "Marketplace"
            order_id = (
                getattr(order, "marketplace_order_id", None)
                or getattr(order, "external_order_id", None)
                or ""
            )
            sku = str(getattr(order, "sku", None) or "").strip()
            quantity = int(getattr(order, "quantity", 0) or 0)
            store_id = getattr(order, "store_id", None)
            product_title = order_title_by_key.get((store_id, sku), "")

            # Use marketplace line identity for display de-duplication. Provider
            # webhook retries may have produced more than one DB row, but the bell
            # should show the commercial sale once. DB history is not modified.
            line_identity = (
                getattr(order, "marketplace_order_item_id", None)
                or sku
                or getattr(order, "id", "")
            )

            records.append({
                "event_key": f"order:{store_id}:{order_id}:{line_identity}",
                "log_type": "marketplace_sale",
                "platform": platform,
                "title": product_title,
                "sku": sku,
                "quantity": quantity,
                "order_id": order_id,
                "message": (
                    product_title
                    or (
                        f"Sale {order_id}: {sku} x{quantity}"
                        if sku
                        else f"Marketplace sale {order_id}"
                    )
                ),
                "created_at": (
                    order.created_at.isoformat()
                    if getattr(order, "created_at", None)
                    else None
                ),
            })

        # Canonical listing truth plus Store in one read.
        listing_rows = (
            db.session.query(MarketplaceListing)
            .options(joinedload(MarketplaceListing.store))
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .order_by(
                MarketplaceListing.created_at.desc(),
                MarketplaceListing.id.desc(),
            )
            .limit(limit)
            .all()
        )

        for listing in listing_rows:
            store = getattr(listing, "store", None)
            platform = getattr(store, "platform", None) or "Marketplace"
            title = str(
                getattr(listing, "title", None)
                or getattr(listing, "external_sku", None)
                or "Marketplace listing"
            ).strip()
            sku = str(getattr(listing, "external_sku", None) or "").strip()
            external_listing_id = str(
                getattr(listing, "external_listing_id", None) or ""
            ).strip()

            records.append({
                "event_key": f"listing:{getattr(listing, 'id', '')}",
                "log_type": "marketplace_listing",
                "platform": platform,
                "title": title,
                "sku": sku,
                "listing_id": external_listing_id,
                "message": title,
                "created_at": (
                    listing.created_at.isoformat()
                    if getattr(listing, "created_at", None)
                    else None
                ),
            })

        # Existing governed push/link audit truth.
        sync_event_rows = (
            db.session.query(SyncLog)
            .filter(
                or_(
                    SyncLog.message.startswith(
                        "event_type=marketplace_push",
                        autoescape=True,
                    ),
                    SyncLog.message.startswith(
                        "event_type=product_linking_",
                        autoescape=True,
                    ),
                )
            )
            .order_by(SyncLog.created_at.desc(), SyncLog.id.desc())
            .limit(limit)
            .all()
        )

        parsed_sync_rows = []
        sync_listing_ids = set()

        for row in sync_event_rows:
            message = str(row.message or "").strip()
            fields = {}
            for token in message.split():
                if "=" not in token:
                    continue
                key, value = token.split("=", 1)
                fields[key.strip()] = value.strip()

            listing_db_id = fields.get("listing_id")
            try:
                if listing_db_id not in (None, ""):
                    sync_listing_ids.add(int(listing_db_id))
            except (TypeError, ValueError):
                pass

            parsed_sync_rows.append((row, message, fields))

        # Resolve every SyncLog listing and Store in one batch rather than one
        # MarketplaceListing.query.get() per event.
        sync_listings_by_id = {}
        if sync_listing_ids:
            sync_listings = (
                db.session.query(MarketplaceListing)
                .options(joinedload(MarketplaceListing.store))
                .filter(MarketplaceListing.id.in_(list(sync_listing_ids)))
                .all()
            )
            sync_listings_by_id = {
                int(listing.id): listing
                for listing in sync_listings
            }

        for row, message, fields in parsed_sync_rows:
            event_type = fields.get("event_type") or "governed_event"
            marketplace = fields.get("marketplace") or "BT38"
            quantity = fields.get("quantity")
            group_id = fields.get("group_id")
            listing = None

            try:
                listing_db_id = fields.get("listing_id")
                if listing_db_id not in (None, ""):
                    listing = sync_listings_by_id.get(int(listing_db_id))
            except (TypeError, ValueError):
                listing = None

            product_title = ""
            sku = ""
            external_listing_id = ""
            asin = ""

            if listing is not None:
                product_title = str(
                    getattr(listing, "title", None) or ""
                ).strip()
                sku = str(
                    getattr(listing, "external_sku", None) or ""
                ).strip()
                external_listing_id = str(
                    getattr(listing, "external_listing_id", None) or ""
                ).strip()
                asin = str(getattr(listing, "asin", None) or "").strip()
                store = getattr(listing, "store", None)
                marketplace = getattr(store, "platform", None) or marketplace
            else:
                logged_sku = str(fields.get("sku") or "").strip()
                if not logged_sku.isdigit():
                    sku = logged_sku

            if event_type == "marketplace_push_succeeded":
                title = "Marketplace quantity push succeeded"
            elif event_type == "marketplace_push_failed":
                title = "Marketplace quantity push failed"
            elif (
                event_type == "marketplace_push_noop"
                or fields.get("reason") == "marketplace_already_matches_warehouse"
            ):
                title = "Quantity verified"
            elif event_type.startswith("product_linking_"):
                title = "Product linking updated"
            else:
                title = "Marketplace push update"

            records.append({
                "event_key": f"sync:{row.id}",
                "id": f"sync:{row.id}",
                "log_type": event_type,
                "platform": marketplace,
                "title": title,
                "product_title": product_title,
                "sku": sku,
                "listing_id": external_listing_id,
                "asin": asin,
                "quantity": quantity,
                "group_id": group_id,
                "message": message,
                "created_at": (
                    row.created_at.isoformat()
                    if row.created_at
                    else None
                ),
            })

        records.sort(
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        )

        seen = set()
        unique = []
        for record in records:
            key = record.get("event_key")
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(record)
            if len(unique) >= limit:
                break

        return jsonify({
            "success": True,
            "records": unique,
            "latest_event_at": (
                unique[0].get("created_at")
                if unique
                else None
            ),
        })

    endpoint = "governed.governed_ui_notifications"
    if endpoint not in app.view_functions:
        raise RuntimeError(
            "governed notification endpoint is not registered"
        )

    app.view_functions[endpoint] = fast_governed_ui_notifications
    app.logger.info(
        "BT38 notification read alignment installed: batched persisted-event reads"
    )
