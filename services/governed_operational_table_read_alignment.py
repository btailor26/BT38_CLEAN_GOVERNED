"""Shared operational-table read alignment.

Read-side only. This module must never own marketplace writes, Warehouse
quantity mutation, Product Linking relationships, MCF submission, push or sync.
It replaces existing registered page readers in-place so each URL keeps one
read owner while using the common 15/25/50/100 table contract.
"""
from __future__ import annotations

from flask import render_template, request


ALLOWED_PAGE_SIZES = (15, 25, 50, 100)


def _page_size(default: int = 15) -> int:
    try:
        value = int(request.args.get("per_page") or default)
    except (TypeError, ValueError):
        value = default
    return value if value in ALLOWED_PAGE_SIZES else default


def _amazon_fba_stock_page():
    """Existing FBA read model with shared bounded server pagination only."""
    from extensions import db
    from models import AmazonFBAInventory, Store

    page = request.args.get("page", 1, type=int)
    page = max(1, int(page or 1))
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
    stores_count = len(fba_stores)
    orphaned_count = AmazonFBAInventory.query.filter(
        AmazonFBAInventory.is_orphaned == True  # noqa: E712
    ).count()
    archived_count = AmazonFBAInventory.query.filter(
        AmazonFBAInventory.is_archived == True  # noqa: E712
    ).count()

    stats = {
        "total_skus": total_skus,
        "total_quantity": total_quantity,
        "stores_count": stores_count,
    }

    return render_template(
        "amazon_fba_stock.html",
        fba_items=fba_items,
        stats=stats,
        no_fba_store=no_fba_store,
        orphaned_count=orphaned_count,
        archived_count=archived_count,
        current_search=search,
        status_filter=status_filter,
        per_page=per_page,
    )


def install_operational_table_read_alignment(app):
    """Replace existing registered read handlers in-place; never add routes."""
    endpoint = "governed.amazon_fba_stock"
    if endpoint not in app.view_functions:
        return {
            "installed": False,
            "reason": "fba_endpoint_missing",
        }

    original = app.view_functions[endpoint]
    if getattr(original, "__bt38_operational_table_aligned__", False):
        return {
            "installed": True,
            "already_installed": True,
        }

    _amazon_fba_stock_page.__bt38_operational_table_aligned__ = True
    _amazon_fba_stock_page.__bt38_original__ = original
    app.view_functions[endpoint] = _amazon_fba_stock_page

    return {
        "installed": True,
        "endpoint": endpoint,
        "page_sizes": ALLOWED_PAGE_SIZES,
        "read_only": True,
        "new_route": False,
    }
