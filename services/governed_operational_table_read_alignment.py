"""Shared operational-table read alignment.

Read-side only. This module must never own marketplace writes, Warehouse
quantity mutation, Product Linking relationships, MCF submission, push or sync.
It intercepts only matching GET page reads before legacy page readers run, so
there is one active read path without adding duplicate URLs or workers.
"""
from __future__ import annotations

from types import SimpleNamespace

from flask import render_template, request


ALLOWED_PAGE_SIZES = (15, 25, 50, 100)


def _page_size(default: int = 15) -> int:
    try:
        value = int(request.args.get("per_page") or default)
    except (TypeError, ValueError):
        value = default
    return value if value in ALLOWED_PAGE_SIZES else default


def _safe_page() -> int:
    try:
        return max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        return 1


def _amazon_fba_stock_page():
    """Existing FBA truth model with shared bounded server pagination only."""
    from extensions import db
    from models import AmazonFBAInventory, Store

    page = _safe_page()
    per_page = _page_size(15)
    search = (request.args.get("search") or "").strip()
    status_filter = request.args.get("status", "active")

    fba_stores = Store.query.filter(
        Store.platform.ilike("amazon"),
        Store.fba_import_enabled == True,  # noqa: E712
    ).all()
    no_fba_store = len(fba_stores) == 0

    query = AmazonFBAInventory.query
    if search:
        like = f"%{search}%"
        query = query.filter(db.or_(
            AmazonFBAInventory.seller_sku.ilike(like),
            AmazonFBAInventory.title.ilike(like),
            AmazonFBAInventory.asin.ilike(like),
            AmazonFBAInventory.fnsku.ilike(like),
        ))

    if status_filter == "orphaned":
        query = query.filter(AmazonFBAInventory.is_orphaned == True)  # noqa: E712
    elif status_filter == "archived":
        query = query.filter(AmazonFBAInventory.is_archived == True)  # noqa: E712
    elif status_filter == "all":
        pass
    else:
        status_filter = "active"
        query = query.filter(AmazonFBAInventory.is_archived == False)  # noqa: E712

    fba_items = query.order_by(AmazonFBAInventory.seller_sku.asc()).paginate(
        page=page,
        per_page=per_page,
        error_out=False,
    )

    total_skus = AmazonFBAInventory.query.filter(
        AmazonFBAInventory.is_archived == False  # noqa: E712
    ).count()
    total_quantity = (
        db.session.query(
            db.func.coalesce(db.func.sum(AmazonFBAInventory.available_quantity), 0)
        ).scalar()
        or 0
    )
    orphaned_count = AmazonFBAInventory.query.filter(
        AmazonFBAInventory.is_orphaned == True  # noqa: E712
    ).count()
    archived_count = AmazonFBAInventory.query.filter(
        AmazonFBAInventory.is_archived == True  # noqa: E712
    ).count()

    return render_template(
        "amazon_fba_stock.html",
        fba_items=fba_items,
        stats={
            "total_skus": total_skus,
            "total_quantity": total_quantity,
            "stores_count": len(fba_stores),
        },
        no_fba_store=no_fba_store,
        orphaned_count=orphaned_count,
        archived_count=archived_count,
        current_search=search,
        status_filter=status_filter,
        per_page=per_page,
    )


def _mcf_orders_page():
    """Bound the MCF list read and keep all write/action handlers untouched."""
    import governed_mcf_routes as mcf_routes

    page = _safe_page()
    per_page = _page_size(15)
    search = (request.args.get("search") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()

    # Idle landing/page navigation reads only enough recent candidates to build
    # the requested page plus one look-ahead row. Explicit search/status filters
    # may inspect the existing bounded 250-order read set because the user has
    # deliberately asked to query older records.
    if search or status:
        candidate_limit = 250
    else:
        candidate_limit = min(250, (page * per_page) + 1)

    rows = mcf_routes._bulk_orders(limit=candidate_limit)

    def _matches(row):
        if status and str(row.get("state") or "").lower() != status:
            return False
        if not search:
            return True
        anchor = row.get("anchor")
        fields = [
            getattr(anchor, "marketplace_order_id", ""),
            getattr(getattr(anchor, "store", None), "name", ""),
            " ".join(str(value) for value in (row.get("group_ids") or [])),
        ]
        fields.extend(str(line.sku or "") for line in (row.get("lines") or []))
        return search in " ".join(fields).lower()

    filtered = [row for row in rows if _matches(row)]
    start = (page - 1) * per_page
    page_rows = filtered[start:start + per_page]
    has_next = len(filtered) > start + per_page

    return render_template(
        "mcf_orders.html",
        orders=page_rows,
        table_page=SimpleNamespace(
            page=page,
            per_page=per_page,
            has_prev=page > 1,
            has_next=has_next,
            prev_page=max(1, page - 1),
            next_page=page + 1,
            visible=len(page_rows),
        ),
        current_search=request.args.get("search") or "",
        current_status=status,
        per_page=per_page,
        server_paged=True,
    )


def install_operational_table_read_alignment(app):
    """Install one read-only pre-route interceptor; never add duplicate routes."""
    if getattr(app, "_bt38_operational_table_read_alignment", False):
        return {"installed": True, "already_installed": True}

    app._bt38_operational_table_read_alignment = True

    @app.before_request
    def _bt38_operational_table_read():
        if request.method != "GET":
            return None
        path = request.path.rstrip("/")
        if path == "/amazon-fba-stock":
            return _amazon_fba_stock_page()
        if path == "/orders-mcf":
            return _mcf_orders_page()
        return None

    return {
        "installed": True,
        "page_sizes": ALLOWED_PAGE_SIZES,
        "read_only": True,
        "new_route": False,
        "paths": ("/amazon-fba-stock", "/orders-mcf"),
    }
