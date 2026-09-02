"""Align FBM reads to the existing BT38 browser-session architecture.

Warehouse is the reference model: load one bounded governed snapshot, eager/bulk
its relationships once, then let the browser session own search, workflow tabs
and pagination.  This module never calls a marketplace/provider and never writes
orders, shipments or inventory.

MarketplaceOrder history can contain more than one row for the same marketplace
order.  The session snapshot therefore keeps the existing business-truth
canonical ranking: issue/cancellation truth, dispatch truth and processed truth
all outrank database row recency.
"""
from __future__ import annotations

from html import escape

from flask import g, request
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from extensions import db
from models import MarketplaceOrder
from governed_fbm_routes import _platform


_MAX_SEARCH_LENGTH = 200
_SESSION_MAX_ROWS = 300
_SESSION_CANDIDATE_MULTIPLIER = 4
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
    """Choose one persisted MarketplaceOrder authority for one marketplace order."""
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
    processed_truth = bool(getattr(row, "processed_at", None) or status == "processed")
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
    """Classify one canonical persisted order using persisted marketplace/shipment truth."""
    status = str(getattr(row, "status", "") or "").strip().lower()
    reason = _status_reason(status)
    if status in _CANCELLED_STATUSES or status.startswith("cancel"):
        return "excluded"
    if reason:
        return reason
    if _sds_committed(shipment):
        return "sds"
    dispatched = bool(
        any(term in status for term in _DISPATCHED_STATUS_TERMS)
        or getattr(row, "tracking_number", None)
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


def _session_snapshot_rows() -> tuple[list[MarketplaceOrder], bool]:
    """Load the FBM operational dataset once for the current browser page request."""
    cached = getattr(g, "_bt38_fbm_session_rows", None)
    if cached is not None:
        return list(cached), bool(getattr(g, "_bt38_fbm_session_truncated", False))

    from services import governed_fbm_page_alignment as page_alignment

    eligible = (
        func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
        ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
    )
    candidate_limit = (_SESSION_MAX_ROWS * _SESSION_CANDIDATE_MULTIPLIER) + 1
    candidates = (
        db.session.query(MarketplaceOrder)
        .filter(*eligible)
        .filter(MarketplaceOrder.store_id.isnot(None), MarketplaceOrder.marketplace_order_id.isnot(None))
        .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
        .order_by(MarketplaceOrder.id.desc())
        .limit(candidate_limit)
        .all()
    )
    candidate_truncated = len(candidates) >= candidate_limit
    canonical = _canonical_order_rows(candidates)
    truncated = candidate_truncated or len(canonical) > _SESSION_MAX_ROWS
    canonical = canonical[:_SESSION_MAX_ROWS]

    profiles = page_alignment._profile_map([
        row for row in canonical if _platform(row).strip().lower() == "amazon"
    ])
    rows: list[MarketplaceOrder] = []
    for row in canonical:
        key = (int(row.store_id), str(row.marketplace_order_id))
        profile = profiles.get(key) if _platform(row).strip().lower() == "amazon" else None
        if page_alignment._workspace_fbm_eligible(row, profile):
            rows.append(row)

    g._bt38_fbm_session_rows = rows
    g._bt38_fbm_session_truncated = truncated
    return list(rows), truncated


def _persisted_workflow_snapshot() -> dict:
    """Classify the already-loaded request/session rows without another order scan."""
    cached = getattr(g, "_bt38_fbm_workflow_snapshot", None)
    if cached is not None:
        return cached

    from services import governed_fbm_page_alignment as page_alignment

    rows, truncated = _session_snapshot_rows()
    shipments = page_alignment._shipment_map(rows)
    grouped = {name: [] for name in _WORKFLOW_TABS}
    for row in rows:
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
    return dict(_persisted_workflow_snapshot()["counts"])


def _workflow_rows(limit: int):
    tab = _workflow_tab()
    if not tab:
        return None
    snapshot = _persisted_workflow_snapshot()
    rows = list(snapshot["rows"].get(tab) or [])
    return rows[:limit], len(rows) > limit or bool(snapshot["truncated"])


def _search_rows(limit: int):
    term = _search_term()
    if not term:
        return None
    rows, truncated = _session_snapshot_rows()
    matched = [row for row in rows if _row_matches_term(row, term)]
    return matched[:limit], len(matched) > limit or truncated


def _search_form_html() -> str:
    return (
        '<form id="bt38FbmGlobalSearch" class="d-flex gap-2 align-items-center flex-wrap" '
        'method="get" action="/fbm" onsubmit="event.preventDefault();return false;">'
        '<input id="bt38FbmGlobalSearchInput" class="form-control form-control-sm" style="width:min(360px,70vw)" '
        'type="search" name="search" autocomplete="off" '
        'placeholder="Search loaded FBM orders, SKU, tracking or product">'
        '<button class="btn btn-sm btn-outline-primary" type="submit">Search</button>'
        '<button id="bt38FbmGlobalSearchClear" class="btn btn-sm btn-outline-secondary" type="button">Clear</button>'
        '<span class="small text-muted">Search stays in this browser session.</span>'
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


def install_governed_fbm_global_search_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_global_search_alignment_installed", False):
        return

    from services import governed_fbm_page_alignment as page_alignment

    original_profile_map = page_alignment._profile_map
    original_shipment_map = page_alignment._shipment_map

    def request_cached_profile_map(rows):
        cache = getattr(g, "_bt38_fbm_profile_cache", None)
        if cache is None:
            cache = {}
            g._bt38_fbm_profile_cache = cache
        missing = []
        for row in rows:
            if row.store_id is None or not row.marketplace_order_id:
                continue
            key = (int(row.store_id), str(row.marketplace_order_id))
            if key not in cache:
                missing.append(row)
        if missing:
            cache.update(original_profile_map(missing))
        return {
            (int(row.store_id), str(row.marketplace_order_id)): cache.get((int(row.store_id), str(row.marketplace_order_id)))
            for row in rows
            if row.store_id is not None and row.marketplace_order_id
            and cache.get((int(row.store_id), str(row.marketplace_order_id))) is not None
        }

    def request_cached_shipment_map(rows):
        cache = getattr(g, "_bt38_fbm_shipment_cache", None)
        loaded = getattr(g, "_bt38_fbm_shipment_keys_loaded", None)
        if cache is None:
            cache = {}
            g._bt38_fbm_shipment_cache = cache
        if loaded is None:
            loaded = set()
            g._bt38_fbm_shipment_keys_loaded = loaded
        keys = {
            (int(row.store_id), str(row.marketplace_order_id))
            for row in rows
            if row.store_id is not None and row.marketplace_order_id
        }
        missing_keys = keys - loaded
        if missing_keys:
            missing_rows = [
                row for row in rows
                if row.store_id is not None and row.marketplace_order_id
                and (int(row.store_id), str(row.marketplace_order_id)) in missing_keys
            ]
            cache.update(original_shipment_map(missing_rows))
            loaded.update(missing_keys)
        return {key: cache.get(key) for key in keys if cache.get(key) is not None}

    def session_rows(_limit: int):
        rows, truncated = _session_snapshot_rows()
        return rows, truncated

    def no_server_expand(html: str, *, visible_limit: int, has_more: bool) -> str:
        return html

    page_alignment._profile_map = request_cached_profile_map
    page_alignment._shipment_map = request_cached_shipment_map
    page_alignment._latest_distinct_fbm_rows = session_rows
    page_alignment._expand_control = no_server_expand

    @app.after_request
    def bt38_fbm_session_search_response(response):
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
        "BT38 FBM browser session aligned: one canonical DB snapshot; local search/tabs/page; request-cached profiles and shipments; no marketplace/provider reads"
    )
