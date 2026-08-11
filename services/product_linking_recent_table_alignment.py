"""Recent-group Product Linking read model.

This module changes only the Product Linking read/query path. The existing
renderer, buttons, relationship writers and Warehouse-controlled push path stay
untouched.

Contract:
- landing/page navigation loads display groups, never a raw 5,000-row snapshot
- groups are ordered by latest committed Warehouse/listing activity
- current MarketplaceListing.master_product_group_id is relationship authority
- Warehouse permanent/original groups remain preserved but do not render as
  duplicate shadow groups while their listings are shared elsewhere
- search remains targeted and returns the same Product Linking row shape
- idle groups are not loaded merely because the browser is open
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from flask import jsonify, request
from sqlalchemy import or_, text


def install_product_linking_recent_table_alignment(app) -> None:
    if getattr(app, "_bt38_product_linking_recent_table_installed", False):
        return
    app._bt38_product_linking_recent_table_installed = True

    @app.before_request
    def _bt38_recent_product_linking_data():
        if request.method != "GET":
            return None
        if request.path.rstrip("/") != "/governed/product-linking/data":
            return None
        return _build_recent_product_linking_response()


def _safe_page_size(value) -> int:
    try:
        parsed = int(value or 15)
    except Exception:
        parsed = 15
    return parsed if parsed in {15, 25, 50, 100} else 15


def _safe_page(value) -> int:
    try:
        return max(1, int(value or 1))
    except Exception:
        return 1


def _identity_activity_sql(search: str | None = None) -> str:
    # A Warehouse row whose active listing currently belongs to a shared group
    # contributes activity to that CURRENT group, not to its permanent/original
    # Warehouse group. This is what prevents Group 775/776-style shadow rows.
    base = """
        WITH listing_activity AS (
            SELECT
                'g:' || ml.master_product_group_id::text AS identity_key,
                GREATEST(
                    COALESCE(ml.updated_at, TIMESTAMP '1970-01-01'),
                    COALESCE(ws.updated_at, TIMESTAMP '1970-01-01')
                ) AS touched_at
            FROM marketplace_listings ml
            LEFT JOIN warehouse_stock ws ON ws.id = ml.warehouse_stock_id
            WHERE ml.is_active = true
              AND ml.master_product_group_id IS NOT NULL
              {listing_search}
        ),
        stock_activity AS (
            SELECT
                CASE
                    WHEN ws.master_product_group_id IS NOT NULL
                        THEN 'g:' || ws.master_product_group_id::text
                    ELSE 's:' || ws.id::text
                END AS identity_key,
                COALESCE(ws.updated_at, ws.created_at, TIMESTAMP '1970-01-01') AS touched_at
            FROM warehouse_stock ws
            WHERE ws.is_active = true
              AND ws.is_deleted = false
              AND NOT EXISTS (
                    SELECT 1
                    FROM marketplace_listings ml2
                    WHERE ml2.is_active = true
                      AND ml2.warehouse_stock_id = ws.id
                      AND ml2.master_product_group_id IS NOT NULL
              )
              {stock_search}
        ),
        activity AS (
            SELECT * FROM listing_activity
            UNION ALL
            SELECT * FROM stock_activity
        ),
        collapsed AS (
            SELECT identity_key, MAX(touched_at) AS touched_at
            FROM activity
            GROUP BY identity_key
        )
    """

    if search:
        listing_search = """
          AND (
              ml.external_sku ILIKE :like
              OR ml.title ILIKE :like
              OR ml.external_listing_id ILIKE :like
              OR ml.asin ILIKE :like
              OR ml.fnsku ILIKE :like
              OR ml.barcode ILIKE :like
              OR ml.parent_item_id ILIKE :like
              OR ml.external_parent_id ILIKE :like
              OR ml.variation_sku_map ILIKE :like
              OR ws.sku ILIKE :like
              OR ws.product_name ILIKE :like
              OR ws.barcode ILIKE :like
              OR ws.group_title ILIKE :like
              OR ml.master_product_group_id::text = :exact
          )
        """
        stock_search = """
          AND (
              ws.sku ILIKE :like
              OR ws.product_name ILIKE :like
              OR ws.barcode ILIKE :like
              OR ws.group_title ILIKE :like
              OR ws.master_product_group_id::text = :exact
          )
        """
    else:
        listing_search = ""
        stock_search = ""

    return base.format(
        listing_search=listing_search,
        stock_search=stock_search,
    )


def _recent_identity_page(search: str, page: int, per_page: int):
    from extensions import db

    params = {
        "limit": per_page,
        "offset": (page - 1) * per_page,
    }
    if search:
        params.update({"like": f"%{search}%", "exact": search})

    cte = _identity_activity_sql(search or None)
    total = int(
        db.session.execute(
            text(cte + " SELECT COUNT(*) FROM collapsed"),
            params,
        ).scalar()
        or 0
    )

    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
        params["offset"] = (page - 1) * per_page

    rows = db.session.execute(
        text(
            cte
            + """
              SELECT identity_key, touched_at
              FROM collapsed
              ORDER BY touched_at DESC, identity_key DESC
              LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).all()

    return page, total, total_pages, [
        (str(row[0]), row[1]) for row in rows
    ]


def _listing_payload(listing, fba_available_quantity=None, stock_quantity=None):
    store = getattr(listing, "store", None)
    platform = (
        getattr(store, "platform", None)
        or getattr(listing, "platform", "")
        or ""
    )
    channel = str(
        getattr(listing, "normalized_amazon_fulfillment_channel", None)
        or getattr(listing, "amazon_fulfillment_channel", None)
        or ""
    ).upper()
    is_amazon = "amazon" in str(platform).lower()
    is_ebay = "ebay" in str(platform).lower()
    is_fba = bool(getattr(listing, "is_fba", False)) or (
        is_amazon and channel not in {"MFN", "FBM", "MERCHANT"}
    )
    is_fbm = is_amazon and channel in {"MFN", "FBM", "MERCHANT"}

    if is_fba:
        push_status = "read_only"
        push_status_label = "FBA read-only"
        push_status_reason = "Amazon controls FBA/AFN stock. Group push skips this listing."
        pushable = False
    elif is_fbm or is_ebay:
        push_status = "pushable"
        push_status_label = "Pushable"
        push_status_reason = "Seller-controlled marketplace stock can be updated from warehouse truth."
        pushable = True
    else:
        push_status = "not_pushable"
        push_status_label = "Not pushable"
        push_status_reason = "Listing is not eligible for governed marketplace push."
        pushable = False

    effective_quantity = (
        int(stock_quantity)
        if stock_quantity is not None
        else int(getattr(listing, "effective_quantity", 0) or 0)
    )

    return {
        "id": listing.id,
        "external_sku": listing.external_sku,
        "sku": listing.external_sku,
        "title": listing.title,
        "external_listing_id": listing.external_listing_id,
        "external_id": listing.external_listing_id,
        "asin": listing.asin,
        "fnsku": listing.fnsku,
        "warehouse_stock_id": listing.warehouse_stock_id,
        "master_product_group_id": listing.master_product_group_id,
        "store_id": listing.store_id,
        "store_name": getattr(store, "name", "") if store else "",
        "platform": platform,
        "amazon_fulfillment_channel": getattr(
            listing, "amazon_fulfillment_channel", None
        ),
        "is_fba": is_fba,
        "is_pushable": pushable,
        "push_status": push_status,
        "push_status_label": push_status_label,
        "push_status_reason": push_status_reason,
        "effective_quantity": effective_quantity,
        "fba_available_quantity": fba_available_quantity,
    }


def _build_recent_product_linking_response():
    from extensions import db
    from models import AmazonFBAInventory, MarketplaceListing, WarehouseStock
    from sqlalchemy.orm import joinedload

    search = (request.args.get("search") or request.args.get("q") or "").strip()
    per_page = _safe_page_size(
        request.args.get("per_page") or request.args.get("limit")
    )
    page = _safe_page(request.args.get("page"))

    page, total_groups, total_pages, identities = _recent_identity_page(
        search,
        page,
        per_page,
    )

    ordered_keys = [key for key, _ in identities]
    touched_by_key = {key: touched for key, touched in identities}
    group_ids = [int(key[2:]) for key in ordered_keys if key.startswith("g:")]
    single_stock_ids = [int(key[2:]) for key in ordered_keys if key.startswith("s:")]

    listings = []
    if group_ids:
        listings.extend(
            db.session.query(MarketplaceListing)
            .options(joinedload(MarketplaceListing.store))
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(MarketplaceListing.master_product_group_id.in_(group_ids))
            .all()
        )
    if single_stock_ids:
        listings.extend(
            db.session.query(MarketplaceListing)
            .options(joinedload(MarketplaceListing.store))
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(MarketplaceListing.warehouse_stock_id.in_(single_stock_ids))
            .filter(MarketplaceListing.master_product_group_id.is_(None))
            .all()
        )

    listing_stock_ids = {
        int(row.warehouse_stock_id)
        for row in listings
        if getattr(row, "warehouse_stock_id", None) is not None
    }

    stock_filter_ids = set(single_stock_ids) | listing_stock_ids
    stock_rows = []
    if stock_filter_ids or group_ids:
        clauses = []
        if stock_filter_ids:
            clauses.append(WarehouseStock.id.in_(list(stock_filter_ids)))
        if group_ids:
            clauses.append(WarehouseStock.master_product_group_id.in_(group_ids))
        stock_rows = (
            db.session.query(WarehouseStock)
            .filter(WarehouseStock.is_active == True)  # noqa: E712
            .filter(WarehouseStock.is_deleted == False)  # noqa: E712
            .filter(or_(*clauses))
            .all()
        )

    stock_by_id = {int(stock.id): stock for stock in stock_rows}
    permanent_group_stocks = defaultdict(list)
    for stock in stock_rows:
        if getattr(stock, "master_product_group_id", None) is not None:
            permanent_group_stocks[int(stock.master_product_group_id)].append(stock)

    listings_by_group = defaultdict(list)
    listings_by_stock = defaultdict(list)
    for listing in listings:
        if getattr(listing, "master_product_group_id", None) is not None:
            listings_by_group[int(listing.master_product_group_id)].append(listing)
        elif getattr(listing, "warehouse_stock_id", None) is not None:
            listings_by_stock[int(listing.warehouse_stock_id)].append(listing)

    # Fetch only FBA truth needed by the selected visible groups.
    fba_skus = {
        str(row.external_sku or "").strip()
        for row in listings
        if str(row.external_sku or "").strip()
        and bool(getattr(row, "is_fba", False))
    }
    fba_fnskus = {
        str(row.fnsku or "").strip()
        for row in listings
        if str(row.fnsku or "").strip()
        and bool(getattr(row, "is_fba", False))
    }
    fba_stock_ids = {
        int(row.warehouse_stock_id)
        for row in listings
        if getattr(row, "warehouse_stock_id", None) is not None
        and bool(getattr(row, "is_fba", False))
    }

    fba_rows = []
    if fba_skus or fba_fnskus or fba_stock_ids:
        fba_rows = (
            db.session.query(AmazonFBAInventory)
            .filter(or_(
                AmazonFBAInventory.seller_sku.in_(list(fba_skus) or ["__none__"]),
                AmazonFBAInventory.fnsku.in_(list(fba_fnskus) or ["__none__"]),
                AmazonFBAInventory.warehouse_stock_id.in_(list(fba_stock_ids) or [-1]),
            ))
            .all()
        )

    fba_by_sku = {
        str(row.seller_sku).strip(): int(row.available_quantity or 0)
        for row in fba_rows
        if getattr(row, "seller_sku", None)
    }
    fba_by_fnsku = {
        str(row.fnsku).strip(): int(row.available_quantity or 0)
        for row in fba_rows
        if getattr(row, "fnsku", None)
    }
    fba_by_stock = {
        int(row.warehouse_stock_id): int(row.available_quantity or 0)
        for row in fba_rows
        if getattr(row, "warehouse_stock_id", None) is not None
    }

    products_by_key = {}
    selected_listing_payloads = []

    for key in ordered_keys:
        if key.startswith("g:"):
            group_id = int(key[2:])
            group_listings = list(listings_by_group.get(group_id, []))
            candidate_stocks = list(permanent_group_stocks.get(group_id, []))
            if not candidate_stocks:
                candidate_stocks = [
                    stock_by_id[int(row.warehouse_stock_id)]
                    for row in group_listings
                    if getattr(row, "warehouse_stock_id", None) in stock_by_id
                ]
            if not candidate_stocks:
                continue

            group_has_fba = any(
                bool(getattr(row, "is_fba", False))
                for row in group_listings
            )
            fba_member_stock_ids = {
                int(row.warehouse_stock_id)
                for row in group_listings
                if bool(getattr(row, "is_fba", False))
                and getattr(row, "warehouse_stock_id", None) is not None
            }
            authority = sorted(
                candidate_stocks,
                key=lambda stock: (
                    0 if group_has_fba and int(stock.id) in fba_member_stock_ids else 1,
                    int(stock.id),
                ),
            )[0]

            payloads = []
            fba_authority_qty = None
            for listing in sorted(group_listings, key=lambda row: int(row.id)):
                fba_qty = None
                if bool(getattr(listing, "is_fba", False)):
                    sku = str(listing.external_sku or "").strip()
                    fnsku = str(listing.fnsku or "").strip()
                    fba_qty = fba_by_sku.get(sku)
                    if fba_qty is None:
                        fba_qty = fba_by_fnsku.get(fnsku)
                    if fba_qty is None and listing.warehouse_stock_id is not None:
                        fba_qty = fba_by_stock.get(int(listing.warehouse_stock_id))
                    if fba_authority_qty is None and fba_qty is not None:
                        fba_authority_qty = fba_qty
                stock_qty = None
                if listing.warehouse_stock_id in stock_by_id:
                    stock_qty = int(
                        stock_by_id[int(listing.warehouse_stock_id)].sellable_quantity or 0
                    )
                payload = _listing_payload(listing, fba_qty, stock_qty)
                payloads.append(payload)
                selected_listing_payloads.append(payload)

            display_qty = (
                fba_authority_qty
                if fba_authority_qty is not None
                else int(authority.sellable_quantity or 0)
            )
            products_by_key[key] = {
                "id": authority.id,
                "sku": authority.sku,
                "name": authority.product_name,
                "product_name": authority.product_name,
                "group_name": authority.group_title or authority.product_name or authority.sku,
                "barcode": authority.barcode,
                "group_title": authority.group_title,
                "master_product_group_id": group_id,
                "is_group_controlled": bool(
                    getattr(authority, "is_group_controlled", False)
                    or len(payloads) > 1
                ),
                "is_fba_group": group_has_fba,
                "fba_authority_quantity": fba_authority_qty,
                "quantity": display_qty,
                "available_quantity": display_qty,
                "sellable_quantity": display_qty,
                "linked_count": len(payloads),
                "platforms": sorted({
                    str(item.get("platform") or "").strip()
                    for item in payloads
                    if item.get("platform")
                }),
                "listings": payloads,
                "updated_at": (
                    touched_by_key.get(key).isoformat()
                    if hasattr(touched_by_key.get(key), "isoformat")
                    else str(touched_by_key.get(key) or "")
                ),
            }
        else:
            stock_id = int(key[2:])
            authority = stock_by_id.get(stock_id)
            if authority is None:
                continue
            stock_listings = list(listings_by_stock.get(stock_id, []))
            payloads = []
            for listing in sorted(stock_listings, key=lambda row: int(row.id)):
                payload = _listing_payload(
                    listing,
                    None,
                    int(authority.sellable_quantity or 0),
                )
                payloads.append(payload)
                selected_listing_payloads.append(payload)
            display_qty = int(authority.sellable_quantity or 0)
            products_by_key[key] = {
                "id": authority.id,
                "sku": authority.sku,
                "name": authority.product_name,
                "product_name": authority.product_name,
                "group_name": authority.group_title or authority.product_name or authority.sku,
                "barcode": authority.barcode,
                "group_title": authority.group_title,
                "master_product_group_id": authority.master_product_group_id,
                "is_group_controlled": bool(getattr(authority, "is_group_controlled", False)),
                "is_fba_group": False,
                "fba_authority_quantity": None,
                "quantity": display_qty,
                "available_quantity": display_qty,
                "sellable_quantity": display_qty,
                "linked_count": len(payloads),
                "platforms": sorted({
                    str(item.get("platform") or "").strip()
                    for item in payloads
                    if item.get("platform")
                }),
                "listings": payloads,
                "updated_at": (
                    touched_by_key.get(key).isoformat()
                    if hasattr(touched_by_key.get(key), "isoformat")
                    else str(touched_by_key.get(key) or "")
                ),
            }

    warehouse_products = [
        products_by_key[key]
        for key in ordered_keys
        if key in products_by_key
    ]

    # Search may also target a genuinely unlinked marketplace listing. Keep the
    # existing unlinked result shape, but do not load all idle unlinked rows.
    unlinked_listings = []
    if search:
        like = f"%{search}%"
        rows = (
            db.session.query(MarketplaceListing)
            .options(joinedload(MarketplaceListing.store))
            .filter(MarketplaceListing.is_active == True)  # noqa: E712
            .filter(MarketplaceListing.warehouse_stock_id.is_(None))
            .filter(or_(
                MarketplaceListing.external_sku.ilike(like),
                MarketplaceListing.title.ilike(like),
                MarketplaceListing.external_listing_id.ilike(like),
                MarketplaceListing.asin.ilike(like),
                MarketplaceListing.fnsku.ilike(like),
            ))
            .order_by(MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc())
            .limit(per_page)
            .all()
        )
        unlinked_listings = [_listing_payload(row) for row in rows]

    unlinked_by_platform = defaultdict(list)
    for item in unlinked_listings:
        unlinked_by_platform[item.get("platform") or "Unknown"].append(item)

    total_listings = int(
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
        .count()
    )

    return jsonify({
        "success": True,
        "ok": True,
        "governed": True,
        "read_only": True,
        "truth_source": "WarehouseStock",
        "mode": "recent_group_event_table",
        "search_term": search,
        "page": page,
        "per_page": per_page,
        "total_stock": total_groups,
        "total_groups": total_groups,
        "total_listings": total_listings,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": max(1, page - 1),
        "next_page": min(total_pages, page + 1),
        "warehouse_products": warehouse_products,
        "unlinked_listings": unlinked_listings,
        "unlinked_by_platform": dict(unlinked_by_platform),
        "all_marketplace_listings": selected_listing_payloads + unlinked_listings,
        "all_stores": [],
        "warehouse": warehouse_products,
        "listings": selected_listing_payloads + unlinked_listings,
    }), 200
