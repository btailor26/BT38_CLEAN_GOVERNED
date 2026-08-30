"""Bound and align the existing FBM workspace without introducing a second workflow.

The registered /fbm page remains the single FBM read surface. Ordinary order-table
reads stay bounded; the compact health/action area is calculated from persisted BT38
facts for the selected day/month and never calls a marketplace or provider. Shipping
options still read only explicitly selected order IDs. Existing Packlink/QZ controls
are moved out of the everyday header and kept beside the shipping workspace.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import jsonify, request, render_template
from flask_login import login_required
from sqlalchemy import func, or_, tuple_
from sqlalchemy.orm import joinedload

from extensions import db
from fbm_models import FBMOrderProfile
from models import MarketplaceOrder, ProductPackMapping
from governed_fbm_routes import (
    _is_fbm_eligible,
    _marketplace_shipping_mode,
    _platform,
    _route_state,
    _shipment_map,
    _shipping_provider_options,
    _store_name,
)
from services.fbm_shipping_state import provider_case_eligibility, shipment_confirmation_state


_FBM_PAGE_SIZE = 15
_FBM_MAX_EXPANDED = 300
_FBM_DISCOVERY_MULTIPLIER = 4
_FBM_MAX_SELECTED = 50
_FBM_HEALTH_MAX_ROWS = 5000
_FBM_HEALTH_TZ = ZoneInfo("Europe/London")


def _requested_limit() -> int:
    try:
        requested = int(request.args.get("limit") or _FBM_PAGE_SIZE)
    except (TypeError, ValueError):
        requested = _FBM_PAGE_SIZE
    requested = max(_FBM_PAGE_SIZE, requested)
    return min(_FBM_MAX_EXPANDED, ((requested + _FBM_PAGE_SIZE - 1) // _FBM_PAGE_SIZE) * _FBM_PAGE_SIZE)


def _selected_order_ids() -> list[int]:
    """Return only the explicit Shipping-options selection, capped defensively."""
    raw_ids = str(request.args.get("order_ids") or "")
    order_ids: list[int] = []
    for value in raw_ids.split(","):
        try:
            order_id = int(value.strip())
        except (TypeError, ValueError):
            continue
        if order_id > 0 and order_id not in order_ids:
            order_ids.append(order_id)
        if len(order_ids) >= _FBM_MAX_SELECTED:
            break
    return order_ids


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


def _workspace_fbm_eligible(row: MarketplaceOrder, profile: FBMOrderProfile | None = None) -> bool:
    """Require seller-fulfilled persisted truth before an order enters FBM."""
    if not _is_fbm_eligible(row):
        return False

    if _platform(row).strip().lower() != "amazon":
        return True

    fulfillment = str(getattr(row, "fulfillment_type", "") or "").strip().upper()
    profile_channel = str(getattr(profile, "fulfillment_channel", "") or "").strip().upper() if profile else ""

    if profile_channel in {"AFN", "FBA", "MCF"}:
        return False
    if profile_channel in {"MFN", "FBM"}:
        return True
    return fulfillment in {"MFN", "FBM"}


def _latest_distinct_fbm_rows(limit: int) -> tuple[list[MarketplaceOrder], bool]:
    """Read newest persisted FBM rows from a bounded candidate window."""
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

    candidate_limit = min(
        _FBM_MAX_EXPANDED * _FBM_DISCOVERY_MULTIPLIER,
        max(limit + 1, (limit + 1) * _FBM_DISCOVERY_MULTIPLIER),
    )
    candidates = (
        query
        .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
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
    """Expose only shipping routes executable from the BT38 workspace."""
    mode = dict(_marketplace_shipping_mode(row, platform, profile))
    if platform.strip().lower() == "ebay":
        mode.update({
            "recommended": "Packlink / connected carrier",
            "marketplace_buy_shipping": False,
            "external_provider": True,
            "manual": True,
            "prime_locked": False,
            "profile_known": True,
            "reason": "Use an in-BT38 connected provider or governed manual dispatch. eBay Shipping opens the exact order in Seller Hub; BT38 does not buy native eBay labels through an API.",
        })
    return mode


def _workspace_provider_options(row: MarketplaceOrder, profile: FBMOrderProfile | None) -> list[dict]:
    """Keep eBay Seller Hub handoff clickable without claiming native label API support."""
    options = [dict(option) for option in _shipping_provider_options(row, profile, None)]
    if _platform(row).strip().lower() == "ebay":
        for option in options:
            if str(option.get("provider") or "") != "ebay_shipping":
                continue
            option.update({
                "available": True,
                "recommended": False,
                "label_formats": [],
                "auto_print_supported": False,
                "requires_terms_acceptance": False,
                "message": "Open this exact order in eBay Seller Hub to check or buy marketplace postage. BT38 does not purchase native eBay labels through an API.",
            })
    return options


def _neutralise_legacy_ebay_handoff(html: str) -> str:
    marker = 'id="fbmShippingOrders"'
    if marker not in html:
        return html
    return html.replace(marker, 'id="fbmShippingOrders" data-ebay-shipping-handoff-installed="1"', 1)


def _positive_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _persisted_pack_mapping_parcel(row: MarketplaceOrder) -> dict:
    try:
        quantity = max(1, int(getattr(row, "quantity", 1) or 1))
    except (TypeError, ValueError):
        quantity = 1
    sku = str(getattr(row, "sku", None) or "").strip()
    if quantity != 1 or not sku:
        return {
            "weight_kg": None, "length_cm": None, "width_cm": None, "height_cm": None,
            "source": "selected_row_deferred_parcel", "complete": False,
        }

    mapping = (
        db.session.query(ProductPackMapping)
        .filter_by(single_sku=sku, is_active=True)
        .order_by(ProductPackMapping.updated_at.desc(), ProductPackMapping.id.desc())
        .first()
    )
    if mapping is None:
        return {
            "weight_kg": None, "length_cm": None, "width_cm": None, "height_cm": None,
            "source": "selected_row_deferred_parcel", "complete": False,
        }

    try:
        units = int(getattr(mapping, "units_per_carton", None) or 1)
    except (TypeError, ValueError):
        units = 1
    if units != 1:
        return {
            "weight_kg": None, "length_cm": None, "width_cm": None, "height_cm": None,
            "source": "selected_row_deferred_parcel", "complete": False,
        }

    parcel = {
        "weight_kg": _positive_float(getattr(mapping, "carton_weight_kg", None)),
        "length_cm": _positive_float(getattr(mapping, "carton_length_cm", None)),
        "width_cm": _positive_float(getattr(mapping, "carton_width_cm", None)),
        "height_cm": _positive_float(getattr(mapping, "carton_height_cm", None)),
        "source": "pack_mapping",
    }
    parcel["complete"] = all(parcel[name] is not None for name in ("weight_kg", "length_cm", "width_cm", "height_cm"))
    return parcel


def _selected_row_parcel(row: MarketplaceOrder) -> dict:
    saved = _persisted_pack_mapping_parcel(row)
    if any(saved.get(name) for name in ("weight_kg", "length_cm", "width_cm", "height_cm")):
        return saved

    try:
        quantity = max(1, int(getattr(row, "quantity", 1) or 1))
    except (TypeError, ValueError):
        quantity = 1
    warehouse = getattr(row, "warehouse_stock", None)
    try:
        unit_weight = float(getattr(warehouse, "product_weight_kg", 0) or 0) if warehouse is not None else 0.0
    except (TypeError, ValueError):
        unit_weight = 0.0
    weight = unit_weight * quantity if unit_weight > 0 else None
    return {
        "weight_kg": weight,
        "length_cm": None,
        "width_cm": None,
        "height_cm": None,
        "source": "selected_row_persisted_weight" if weight else "selected_row_missing_parcel",
        "complete": False,
    }


def _health_period() -> tuple[str, datetime, datetime, str]:
    """Return selected London-local reporting period as naive UTC DB bounds."""
    now_local = datetime.now(_FBM_HEALTH_TZ)
    mode = str(request.args.get("health_period") or "today").strip().lower()

    if mode == "date":
        raw = str(request.args.get("health_date") or "").strip()
        try:
            chosen = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            chosen = now_local.date()
            mode = "today"
        start_local = datetime(chosen.year, chosen.month, chosen.day, tzinfo=_FBM_HEALTH_TZ)
        end_local = start_local + timedelta(days=1)
        label = chosen.strftime("%d %b %Y")
    elif mode == "month":
        raw = str(request.args.get("health_month") or "").strip()
        try:
            chosen_month = datetime.strptime(raw, "%Y-%m")
            year, month = chosen_month.year, chosen_month.month
        except ValueError:
            year, month = now_local.year, now_local.month
        start_local = datetime(year, month, 1, tzinfo=_FBM_HEALTH_TZ)
        if month == 12:
            end_local = datetime(year + 1, 1, 1, tzinfo=_FBM_HEALTH_TZ)
        else:
            end_local = datetime(year, month + 1, 1, tzinfo=_FBM_HEALTH_TZ)
        label = start_local.strftime("%B %Y")
    else:
        mode = "today"
        start_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=_FBM_HEALTH_TZ)
        end_local = start_local + timedelta(days=1)
        label = f"Today · {start_local.strftime('%d %b %Y')}"

    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return mode, start_utc, end_utc, label


def _dedupe_latest(rows: list[MarketplaceOrder]) -> list[MarketplaceOrder]:
    result: list[MarketplaceOrder] = []
    seen: set[tuple[int, str]] = set()
    for row in sorted(rows, key=lambda item: item.id or 0, reverse=True):
        if row.store_id is None or not row.marketplace_order_id:
            continue
        key = (int(row.store_id), str(row.marketplace_order_id))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _health_summary() -> dict:
    """Aggregate period health from persisted DB facts only.

    Orders are counted by created_at in the selected period. Return/replacement/
    refund/case activity is counted by updated_at in that same period so an older
    order that develops an issue today is visible today. No marketplace/provider
    request is performed here.
    """
    mode, start_at, end_at, label = _health_period()
    eligible = (
        func.upper(func.coalesce(MarketplaceOrder.fulfillment_type, "")).notin_(("FBA", "AFN", "MCF")),
        ~func.lower(func.coalesce(MarketplaceOrder.status, "")).like("mcf_%"),
    )
    rows = (
        db.session.query(MarketplaceOrder)
        .filter(
            *eligible,
            or_(
                (MarketplaceOrder.created_at >= start_at) & (MarketplaceOrder.created_at < end_at),
                (MarketplaceOrder.updated_at >= start_at) & (MarketplaceOrder.updated_at < end_at),
            ),
        )
        .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
        .order_by(MarketplaceOrder.id.desc())
        .limit(_FBM_HEALTH_MAX_ROWS)
        .all()
    )
    latest = _dedupe_latest(rows)
    profiles = _profile_map([row for row in latest if _platform(row).strip().lower() == "amazon"])

    order_rows: list[MarketplaceOrder] = []
    lifecycle_rows: list[MarketplaceOrder] = []
    for row in latest:
        key = (int(row.store_id), str(row.marketplace_order_id))
        profile = profiles.get(key) if _platform(row).strip().lower() == "amazon" else None
        if not _workspace_fbm_eligible(row, profile):
            continue
        created = getattr(row, "created_at", None)
        updated = getattr(row, "updated_at", None)
        if created is not None and start_at <= created < end_at:
            order_rows.append(row)
        if updated is not None and start_at <= updated < end_at:
            lifecycle_rows.append(row)

    shipments = _shipment_map(order_rows)
    ready = dispatched = awaiting = overdue = mapping_review = 0
    platform_counts: dict[str, int] = {}
    for row in order_rows:
        platform = _platform(row).strip() or "Other"
        platform_counts[platform] = platform_counts.get(platform, 0) + 1
        route_state = _route_state(row)
        if route_state == "Ready for FBM routing":
            ready += 1
        if route_state in {"Dispatched", "Tracking recorded"}:
            dispatched += 1
        shipment = shipments.get((int(row.store_id), str(row.marketplace_order_id)))
        if shipment:
            state = shipment_confirmation_state(shipment)
            if state == "awaiting_carrier_acceptance":
                awaiting += 1
            elif state == "acceptance_overdue":
                overdue += 1
            review = getattr(shipment, "mapping_review", None)
            if review is not None and getattr(review, "status", None) == "under_review":
                mapping_review += 1

    returns = replacements = refund_issues = 0
    for row in lifecycle_rows:
        status = str(getattr(row, "status", "") or "").strip().lower()
        if status in {"return_requested", "returned"}:
            returns += 1
        elif status in {"replacement_requested", "replacement"}:
            replacements += 1
        elif status in {"refund_requested", "refunded", "case_open", "dispute", "chargeback"}:
            refund_issues += 1

    total = len(order_rows)
    risk_actions = overdue + mapping_review + returns + replacements + refund_issues
    health_base = max(1, total + returns + replacements + refund_issues)
    health_score = max(0, min(100, round(100 * (health_base - risk_actions) / health_base)))
    shipping_actions = ready + overdue + mapping_review

    return {
        "period_mode": mode,
        "period_label": label,
        "period_start": start_at,
        "period_end": end_at,
        "total": total,
        "ready": ready,
        "dispatched": dispatched,
        "awaiting_acceptance": awaiting,
        "overdue": overdue,
        "mapping_review": mapping_review,
        "returns": returns,
        "replacements": replacements,
        "refund_issues": refund_issues,
        "platform_counts": dict(sorted(platform_counts.items(), key=lambda item: (-item[1], item[0].lower()))),
        "health_score": health_score,
        "risk_actions": risk_actions,
        "shipping_actions": shipping_actions,
        "truncated": len(rows) >= _FBM_HEALTH_MAX_ROWS,
    }


def _metric_card(label: str, value: int, lines: list[str], extra_class: str = "") -> str:
    tip = "".join(f"<div>{escape(line)}</div>" for line in lines if line)
    return (
        f'<div class="fbm-period-card {extra_class}" tabindex="0">'
        f'<div class="fbm-period-label">{escape(label)}</div>'
        f'<div class="fbm-period-value">{int(value)}</div>'
        f'<div class="fbm-period-tip" role="tooltip">{tip or "<div>No items in this period</div>"}</div>'
        '</div>'
    )


def _period_controls(health: dict) -> str:
    preserved = {}
    for name in ("platform", "status", "limit"):
        value = str(request.args.get(name) or "").strip()
        if value:
            preserved[name] = value
    hidden = "".join(
        f'<input type="hidden" name="{escape(key)}" value="{escape(value)}">'
        for key, value in preserved.items()
    )
    today_url = f"{request.path}?{urlencode({**preserved, 'health_period': 'today'})}"
    selected_date = str(request.args.get("health_date") or "")
    selected_month = str(request.args.get("health_month") or "")
    return (
        '<div class="fbm-period-controls" aria-label="FBM health period">'
        f'<a class="btn btn-sm {"btn-primary" if health["period_mode"] == "today" else "btn-outline-secondary"}" href="{escape(today_url)}">Today</a>'
        f'<form method="get" action="{escape(request.path)}">{hidden}<input type="hidden" name="health_period" value="date">'
        f'<input class="form-control form-control-sm" aria-label="Custom FBM date" type="date" name="health_date" value="{escape(selected_date)}" onchange="this.form.submit()"></form>'
        f'<form method="get" action="{escape(request.path)}">{hidden}<input type="hidden" name="health_period" value="month">'
        f'<input class="form-control form-control-sm" aria-label="Custom FBM month" type="month" name="health_month" value="{escape(selected_month)}" onchange="this.form.submit()"></form>'
        '</div>'
    )


def _guide_html(health: dict) -> str:
    actions = int(health["shipping_actions"])
    if actions == 0:
        image = "bt38-guide-complete.svg"
        title = "All shipping caught up"
        copy = "Everything that needs a shipping action is clear for this period."
        small = "Nice work — BT38 will keep the queue visible."
    elif actions == 1:
        image = "bt38-guide-nearly-done.svg"
        title = "Just 1 shipping action left"
        copy = "You’re nearly clear. Finish the last shipping action."
        small = "One more step and the shipping queue is clear."
    elif actions <= 3:
        image = "bt38-guide-progress.svg"
        title = f"Only {actions} shipping actions left"
        copy = "You’re making strong progress through the FBM queue."
        small = "Keep going — the remaining actions are shown below."
    else:
        image = "bt38-guide-active.svg"
        title = f"You have {actions} shipping actions"
        copy = "Work through the important shipping actions first."
        small = "Ready-to-ship, overdue carrier and mapping actions drive this number."
    return (
        '<section class="fbm-guide">'
        f'<div class="fbm-guide-art"><img src="/static/img/{image}" alt="BT38 shipping guide"></div>'
        f'<div class="fbm-guide-copy"><h3>{escape(title)}</h3><p>{escape(copy)}</p><small>{escape(small)}</small></div>'
        f'<div class="fbm-guide-period"><strong>{escape(health["period_label"])}</strong><span>{health["total"]} FBM orders</span></div>'
        '</section>'
    )


def _health_html(health: dict) -> str:
    platform_lines = [f"{name}: {count}" for name, count in health["platform_counts"].items()]
    cards = [
        _metric_card("Orders", health["total"], platform_lines, "fbm-period-orders"),
        _metric_card("Ready to ship", health["ready"], [f"{health['ready']} orders still need a shipping action"]),
        _metric_card("Dispatched", health["dispatched"], [f"{health['dispatched']} orders have dispatch/tracking recorded"]),
        _metric_card("Awaiting carrier", health["awaiting_acceptance"], [f"{health['awaiting_acceptance']} labels are waiting for carrier acceptance"]),
        _metric_card("Carrier overdue", health["overdue"], [f"{health['overdue']} shipments are overdue for carrier acceptance"]),
        _metric_card("Returns", health["returns"], [f"{health['returns']} return events recorded in this period"]),
        _metric_card("Replacements", health["replacements"], [f"{health['replacements']} replacement events recorded in this period"]),
        _metric_card("Refunds / issues", health["refund_issues"], [f"{health['refund_issues']} refund, case, dispute or chargeback events"]),
        _metric_card("Mapping review", health["mapping_review"], [f"{health['mapping_review']} carrier mappings need review"]),
    ]
    warning = '<div class="small text-warning mt-1">Reporting window reached the 5,000-row safety cap.</div>' if health["truncated"] else ""
    return (
        '<section class="fbm-period-health">'
        '<div class="fbm-period-head"><div><strong>FBM Health</strong>'
        f'<div class="small text-muted">{escape(health["period_label"])} · DB-backed shipping and lifecycle facts</div>{warning}</div>'
        f'{_period_controls(health)}</div>'
        '<div class="fbm-period-grid">'
        '<div class="fbm-score-card">'
        f'<div class="fbm-score-ring" style="--fbm-score:{health["health_score"]}%"><div><strong>{health["health_score"]}%</strong><span>health</span></div></div>'
        f'<div><strong>Shipping health</strong><div class="small text-muted">{health["risk_actions"]} risk/issue actions in this period</div></div>'
        '</div>'
        + "".join(cards) +
        '</div></section>'
    )


def _setup_html() -> str:
    return (
        '<details class="fbm-shipping-setup mt-3">'
        '<summary><strong>Shipping setup</strong><span>Packlink PRO · Label printer</span></summary>'
        '<div class="fbm-shipping-setup-grid">'
        '<div class="border rounded p-2"><div class="d-flex justify-content-between align-items-center gap-2"><div><strong>Packlink PRO</strong><div class="small text-muted">Provider connection and paid-label handoff.</div></div><button id="packlinkConnectionTest" class="btn btn-sm btn-outline-primary" type="button">Test connection</button></div><div id="packlinkConnectionStatus" class="small text-muted mt-2">Connection status appears here.</div></div>'
        '<div class="border rounded p-2"><strong>Label printer · QZ Tray</strong><div class="small text-muted">Purchased labels are saved before printing.</div><div class="d-flex flex-wrap gap-2 align-items-center mt-2"><button id="qzConnect" class="btn btn-sm btn-outline-primary" type="button">Connect QZ</button><select id="qzPrinter" class="form-select form-select-sm" style="max-width:220px"><option value="">Saved/default printer</option></select><button id="qzSavePrinter" class="btn btn-sm btn-outline-secondary" type="button">Save printer</button><div class="form-check"><input id="qzAutoPrint" class="form-check-input" type="checkbox" checked><label class="form-check-label small" for="qzAutoPrint">Auto-print after purchase</label></div></div><div id="qzStatus" class="small text-muted mt-2">QZ not connected.</div></div>'
        '</div></details>'
    )


def _align_fbm_header(html: str, health: dict) -> str:
    """Replace the old setup/loaded-row health header without touching order actions."""
    top_start = html.find('<div class="fbm-top-grid">')
    overview_start = html.find('<div class="fbm-overview-grid">', top_start if top_start >= 0 else 0)
    orders_marker = '<div class="card">\n    <div class="card-header d-flex justify-content-between align-items-center flex-wrap gap-2"><div><span class="fw-semibold">FBM Orders</span>'

    if top_start >= 0 and overview_start > top_start:
        html = html[:top_start] + _guide_html(health) + "\n" + html[overview_start:]
        overview_start = html.find('<div class="fbm-overview-grid">')

    orders_start = html.find(orders_marker, overview_start if overview_start >= 0 else 0)
    if overview_start >= 0 and orders_start > overview_start:
        html = html[:overview_start] + _health_html(health) + "\n" + html[orders_start:]

    shipping_orders_marker = '<div id="fbmShippingOrders"></div>'
    shipping_orders_aligned_marker = '<div id="fbmShippingOrders" data-ebay-shipping-handoff-installed="1"></div>'
    if shipping_orders_aligned_marker in html:
        html = html.replace(shipping_orders_aligned_marker, shipping_orders_aligned_marker + _setup_html(), 1)
    elif shipping_orders_marker in html:
        html = html.replace(shipping_orders_marker, shipping_orders_marker + _setup_html(), 1)

    style = '''<style id="bt38FbmHealthAlignment">
.fbm-top-grid{display:none!important}.fbm-guide{min-height:118px;margin:0 0 .7rem;border:1px solid #efdca7;border-radius:14px;background:linear-gradient(100deg,#fff1ce 0%,#fff9e9 62%,#fffdf8 100%);display:grid;grid-template-columns:140px minmax(0,1fr) 190px;align-items:center;gap:16px;padding:8px 20px;overflow:hidden}.fbm-guide-art{height:102px;display:flex;align-items:flex-end;justify-content:center}.fbm-guide-art img{max-width:125px;max-height:102px;object-fit:contain}.fbm-guide-copy h3{margin:0 0 5px;font-size:21px;font-weight:800}.fbm-guide-copy p{margin:0 0 3px;font-size:13px}.fbm-guide-copy small{font-size:11px;color:#667085}.fbm-guide-period{background:#fff;border:1px solid #eadfca;border-radius:10px;padding:10px 12px}.fbm-guide-period strong,.fbm-guide-period span{display:block}.fbm-guide-period strong{font-size:12px}.fbm-guide-period span{font-size:11px;color:#667085;margin-top:3px}.fbm-period-health{margin-bottom:.8rem}.fbm-period-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:.45rem}.fbm-period-controls{display:flex;align-items:center;gap:6px;flex-wrap:wrap}.fbm-period-controls form{margin:0}.fbm-period-controls input{width:145px}.fbm-period-grid{display:grid;grid-template-columns:minmax(210px,1.15fr) repeat(3,minmax(110px,1fr));gap:.5rem}.fbm-score-card,.fbm-period-card{background:#fff;border:1px solid #e1e5eb;border-radius:9px;min-height:62px}.fbm-score-card{grid-row:span 3;display:flex;align-items:center;gap:12px;padding:10px 12px}.fbm-score-ring{--fbm-score:100%;width:72px;height:72px;min-width:72px;border-radius:50%;background:conic-gradient(#198754 0 var(--fbm-score),#e9ecef var(--fbm-score) 100%);display:grid;place-items:center;position:relative}.fbm-score-ring:after{content:"";width:52px;height:52px;border-radius:50%;background:#fff;position:absolute}.fbm-score-ring div{position:relative;z-index:1;text-align:center;line-height:1}.fbm-score-ring strong,.fbm-score-ring span{display:block}.fbm-score-ring strong{font-size:14px}.fbm-score-ring span{font-size:9px;color:#6c757d;margin-top:2px}.fbm-period-card{padding:8px 10px;position:relative;cursor:default;outline:none}.fbm-period-card:focus{box-shadow:0 0 0 2px rgba(13,110,253,.16)}.fbm-period-label{font-size:10px;color:#6b7280}.fbm-period-value{font-size:20px;font-weight:750;line-height:1.15;margin-top:2px}.fbm-period-tip{display:none;position:absolute;z-index:1080;left:8px;top:calc(100% + 5px);min-width:180px;max-width:260px;padding:8px 10px;border-radius:8px;background:#111827;color:#fff;font-size:11px;line-height:1.45;box-shadow:0 8px 24px rgba(15,23,42,.22);pointer-events:none}.fbm-period-card:hover .fbm-period-tip,.fbm-period-card:focus .fbm-period-tip{display:block}.fbm-shipping-setup{border:1px solid #e3e7ed;border-radius:9px;padding:8px 10px;background:#f8fafc}.fbm-shipping-setup summary{cursor:pointer;display:flex;justify-content:space-between;gap:10px;list-style:none}.fbm-shipping-setup summary::-webkit-details-marker{display:none}.fbm-shipping-setup summary span{font-size:11px;color:#6b7280}.fbm-shipping-setup-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px;background:#fff;padding:8px;border-radius:8px}
@media(max-width:900px){.fbm-guide{grid-template-columns:90px minmax(0,1fr);padding:8px 12px}.fbm-guide-period{grid-column:1/-1}.fbm-period-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.fbm-score-card{grid-column:1/-1;grid-row:auto}.fbm-shipping-setup-grid{grid-template-columns:1fr}}
@media(max-width:520px){.fbm-guide-art{height:80px}.fbm-guide-art img{max-width:88px;max-height:80px}.fbm-guide-copy h3{font-size:17px}.fbm-period-head{align-items:flex-start;flex-direction:column}.fbm-period-controls{width:100%}.fbm-period-controls form{flex:1}.fbm-period-controls input{width:100%}.fbm-period-grid{grid-template-columns:1fr 1fr}.fbm-score-card{grid-column:1/-1}}
</style>'''
    if "</head>" in html:
        html = html.replace("</head>", style + "</head>", 1)
    else:
        html = style + html
    return html


def _expand_control(html: str, *, visible_limit: int, has_more: bool) -> str:
    if not html:
        return html

    params = {}
    for name in ("platform", "status", "health_period", "health_date", "health_month"):
        value = str(request.args.get(name) or "").strip()
        if value:
            params[name] = value

    actions = []
    if visible_limit > _FBM_PAGE_SIZE:
        collapse_params = dict(params)
        collapse_params["limit"] = _FBM_PAGE_SIZE
        actions.append(f'<a class="btn btn-sm btn-outline-secondary" href="{request.path}?{urlencode(collapse_params)}">Show latest 15</a>')
    if has_more and visible_limit < _FBM_MAX_EXPANDED:
        expand_params = dict(params)
        expand_params["limit"] = min(_FBM_MAX_EXPANDED, visible_limit + _FBM_PAGE_SIZE)
        actions.append(f'<a id="fbmExpandOrders" class="btn btn-sm btn-outline-primary" href="{request.path}?{urlencode(expand_params)}">Show 15 more</a>')

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
    """Bound the existing FBM page and Shipping-options read endpoints only."""
    if getattr(app, "_bt38_fbm_page_alignment_installed", False):
        return

    page_endpoint = "governed_fbm.fbm_page"
    shipping_options_endpoint = "governed_fbm.fbm_shipping_options"
    if page_endpoint not in app.view_functions:
        raise RuntimeError("governed FBM page endpoint is not registered")
    if shipping_options_endpoint not in app.view_functions:
        raise RuntimeError("governed FBM shipping-options endpoint is not registered")

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
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key)
            if not _workspace_fbm_eligible(row, profile):
                continue
            platform = _platform(row)
            route_state = _route_state(row)
            shipment = shipments.get(key)
            shipment_state = shipment_confirmation_state(shipment) if shipment else "not_started"
            case = provider_case_eligibility(shipment) if shipment else {"eligible": False, "reason": "shipment_not_started", "case_type": None}
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
            "mapping_review": sum(1 for item in orders if item["mapping_review"] and item["mapping_review"].status == "under_review"),
        }

        health = _health_summary()
        html = render_template(
            "fbm.html",
            orders=orders,
            counts=counts,
            platform_filter=platform_filter,
            status_filter=status_filter,
        )
        html = _neutralise_legacy_ebay_handoff(html)
        html = _align_fbm_header(html, health)
        return _expand_control(html, visible_limit=visible_limit, has_more=has_more)

    @login_required
    def bounded_shipping_options():
        order_ids = _selected_order_ids()
        if not order_ids:
            return jsonify({"success": False, "message": "Select at least one FBM order."}), 400

        rows = (
            db.session.query(MarketplaceOrder)
            .filter(MarketplaceOrder.id.in_(order_ids))
            .options(joinedload(MarketplaceOrder.store))
            .all()
        )
        amazon_rows = [row for row in rows if _platform(row).strip().lower() == "amazon"]
        profiles = _profile_map(amazon_rows)
        by_id = {}
        for row in rows:
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key) if _platform(row).strip().lower() == "amazon" else None
            if _workspace_fbm_eligible(row, profile):
                by_id[row.id] = row
        result = []

        for order_id in order_ids:
            row = by_id.get(order_id)
            if row is None:
                continue
            platform = _platform(row).strip().lower()
            key = (int(row.store_id), str(row.marketplace_order_id))
            profile = profiles.get(key) if platform == "amazon" else None
            try:
                quantity = max(1, int(getattr(row, "quantity", 1) or 1))
            except (TypeError, ValueError):
                quantity = 1
            result.append({
                "id": row.id,
                "marketplace_order_id": row.marketplace_order_id,
                "platform": _platform(row),
                "store_name": _store_name(row),
                "sku": getattr(row, "sku", None),
                "quantity": quantity,
                "postcode": getattr(row, "ship_to_postcode", None),
                "route_state": _route_state(row),
                "is_prime": profile.is_prime if profile else None,
                "prime_profile_error": None,
                "parcel": _selected_row_parcel(row),
                "providers": _workspace_provider_options(row, profile),
            })

        if not result:
            return jsonify({"success": False, "message": "No selected orders are eligible for FBM shipping."}), 404

        return jsonify({
            "success": True,
            "orders": result,
            "selected_count": len(result),
            "printing": {
                "mode": "qz_tray",
                "auto_print_after_purchase": True,
                "printer_preference_required": True,
                "fallback": "download_label",
            },
            "message": "Shipping routes and selected-row persisted defaults prepared. Complete provider reads remain deferred until an explicit shipping action.",
        })

    app.view_functions[page_endpoint] = bounded_fbm_page
    app.view_functions[shipping_options_endpoint] = bounded_shipping_options
    app._bt38_fbm_page_alignment_installed = True
    app.logger.info(
        "BT38 FBM alignment installed: bounded order discovery, period health from DB, selected-row Shipping options, and shipping setup beside the modal"
    )
