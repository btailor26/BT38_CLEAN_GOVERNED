"""Server-side FBM search over persisted order history.

The FBM page normally loads only the newest bounded window. Search is different:
when the user supplies a search term, filter the existing MarketplaceOrder query
before the display limit is applied so a matching persisted order can be found
regardless of whether it is in the currently loaded 15/30/100 rows.

This module is read-only. It does not call a marketplace/provider, hydrate an
order, mutate inventory, or create another order source.
"""
from __future__ import annotations

import re
from html import escape
from urllib.parse import quote_plus, urlencode

from flask import request
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from models import MarketplaceOrder, Store, WarehouseStock


_MAX_SEARCH_LENGTH = 200


def _search_term() -> str:
    return str(request.args.get("search") or request.args.get("q") or "").strip()[:_MAX_SEARCH_LENGTH]


def _escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_rows(limit: int):
    """Search persisted FBM candidates before applying the visible-row limit."""
    term = _search_term()
    if not term:
        return None

    pattern = f"%{_escaped_like(term)}%"
    eligible = (
        func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
        ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
    )
    query = db.session.query(MarketplaceOrder).filter(*eligible)

    platform_filter = str(request.args.get("platform") or "").strip().lower()
    status_filter = str(request.args.get("status") or "").strip().lower()
    if platform_filter:
        query = query.filter(MarketplaceOrder.store.has(Store.platform.ilike(platform_filter)))

    tracking_present = MarketplaceOrder.tracking_number.isnot(None) & (MarketplaceOrder.tracking_number != "")
    if status_filter == "tracking recorded":
        query = query.filter(tracking_present)
    elif status_filter == "dispatched":
        query = query.filter(~tracking_present, MarketplaceOrder.shipped_at.isnot(None))
    elif status_filter == "ready for fbm routing":
        query = query.filter(~tracking_present, MarketplaceOrder.shipped_at.is_(None))

    query = query.filter(or_(
        MarketplaceOrder.marketplace_order_id.ilike(pattern, escape="\\"),
        MarketplaceOrder.marketplace_order_item_id.ilike(pattern, escape="\\"),
        MarketplaceOrder.sku.ilike(pattern, escape="\\"),
        MarketplaceOrder.tracking_number.ilike(pattern, escape="\\"),
        MarketplaceOrder.carrier.ilike(pattern, escape="\\"),
        MarketplaceOrder.status.ilike(pattern, escape="\\"),
        MarketplaceOrder.store.has(or_(
            Store.name.ilike(pattern, escape="\\"),
            Store.platform.ilike(pattern, escape="\\"),
        )),
        MarketplaceOrder.warehouse_stock.has(
            WarehouseStock.product_name.ilike(pattern, escape="\\")
        ),
    ))

    # The filter is executed by Postgres across the persisted order history.
    # Only matching rows are bounded afterwards, so page size never controls
    # whether a record can be discovered.
    candidate_limit = min(1200, max((limit + 1) * 4, limit + 1))
    candidates = (
        query
        .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
        .order_by(MarketplaceOrder.id.desc())
        .limit(candidate_limit)
        .all()
    )

    rows = []
    seen = set()
    for row in candidates:
        if row.store_id is None or not row.marketplace_order_id:
            continue
        key = (int(row.store_id), str(row.marketplace_order_id))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if len(rows) >= limit + 1:
            break

    rows.sort(key=lambda row: (row.created_at is not None, row.created_at, row.id), reverse=True)
    has_more = len(rows) > limit or len(candidates) == candidate_limit
    return rows[:limit], has_more


def _search_form_html() -> str:
    term = _search_term()
    preserved = {}
    for name in ("platform", "status", "health_period", "health_date", "health_month"):
        value = str(request.args.get(name) or "").strip()
        if value:
            preserved[name] = value
    hidden = "".join(
        f'<input type="hidden" name="{escape(name)}" value="{escape(value)}">'
        for name, value in preserved.items()
    )
    clear_url = f"{request.path}?{urlencode(preserved)}" if preserved else request.path
    clear = (
        f'<a class="btn btn-sm btn-outline-secondary" href="{escape(clear_url)}">Clear</a>'
        if term else ""
    )
    return (
        '<form id="bt38FbmGlobalSearch" class="d-flex gap-2 align-items-center flex-wrap" method="get" action="/fbm">'
        f'{hidden}'
        '<input id="bt38FbmGlobalSearchInput" class="form-control form-control-sm" style="width:min(360px,70vw)" '
        'type="search" name="search" autocomplete="off" '
        'placeholder="Search all FBM orders, SKU, tracking or product" '
        f'value="{escape(term)}">'
        '<button class="btn btn-sm btn-outline-primary" type="submit">Search</button>'
        f'{clear}'
        '<span class="small text-muted">Searches all persisted FBM history, not only loaded rows.</span>'
        '</form>'
    )


def _inject_search_form(html: str) -> str:
    if 'id="bt38FbmGlobalSearch"' in html:
        return html
    marker = '<div class="card-header d-flex justify-content-between align-items-center flex-wrap gap-2"><div><span class="fw-semibold">FBM Orders</span>'
    index = html.find(marker)
    if index < 0:
        return html
    card_start = html.rfind('<div class="card">', 0, index + 1)
    if card_start < 0:
        return html
    search_bar = '<div class="card-header border-bottom-0 pb-0">' + _search_form_html() + '</div>\n'
    return html[:card_start] + '<div class="card">\n' + search_bar + html[card_start + len('<div class="card">'):]


def _preserve_search_in_expand_links(html: str) -> str:
    term = _search_term()
    if not term:
        return html
    encoded = quote_plus(term)

    def repl(match):
        href = match.group(1)
        if "search=" in href:
            return match.group(0)
        separator = "&" if "?" in href else "?"
        return f'href="{href}{separator}search={encoded}"'

    return re.sub(r'href="([^\"]*?/fbm\?[^\"]*)"', repl, html)


def install_governed_fbm_global_search_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_global_search_alignment_installed", False):
        return

    from services import governed_fbm_page_alignment as page_alignment

    original_rows = page_alignment._latest_distinct_fbm_rows
    original_expand = page_alignment._expand_control

    def search_aware_rows(limit: int):
        searched = _search_rows(limit)
        return searched if searched is not None else original_rows(limit)

    def search_aware_expand(html: str, *, visible_limit: int, has_more: bool) -> str:
        rendered = original_expand(html, visible_limit=visible_limit, has_more=has_more)
        return _preserve_search_in_expand_links(rendered)

    page_alignment._latest_distinct_fbm_rows = search_aware_rows
    page_alignment._expand_control = search_aware_expand

    @app.after_request
    def bt38_fbm_global_search_response(response):
        path = request.path.rstrip("/") or "/"
        if (
            path == "/fbm"
            and response.status_code == 200
            and response.content_type
            and "text/html" in response.content_type
        ):
            response.set_data(_inject_search_form(response.get_data(as_text=True)))
        return response

    app._bt38_fbm_global_search_alignment_installed = True
    app.logger.info(
        "BT38 FBM global search aligned: persisted DB filtering occurs before the visible page limit; no marketplace/provider reads"
    )
