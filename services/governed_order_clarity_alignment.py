"""Presentation-only alignment for governed FBM UI.

The marketplace/provider handoff owns collection and persistence. Page GETs must
not recover, reconcile, hydrate, or re-query historical order/shipment facts.
This module keeps presentation cleanup separate while installing the existing
DB-first marketplace lifecycle alignment before the FBM page wrapper is bound.
"""
from __future__ import annotations

import re
from flask import request

_JOURNEY_LABEL_REPLACEMENTS = (("1 · Picked up", "Picked up"), ("2 · In transit", "In transit"), ("3 · Delivered", "Delivered"))
_TRACKING_LINK_STYLE = ('<style id="bt38FbmTrackingLinkAlignment">''.fbm-orders-table td a:has(code),.fbm-orders-table td a:has(code):hover,.fbm-orders-table td a:has(code):focus,.fbm-orders-table .fbm-tracking-journey,.fbm-orders-table .fbm-tracking-journey:hover,.fbm-orders-table .fbm-tracking-journey:focus{text-decoration:none!important;border-bottom:0!important;box-shadow:none!important}.fbm-orders-table td a:has(code) code,.fbm-orders-table .fbm-tracking-journey code{text-decoration:none!important;border-bottom:0!important;box-shadow:none!important}</style>')
_MARKETPLACE_BADGE_STYLE = ('<style id="bt38FbmMarketplaceBadgeAlignment">''.fbm-marketplace-cell{min-width:110px!important}.fbm-marketplace-logo{display:block!important;max-width:82px!important;max-height:36px!important;width:auto!important;height:auto!important;object-fit:contain!important;object-position:left center!important;image-rendering:auto}</style>')
_PROMISE_JOURNEY_SCRIPT = '<script id="bt38FbmPromiseJourneyAlignment" src="/static/js/fbm_delivery_promise_journey_alignment.js"></script>'
_EVENT_SESSION_REFRESH_SCRIPT = '<script id="bt38FbmEventSessionRefreshAlignment" src="/static/js/fbm_event_session_refresh_alignment.js"></script>'
_SCROLL_POSITION_SCRIPT = '<script id="bt38FbmScrollPositionAlignment" src="/static/js/fbm_scroll_position_alignment.js"></script>'


def _clean_fbm_journey_html(html: str) -> str:
    value = str(html or "")
    for old, new in _JOURNEY_LABEL_REPLACEMENTS:
        value = value.replace(old, new)
    return value


def _inject_once(html: str, marker: str, payload: str, closing: str) -> str:
    value = str(html or "")
    if marker in value:
        return value
    return value.replace(closing, payload + closing, 1) if closing in value else value + payload


def _align_fbm_tracking_link_html(html: str) -> str:
    return _inject_once(html, 'id="bt38FbmTrackingLinkAlignment"', _TRACKING_LINK_STYLE, '</head>')


def _align_fbm_marketplace_badge_html(html: str) -> str:
    return _inject_once(html, 'id="bt38FbmMarketplaceBadgeAlignment"', _MARKETPLACE_BADGE_STYLE, '</head>')


def _align_fbm_promise_journey_html(html: str) -> str:
    return _inject_once(html, 'id="bt38FbmPromiseJourneyAlignment"', _PROMISE_JOURNEY_SCRIPT, '</body>')


def _align_fbm_event_session_refresh_html(html: str) -> str:
    """Reuse the existing shared marketplace event to refresh this FBM session from DB."""
    return _inject_once(html, 'id="bt38FbmEventSessionRefreshAlignment"', _EVENT_SESSION_REFRESH_SCRIPT, '</body>')


def _align_fbm_scroll_position_html(html: str) -> str:
    """Prevent a stale pager anchor from forcing a browser reload to the page bottom."""
    return _inject_once(html, 'id="bt38FbmScrollPositionAlignment"', _SCROLL_POSITION_SCRIPT, '</body>')


def _align_fbm_buyer_messages_card(html: str) -> str:
    value = str(html or "")
    pattern = re.compile(r'<div class="fbm-period-card(?P<class_suffix>[^"]*)" tabindex="0"><div class="fbm-period-label">Mapping review</div><div class="fbm-period-value">[^<]*</div><div class="fbm-period-tip" role="tooltip">.*?</div></div>', re.DOTALL)
    replacement = ('<div class="fbm-period-card\\g<class_suffix>" tabindex="0"><div class="fbm-period-label">Buyer messages</div><div class="fbm-period-value">0</div><div class="fbm-period-tip" role="tooltip"><div>No buyer messages are currently ingested into BT38.</div></div></div>')
    return pattern.sub(replacement, value, count=1)


def install_governed_order_clarity_alignment(app) -> None:
    if getattr(app, "_bt38_order_clarity_alignment_installed", False):
        return
    from services.governed_fbm_lifecycle_alignment import install_governed_fbm_lifecycle_alignment
    from services.governed_fbm_marketplace_dispatch_authority_alignment import install_governed_fbm_marketplace_dispatch_authority_alignment
    from services.governed_fbm_fulfillment_guard import install_governed_fbm_fulfillment_guard
    from services.fbm_db_delivery_promise_alignment import install_fbm_db_delivery_promise_alignment
    from services.governed_fbm_global_search_alignment import install_governed_fbm_global_search_alignment
    from services.governed_fbm_all_orders_health_alignment import install_governed_fbm_all_orders_health_alignment
    from services.governed_fbm_overdue_alert_alignment import install_governed_fbm_overdue_alert_alignment

    install_fbm_db_delivery_promise_alignment(app)
    install_governed_fbm_global_search_alignment(app)
    install_governed_fbm_all_orders_health_alignment(app)
    install_governed_fbm_overdue_alert_alignment(app)
    install_governed_fbm_lifecycle_alignment(app)
    install_governed_fbm_marketplace_dispatch_authority_alignment()
    install_governed_fbm_fulfillment_guard()
    app._bt38_order_clarity_alignment_installed = True

    @app.after_request
    def bt38_order_clarity_response(response):
        path = request.path.rstrip("/") or "/"

        # Render-only boundary. Never query or reconcile from a page GET; the
        # marketplace/provider handoff must have persisted the event first.
        if path == "/fbm" and response.status_code == 200 and response.content_type and "text/html" in response.content_type:
            html = _clean_fbm_journey_html(response.get_data(as_text=True))
            html = _align_fbm_tracking_link_html(html)
            html = _align_fbm_marketplace_badge_html(html)
            html = _align_fbm_buyer_messages_card(html)
            html = _align_fbm_promise_journey_html(html)
            html = _align_fbm_event_session_refresh_html(html)
            html = _align_fbm_scroll_position_html(html)
            response.set_data(html)
        return response

    app.logger.info("BT38 order clarity alignment installed: persisted delivery promises + global persisted FBM search + all-orders persisted FBM health + low-pressure overdue alert/filter + buyer-messages health slot + clean tracking controls + sharper existing marketplace badges + DB-row-authoritative promise journey + marketplace-dispatch shipment authority + existing-event FBM session refresh + stable reload scroll position; event-persisted state remains authoritative")
