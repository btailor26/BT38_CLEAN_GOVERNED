from __future__ import annotations

from flask import jsonify, request
from flask_login import login_required


def _fulfillment_mode(order, profile) -> str:
    """Return the persisted marketplace fulfillment truth used by the bell.

    FBA/AFN always wins and is never treated as merchant fulfilled. Seller
    Fulfilled Prime is only true when the exact Amazon order profile says
    is_prime=True. Premium/service-level text is deliberately not authority.
    Every remaining merchant-fulfilled order is standard FBM.
    """
    fulfillment = str(getattr(order, "fulfillment_type", None) or "").strip().upper()
    profile_channel = str(
        getattr(profile, "fulfillment_channel", None) or ""
    ).strip().upper()
    if fulfillment in {"FBA", "AFN"} or profile_channel in {"FBA", "AFN"}:
        return "FBA"
    if profile is not None and getattr(profile, "is_prime", None) is True:
        return "SFP"
    return "FBM"


def install_governed_notification_read_alignment(app):
    """Replace only the existing notification bell read implementation.

    The registered URL and endpoint stay unchanged. This is read-only UI
    alignment: MarketplaceOrder, MarketplaceListing, FBMOrderProfile and
    SyncLog remain the persisted sources. No marketplace call, sync, import,
    push or DB write is introduced here.
    """

    @login_required
    def fast_governed_ui_notifications():
        from extensions import db
        from fbm_models import FBMOrderProfile
        from models import MarketplaceOrder, MarketplaceListing, SyncLog
        from sqlalchemy import and_, or_, tuple_
        from sqlalchemy.orm import joinedload

        try:
            limit = int(request.args.get("limit") or 20)
        except Exception:
            limit = 20
        limit = max(1, min(limit, 50))

        records = []

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

        profile_keys = sorted({
            (
                int(getattr(order, "store_id")),
                str(getattr(order, "marketplace_order_id", None) or "").strip(),
            )
            for order in orders
            if getattr(order, "store_id", None) is not None
            and str(getattr(order, "marketplace_order_id", None) or "").strip()
        })
        profiles_by_key = {}
        if profile_keys:
            profile_rows = (
                db.session.query(FBMOrderProfile)
                .filter(
                    tuple_(
                        FBMOrderProfile.store_id,
                        FBMOrderProfile.marketplace_order_id,
                    ).in_(profile_keys)
                )
                .all()
            )
            profiles_by_key = {
                (int(profile.store_id), str(profile.marketplace_order_id)): profile
                for profile in profile_rows
            }

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
            marketplace = getattr(store, "platform", None) or "Marketplace"
            order_id = (
                getattr(order, "marketplace_order_id", None)
                or getattr(order, "external_order_id", None)
                or ""
            )
            sku = str(getattr(order, "sku", None) or "").strip()
            quantity = int(getattr(order, "quantity", 0) or 0)
            store_id = getattr(order, "store_id", None)
            product_title = order_title_by_key.get((store_id, sku), "")
            profile = (
                profiles_by_key.get((int(store_id), str(order_id)))
                if store_id is not None and order_id
                else None
            )
            fulfillment_mode = _fulfillment_mode(order, profile)
            fulfillment_label = "Prime" if fulfillment_mode == "SFP" else fulfillment_mode
            platform_display = f"{marketplace} · {fulfillment_label}"

            line_identity = (
                getattr(order, "marketplace_order_item_id", None)
                or sku
                or getattr(order, "id", "")
            )

            records.append({
                "event_key": f"order:{store_id}:{order_id}:{line_identity}",
                "log_type": "marketplace_sale",
                "platform": platform_display,
                "marketplace": marketplace,
                "title": product_title,
                "sku": sku,
                "quantity": quantity,
                "order_id": order_id,
                "fulfillment_mode": fulfillment_mode,
                "fulfillment_label": fulfillment_label,
                "is_prime": bool(fulfillment_mode == "SFP"),
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
