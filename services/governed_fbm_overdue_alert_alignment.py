"""Expose overdue FBM work without adding polling or repeated DB pressure.

The all-orders health summary remains DB authoritative, but repeated page refreshes
reuse a short in-process read-through cache. The red overdue alert is render-only;
its click performs one explicit persisted DB query for the latest overdue shipment
per marketplace order and returns only those FBM orders.

No marketplace/provider calls, no scheduler, no browser poller, no DB writes.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from time import monotonic

from flask import request
from sqlalchemy import func, tuple_
from sqlalchemy.orm import joinedload

from extensions import db
from fbm_models import FBMShipment
from models import MarketplaceOrder


_HEALTH_CACHE_TTL_SECONDS = 60.0
_health_cache: dict[str, object] = {"at": 0.0, "value": None}


def _cached_health_summary(original_health_summary) -> dict:
    """Run the all-orders health scan at most once per minute per app process."""
    now = monotonic()
    cached = _health_cache.get("value")
    cached_at = float(_health_cache.get("at") or 0.0)
    if isinstance(cached, dict) and now - cached_at < _HEALTH_CACHE_TTL_SECONDS:
        return dict(cached)

    health = dict(original_health_summary())
    _health_cache["value"] = dict(health)
    _health_cache["at"] = now
    return health


def _latest_overdue_rows(page_alignment) -> list[MarketplaceOrder]:
    """Read only latest persisted shipments whose carrier handover is overdue."""
    latest_shipment_ids = (
        db.session.query(func.max(FBMShipment.id).label("id"))
        .filter(FBMShipment.store_id.isnot(None), FBMShipment.marketplace_order_id.isnot(None))
        .group_by(FBMShipment.store_id, FBMShipment.marketplace_order_id)
        .subquery()
    )
    overdue_shipments = (
        db.session.query(FBMShipment.store_id, FBMShipment.marketplace_order_id)
        .join(latest_shipment_ids, FBMShipment.id == latest_shipment_ids.c.id)
        .filter(
            FBMShipment.handover_due_at.isnot(None),
            FBMShipment.handover_due_at < datetime.utcnow(),
            FBMShipment.carrier_accepted_at.is_(None),
            FBMShipment.delivered_at.is_(None),
        )
        .all()
    )
    identities = sorted({
        (int(store_id), str(order_id))
        for store_id, order_id in overdue_shipments
        if store_id is not None and order_id
    })
    if not identities:
        return []

    latest_order_ids = (
        db.session.query(func.max(MarketplaceOrder.id).label("id"))
        .filter(tuple_(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id).in_(identities))
        .group_by(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id)
        .subquery()
    )
    rows = (
        db.session.query(MarketplaceOrder)
        .join(latest_order_ids, MarketplaceOrder.id == latest_order_ids.c.id)
        .options(joinedload(MarketplaceOrder.store), joinedload(MarketplaceOrder.warehouse_stock))
        .order_by(MarketplaceOrder.id.desc())
        .all()
    )

    profiles = page_alignment._profile_map([
        row for row in rows
        if page_alignment._platform(row).strip().lower() == "amazon"
    ])
    result: list[MarketplaceOrder] = []
    for row in rows:
        key = (int(row.store_id), str(row.marketplace_order_id))
        profile = profiles.get(key) if page_alignment._platform(row).strip().lower() == "amazon" else None
        if page_alignment._workspace_fbm_eligible(row, profile):
            result.append(row)
    return result


def _overdue_alert_html(health: dict) -> str:
    overdue = int(health.get("overdue") or 0)
    filtering = str(request.args.get("health_filter") or "").strip().lower() == "overdue"
    if overdue <= 0 and not filtering:
        return ""

    if filtering:
        text = f"Showing {overdue} overdue FBM order{'s' if overdue != 1 else ''} only"
        action = '<span class="fbm-overdue-clear">Show all orders</span>'
        href = "/fbm"
    else:
        text = f"{overdue} overdue FBM order{'s' if overdue != 1 else ''} need attention"
        action = '<span class="fbm-overdue-action">Show overdue orders</span>'
        href = "/fbm?health_filter=overdue"

    return (
        f'<a id="bt38FbmOverdueAlert" class="fbm-overdue-alert" href="{escape(href)}">'
        f'<strong>{escape(text)}</strong>{action}</a>'
    )


def _inject_overdue_alert(html: str, health: dict) -> str:
    alert = _overdue_alert_html(health)
    if not alert or 'id="bt38FbmOverdueAlert"' in html:
        return html
    marker = '<section class="fbm-period-health">'
    if marker not in html:
        return html
    return html.replace(marker, marker + alert, 1)


def _inject_overdue_style(html: str) -> str:
    if 'id="bt38FbmOverdueAlertStyle"' in html:
        return html
    style = '''<style id="bt38FbmOverdueAlertStyle">
.fbm-overdue-alert{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 .55rem;padding:9px 12px;border:1px solid #dc3545;border-radius:9px;background:#dc3545;color:#fff!important;text-decoration:none!important;box-shadow:0 0 0 0 rgba(220,53,69,.48);animation:bt38FbmOverduePulse 1.25s ease-in-out infinite}.fbm-overdue-alert:hover{color:#fff!important;background:#bb2d3b}.fbm-overdue-action,.fbm-overdue-clear{font-size:11px;font-weight:700;border:1px solid rgba(255,255,255,.75);border-radius:999px;padding:4px 8px;white-space:nowrap}@keyframes bt38FbmOverduePulse{0%,100%{box-shadow:0 0 0 0 rgba(220,53,69,.46)}50%{box-shadow:0 0 0 7px rgba(220,53,69,0)}}@media(prefers-reduced-motion:reduce){.fbm-overdue-alert{animation:none}}
</style>'''
    if "</head>" in html:
        return html.replace("</head>", style + "</head>", 1)
    return style + html


def install_governed_fbm_overdue_alert_alignment(app) -> None:
    if getattr(app, "_bt38_fbm_overdue_alert_alignment_installed", False):
        return

    from services import governed_fbm_page_alignment as page_alignment

    original_health_summary = page_alignment._health_summary
    original_health_html = page_alignment._health_html
    original_rows = page_alignment._latest_distinct_fbm_rows

    def low_pressure_health_summary() -> dict:
        return _cached_health_summary(original_health_summary)

    def overdue_aware_rows(limit: int):
        if str(request.args.get("health_filter") or "").strip().lower() == "overdue":
            return _latest_overdue_rows(page_alignment), False
        return original_rows(limit)

    def overdue_aware_health_html(health: dict) -> str:
        html = original_health_html(health)
        html = _inject_overdue_alert(html, health)
        return _inject_overdue_style(html)

    page_alignment._health_summary = low_pressure_health_summary
    page_alignment._latest_distinct_fbm_rows = overdue_aware_rows
    page_alignment._health_html = overdue_aware_health_html
    app._bt38_fbm_overdue_alert_alignment_installed = True
    app.logger.info(
        "BT38 FBM overdue alert aligned: red pulse is render-only; overdue list is explicit-click DB read; all-orders health scan cached 60s; no poller/scheduler/provider reads"
    )
