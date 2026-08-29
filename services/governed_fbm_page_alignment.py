"""Bound the existing FBM page read without introducing a second workflow.

This alignment keeps the registered /fbm endpoint, existing template, shipping
handlers and persisted authorities unchanged. It only replaces the expensive
page-read implementation so an ordinary refresh hydrates the latest 15 distinct
FBM orders. Older records are user-expanded in 15-order increments.

For eBay, the workspace exposes only capabilities BT38 can actually execute.
The legacy Seller Hub redirect is neutralised: connected in-BT38 providers and
manual governed dispatch remain available, while native eBay label purchase is
shown as unavailable until a real governed adapter exists.

No marketplace/provider calls, DB writes, reconciliation, scheduler or MCF path
is introduced here.
"""
from __future__ import annotations

from urllib.parse import urlencode

from flask import request, render_template
from flask_login import login_required
from sqlalchemy import func, tuple_
from sqlalchemy.orm import joinedload

from extensions import db
from fbm_models import FBMOrderProfile
from models import MarketplaceOrder
from governed_fbm_routes import (
    _is_fbm_eligible,
    _marketplace_shipping_mode,
    _platform,
    _route_state,
    _shipment_map,
    _store_name,
)
from services.fbm_shipping_state import provider_case_eligibility, shipment_confirmation_state


_FBM_PAGE_SIZE = 15
_FBM_MAX_EXPANDED = 300
_FBM_DISCOVERY_MULTIPLIER = 4


def _requested_limit() -> int:
    try:
        requested = int(request.args.get("limit") or _FBM_PAGE_SIZE)
    except (TypeError, ValueError):
        requested = _FBM_PAGE_SIZE
    requested = max(_FBM_PAGE_SIZE, requested)
    return min(_FBM_MAX_EXPANDED, ((requested + _FBM_PAGE_SIZE - 1) // _FBM_PAGE_SIZE) * _FBM_PAGE_SIZE)


def _profile_map(rows: list[MarketplaceOrder]) -> dict[tuple[int, str], FBMOrderProfile]:
    identities = sorted({
        (int(row.store_id), str(row.marketplace_order_id))
        for row in rows
        if row.store_id is not None and row.marketplace_order_id
    })
    if not identities:
        return {}

    profiles = (
        db.session.query(FBMOrderProfile)
        .filter(tuple_(FBMOrderProfile.store_id, FBMOrderProfile.marketplace_order_id).in_(identities))
        .order_by(FBMOrderProfile.id.desc())
        .all()
    )
    result: dict[tuple[int, str], FBMOrderProfile] = {}
    for profile in profiles:
        key = (int(profile.store_id), str(profile.marketplace_order_id))
        if key not in result:
            result[key] = profile
    return result


def _latest_distinct_fbm_rows(limit: int) -> tuple[list[MarketplaceOrder], bool]:
    """Read the newest persisted FBM rows from a bounded candidate window.

    Marketplace events can leave more than one persisted row for an order. The
    previous read grouped the complete eligible history on every page request.
    This keeps the same latest-row authority but first limits discovery to the
    newest candidate rows, then de-duplicates that small set in process.
    """
    eligible = (
        func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
        ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
    )

    query = db.session.query(MarketplaceOrder).filter(*eligible)

    platform_filter = str(request.args.get("platform") or "").strip().lower()
    status_filter = str(request.args.get("status") or "").strip().lower()
    if platform_filter:
        query = query.filter(MarketplaceOrder.store.has(platform=platform_filter))

    tracking_present = MarketplaceOrder.tracking_number.isnot(None) & (MarketplaceOrder.tracking_number != "")
    if status_filter == "tracking recorded":
        query = query.filter(tracking_present)
    elif status_filter == "dispatched":
        query = query.filter(~tracking_present, MarketplaceOrder.shipped_at.isnot(None))
    elif status_filter == "ready for fbm routing":
        query = query.filter(~tracking_present, MarketplaceOrder.shipped_at.is_(None))

    candidate_limit = min(_FBM_MAX_EXPANDED * _FBM_DISCOVERY_MULTIPLIER, max(limit + 1, (limit + 1) * _FBM_DISCOVERY_MULTIPLIER))
    candidates = (
        query
        .options(
            joinedload(MarketplaceOrder.store),
            joinedload(MarketplaceOrder.warehouse_stock),
        )
        .order_by(MarketplaceOrder.id.desc())
        .limit(candidate_limit)
        .all()
    )

    rows: list[MarketplaceOrder] = []
    seen: set[tuple[int, str]] = set()
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


def _workspace_shipping_mode(row: MarketplaceOrder, platform: str, profile: FBMOrderProfile | None) -> dict:
    """Expose only shipping routes that are executable from the BT38 workspace."""
    mode = dict(_marketplace_shipping_mode(row, platform, profile))
    if platform.strip().lower() == "ebay":
        mode.update({
            "recommended": "Packlink / connected carrier",
            "marketplace_buy_shipping": False,
            "external_provider": True,
            "manual": True,
            "prime_locked": False,
            "profile_known": True,
            "reason": "Use an in-BT38 connected provider or governed manual dispatch. Native eBay label purchase is not exposed until BT38 has a supported adapter.",
        })
    return mode


def _neutralise_legacy_ebay_handoff(html: str) -> str:
    """Compatibility no-op: preserve the existing eBay shipping click handler.

    The former overlay forced "eBay postage unavailable" and said
    "Native eBay label purchase is not enabled", which disabled the button after
    the existing FBM JavaScript had enabled it. Do not override that click path.
    """
    return html


def _expand_control(html: str, *, visible_limit: int, has_more: bool) -> str:
    """Add the bounded expansion control to the existing FBM card only."""
    if not html:
        return html

    params = {}
    platform_filter = str(request.args.get("platform") or "").strip()
    status_filter = str(request.args.get("status") or "").strip()
    if platform_filter:
        params["platform"] = platform_filter
    if status_filter:
        params["status"] = status_filter

    actions = []
    if visible_limit > _FBM_PAGE_SIZE:
        collapse_params = dict(params)
        collapse_params["limit"] = _FBM_PAGE_SIZE
        actions.append(
            f'<a class="btn btn-sm btn-outline-secondary" href="{request.path}?{urlencode(collapse_params)}">Show latest 15</a>'
        )
    if has_more and visible_limit < _FBM_MAX_EXPANDED:
        expand_params = dict(params)
        expand_params["limit"] = min(_FBM_MAX_EXPANDED, visible_limit + _FBM_PAGE_SIZE)
        actions.append(
            f'<a id="fbmExpandOrders" class="btn btn-sm btn-outline-primary" href="{request.path}?{urlencode(expand_params)}">Show 15 more</a>'
        )

    if not actions:
        return html

    control = (
        '<div class="card-footer d-flex justify-content-between align-items-center flex-wrap gap-2">'
        f'<span class="small text-muted">Showing the latest {visible_limit} FBM orders. Older orders load only when expanded.</span>'
        f'<div class="d-flex gap-2">{"".join(actions)}</div>'
        '</div>'
    )
    marker = "</tbody></table></div>\n</div>"
    if marker not in html:
        return html
    return html.replace(marker, f"</tbody></table></div>\n{control}\n</div>", 1)


def install_governed_fbm_page_alignment(app) -> None:
    """Replace only the existing /fbm page read; all action endpoints stay put."""
    if getattr(app, "_bt38_fbm_page_alignment_installed", False):
        return

    endpoint = "governed_fbm.fbm_page"
    if endpoint not in app.view_functions:
        raise RuntimeError("governed FBM page endpoint is not registered")

    @login_required
    def bounded_fbm_page():
        platform_filter = str(request.args.get("platform") or "").strip().lower()
        status_filter = str(request.args.get("status") or "").strip().lower()
        visible_limit = _requested_limit()

        rows, has_more = _latest_distinct_fbm_rows(visible_limit)
        shipments = _shipment_map(rows)
        profiles = _profile_map(rows)

        orders = []
        for row in rows:
            if not _is_fbm_eligible(row):
                continue
            key = (int(row.store_id), str(row.marketplace_order_id))
            platform = _platform(row)
            route_state = _route_state(row)
            profile = profiles.get(key)
            shipment = shipments.get(key)
            shipment_state = shipment_confirmation_state(shipment) if shipment else "not_started"
            case = (
                provider_case_eligibility(shipment)
                if shipment
                else {"eligible": False, "reason": "shipment_not_started", "case_type": None}
            )
            mapping_review = getattr(shipment, "mapping_review", None) if shipment else None
            orders.append({
                "order": row,
                "platform": platform,
                "store_name": _store_name(row),
                "route_state": route_state,
                "shipping_mode": _workspace_shipping_mode(row, platform, profile),
                "shipment": shipment,
                "shipment_state": shipment_state,
                "case": case,
                "profile": profile,
                "mapping_review": mapping_review,
            })

        counts = {
            "total": len(orders),
            "ready": sum(1 for item in orders if item["route_state"] == "Ready for FBM routing"),
            "tracking": sum(1 for item in orders if item["route_state"] == "Tracking recorded"),
            "dispatched": sum(1 for item in orders if item["route_state"] == "Dispatched"),
            "marketplace_shipping": sum(1 for item in orders if item["shipping_mode"]["marketplace_buy_shipping"]),
            "awaiting_acceptance": sum(1 for item in orders if item["shipment_state"] == "awaiting_carrier_acceptance"),
            "overdue": sum(1 for item in orders if item["shipment_state"] == "acceptance_overdue"),
            "mapping_review": sum(
                1 for item in orders
                if item["mapping_review"] and item["mapping_review"].status == "under_review"
            ),
        }

        html = render_template(
            "fbm.html",
            orders=orders,
            counts=counts,
            platform_filter=platform_filter,
            status_filter=status_filter,
        )
        html = _neutralise_legacy_ebay_handoff(html)
        return _expand_control(html, visible_limit=visible_limit, has_more=has_more)

    app.view_functions[endpoint] = bounded_fbm_page
    app._bt38_fbm_page_alignment_installed = True
    app.logger.info(
        "BT38 FBM page alignment installed: bounded latest-order discovery with 15-order expansion and in-workspace eBay capability gating"
    )