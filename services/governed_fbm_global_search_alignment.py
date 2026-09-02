"""Server-side FBM search and workflow tabs over persisted order history.

The FBM page normally loads only a bounded window. Search and workflow tabs filter
persisted MarketplaceOrder/FBMShipment truth before the visible limit is applied,
so the browser never becomes the authority for Ready, Dispatched, SDS,
Replacements or Refunds. This module is read-only: no marketplace/provider calls,
no hydration, no inventory mutation and no order/shipment writes.

MarketplaceOrder history can contain more than one persisted row for the same
marketplace order. The FBM workspace therefore resolves exactly one canonical row
per (store_id, marketplace_order_id) before queue classification. Shipment truth,
marketplace lifecycle truth and an already processed canonical row outrank a later
pending/import fallback row. Database row recency is only the final tie-breaker.
"""
from __future__ import annotations

import re
from html import escape
from urllib.parse import quote_plus, urlencode

from flask import g, request
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder, Store, WarehouseStock
from governed_fbm_routes import _platform, _shipment_map


_MAX_SEARCH_LENGTH = 200
_WORKFLOW_MAX_ROWS = 5000
_WORKFLOW_CANDIDATE_MULTIPLIER = 4
_CANCELLED_STATUSES = {"cancelled", "canceled", "cancelled_by_buyer", "cancelled_by_seller"}
_REPLACEMENT_TERMS = ("replacement", "replaced")
_REFUND_TERMS = ("refund", "refunded", "return", "returned", "inr", "case", "claim", "dispute", "issue")
_DISPATCHED_STATUS_TERMS = ("shipped", "dispatched", "delivered", "fulfilled", "completed")
_WORKFLOW_TABS = {"ready_dispatch", "dispatched", "sds", "replacements", "refunds"}


def _search_term() -> str:
    return str(request.args.get("search") or request.args.get("q") or "").strip()[:_MAX_SEARCH_LENGTH]


def _workflow_tab() -> str:
    value = str(request.args.get("fbm_tab") or "").strip().lower()
    return value if value in _WORKFLOW_TABS else ""


def _escaped_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _status_reason(status: str) -> str | None:
    value = str(status or "").strip().lower()
    if any(term in value for term in _REPLACEMENT_TERMS):
        return "replacements"
    if any(term in value for term in _REFUND_TERMS):
        return "refunds"
    return None


def _sds_committed(shipment) -> bool:
    if shipment is None or str(getattr(shipment, "provider", "") or "").strip().lower() != "sds":
        return False
    purchase_status = str(getattr(shipment, "purchase_status", "") or "").strip().lower()
    return bool(
        getattr(shipment, "label_purchased_at", None)
        or getattr(shipment, "carrier_accepted_at", None)
        or getattr(shipment, "first_movement_at", None)
        or getattr(shipment, "delivered_at", None)
        or getattr(shipment, "tracking_number", None)
        or purchase_status in {"confirmed", "purchased", "committed"}
    )


def _canonical_order_rank(row: MarketplaceOrder) -> tuple[int, ...]:
    """Choose one persisted MarketplaceOrder authority for one marketplace order.

    A later duplicate row must never erase stronger marketplace/shipment truth.
    Issue/cancellation truth wins first, then dispatch truth, then a row already
    processed by the canonical order path. The numeric row id is only a final
    tie-breaker after business truth has been compared.
    """
    status = str(getattr(row, "status", "") or "").strip().lower()
    issue_or_cancel = bool(
        _status_reason(status)
        or status in _CANCELLED_STATUSES
        or status.startswith("cancel")
    )
    dispatch_truth = bool(
        any(term in status for term in _DISPATCHED_STATUS_TERMS)
        or getattr(row, "tracking_number", None)
        or getattr(row, "shipped_at", None)
    )
    processed_truth = bool(
        getattr(row, "processed_at", None)
        or status == "processed"
    )
    return (
        1 if issue_or_cancel else 0,
        1 if dispatch_truth else 0,
        1 if processed_truth else 0,
        int(getattr(row, "id", 0) or 0),
    )


def _canonical_order_rows(rows: list[MarketplaceOrder]) -> list[MarketplaceOrder]:
    """Collapse duplicate DB rows to one business-truth row per marketplace order."""
    selected: dict[tuple[int, str], MarketplaceOrder] = {}
    for row in rows:
        if row.store_id is None or not row.marketplace_order_id:
            continue
        key = (int(row.store_id), str(row.marketplace_order_id))
        current = selected.get(key)
        if current is None or _canonical_order_rank(row) > _canonical_order_rank(current):
            selected[key] = row
    return sorted(selected.values(), key=lambda row: int(row.id or 0), reverse=True)


def workflow_queue_for(row: MarketplaceOrder, shipment) -> str:
    """Classify one canonical persisted order using marketplace + physical shipment truth."""
    status = str(getattr(row, "status", "") or "").strip().lower()
    reason = _status_reason(status)
    if status in _CANCELLED_STATUSES or status.startswith("cancel"):
        return "excluded"
    if reason:
        return reason
    if _sds_committed(shipment):
        return "sds"
    dispatched = bool(
        getattr(row, "tracking_number", None)
        or getattr(row, "shipped_at", None)
        or (shipment and getattr(shipment, "tracking_number", None))
        or (shipment and getattr(shipment, "carrier_accepted_at", None))
        or (shipment and getattr(shipment, "first_movement_at", None))
        or (shipment and getattr(shipment, "delivered_at", None))
    )
    return "dispatched" if dispatched else "ready_dispatch"


def _row_matches_term(row: MarketplaceOrder, term: str) -> bool:
    if not term:
        return True
    needle = term.casefold()
    store = getattr(row, "store", None)
    warehouse = getattr(row, "warehouse_stock", None)
    values = (
        getattr(row, "marketplace_order_id", None),
        getattr(row, "marketplace_order_item_id", None),
        getattr(row, "sku", None),
        getattr(row, "tracking_number", None),
        getattr(row, "carrier", None),
        getattr(row, "status", None),
        getattr(store, "name", None) if store else None,
        getattr(store, "platform", None) if store else None,
        getattr(warehouse, "product_name", None) if warehouse else None,
    )
    return any(needle in str(value or "").casefold() for value in values)


def _persisted_workflow_snapshot() -> dict:
    """Return canonical persisted FBM rows grouped by workflow tab for this request.

    The read remains bounded, but duplicate DB rows are resolved by business truth
    rather than MAX(id). The snapshot is cached only for the current Flask request.
    """
    cached = getattr(g, "_bt38_fbm_workflow_snapshot", None)
    if cached is not None:
        return cached

    from services import governed_fbm_page_alignment as page_alignment

    eligible = (
        func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
        ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
    )
    query = (
        db.session.query(MarketplaceOrder)
        .filter(*eligible)
        .filter(MarketplaceOrder.store_id.isnot(None), MarketplaceOrder.marketplace_order_id.isnot(None))
        .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
        .order_by(MarketplaceOrder.id.desc())
    )
    platform_filter = str(request.args.get("platform") or "").strip().lower()
    if platform_filter:
        query = query.filter(MarketplaceOrder.store.has(Store.platform.ilike(platform_filter)))

    candidate_limit = (_WORKFLOW_MAX_ROWS * _WORKFLOW_CANDIDATE_MULTIPLIER) + 1
    candidates = query.limit(candidate_limit).all()
    candidate_truncated = len(candidates) >= candidate_limit
    rows = _canonical_order_rows(candidates)
    truncated = candidate_truncated or len(rows) > _WORKFLOW_MAX_ROWS
    rows = rows[:_WORKFLOW_MAX_ROWS]

    profiles = page_alignment._profile_map([
        row for row in rows if _platform(row).strip().lower() == "amazon"
    ])
    eligible_rows: list[MarketplaceOrder] = []
    for row in rows:
        key = (int(row.store_id), str(row.marketplace_order_id))
        profile = profiles.get(key) if _platform(row).strip().lower() == "amazon" else None
        if page_alignment._workspace_fbm_eligible(row, profile):
            eligible_rows.append(row)

    shipments = _shipment_map(eligible_rows)
    grouped = {name: [] for name in _WORKFLOW_TABS}
    term = _search_term()
    for row in eligible_rows:
        if not _row_matches_term(row, term):
            continue
        shipment = shipments.get((int(row.store_id), str(row.marketplace_order_id)))
        queue = workflow_queue_for(row, shipment)
        if queue in grouped:
            grouped[queue].append(row)

    snapshot = {
        "rows": grouped,
        "counts": {name: len(grouped[name]) for name in _WORKFLOW_TABS},
        "truncated": truncated,
    }
    g._bt38_fbm_workflow_snapshot = snapshot
    return snapshot


def workflow_counts() -> dict[str, int]:
    """Persisted DB-backed counts for the current FBM platform/search scope."""
    return dict(_persisted_workflow_snapshot()["counts"])


def _workflow_rows(limit: int):
    tab = _workflow_tab()
    if not tab:
        return None
    snapshot = _persisted_workflow_snapshot()
    rows = list(snapshot["rows"].get(tab) or [])
    has_more = len(rows) > limit or bool(snapshot["truncated"])
    return rows[:limit], has_more


def _search_rows(limit: int):
    """Search persisted FBM candidates, then resolve one canonical row per order."""
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

    candidate_limit = min(1200, max((limit + 1) * 8, limit + 1))
    candidates = (
        query
        .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
        .order_by(MarketplaceOrder.id.desc())
        .limit(candidate_limit)
        .all()
    )

    rows = _canonical_order_rows(candidates)
    if status_filter:
        filtered = []
        for row in rows:
            tracking_present = bool(str(getattr(row, "tracking_number", "") or "").strip())
            shipped_at_present = getattr(row, "shipped_at", None) is not None
            if status_filter == "tracking recorded" and not tracking_present:
                continue
            if status_filter == "dispatched" and (tracking_present or not shipped_at_present):
                continue
            if status_filter == "ready for fbm routing" and (tracking_present or shipped_at_present):
                continue
            filtered.append(row)
        rows = filtered

    has_more = len(rows) > limit or len(candidates) == candidate_limit
    return rows[:limit], has_more


def _search_form_html() -> str:
    term = _search_term()
    preserved = {}
    for name in ("platform", "status", "health_period", "health_date", "health_month", "fbm_tab"):
        value = str(request.args.get(name) or "").strip()
        if value:
            preserved[name] = value
    hidden = "".join(
        f'<input type="hidden" name="{escape(name)}" value="{escape(value)}">'
        for name, value in preserved.items()
    )
    clear_preserved = {key: value for key, value in preserved.items() if key != "fbm_tab"}
    clear_url = f"{request.path}?{urlencode(clear_preserved)}" if clear_preserved else request.path
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
        '<span class="small text-muted">Searches persisted FBM truth, not only loaded rows.</span>'
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


def _preserve_scope_in_expand_links(html: str) -> str:
    values = {}
    term = _search_term()
    tab = _workflow_tab()
    if term:
        values["search"] = term
    if tab:
        values["fbm_tab"] = tab
    if not values:
        return html

    def repl(match):
        href = match.group(1)
        additions = []
        for key, value in values.items():
            if f"{key}=" not in href:
                additions.append(f"{key}={quote_plus(value)}")
        if not additions:
            return match.group(0)
        separator = "&" if "?" in href else "?"
        return f'href="{href}{separator}{"&".join(additions)}"'

    return re.sub(r'href="([^\"]*?/fbm\?[^\"]*)"', repl, html)


def install_governed_fbm_global_search_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_global_search_alignment_installed", False):
        return

    from services import governed_fbm_page_alignment as page_alignment

    original_rows = page_alignment._latest_distinct_fbm_rows
    original_expand = page_alignment._expand_control

    def search_and_tab_aware_rows(limit: int):
        workflow = _workflow_rows(limit)
        if workflow is not None:
            return workflow
        searched = _search_rows(limit)
        return searched if searched is not None else original_rows(limit)

    def scope_aware_expand(html: str, *, visible_limit: int, has_more: bool) -> str:
        rendered = original_expand(html, visible_limit=visible_limit, has_more=has_more)
        return _preserve_scope_in_expand_links(rendered)

    page_alignment._latest_distinct_fbm_rows = search_and_tab_aware_rows
    page_alignment._expand_control = scope_aware_expand

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
        "BT38 FBM persisted scope aligned: one canonical MarketplaceOrder per marketplace order; search and workflow tabs use business truth before row recency; no marketplace/provider reads"
    )