"""Presentation-only alignment for governed FBM UI.

The marketplace/provider handoff owns collection and persistence. Page GETs must
not recover, reconcile, hydrate, or re-query historical order/shipment facts.
This module keeps presentation cleanup separate while installing the existing
DB-first marketplace lifecycle alignment before the FBM page wrapper is bound.
"""
from __future__ import annotations

import re

from flask import request


_JOURNEY_LABEL_REPLACEMENTS = (
    ("1 · Picked up", "Picked up"),
    ("2 · In transit", "In transit"),
    ("3 · Delivered", "Delivered"),
)
_TRACKING_LINK_STYLE = (
    '<style id="bt38FbmTrackingLinkAlignment">'
    '.fbm-orders-table td a:has(code){text-decoration:none!important}'
    '.fbm-orders-table td a:has(code) code{text-decoration:none!important}'
    '</style>'
)
_MARKETPLACE_BADGE_STYLE = (
    '<style id="bt38FbmMarketplaceBadgeAlignment">'
    '.fbm-marketplace-cell{min-width:110px!important}'
    '.fbm-marketplace-logo{display:block!important;max-width:82px!important;max-height:36px!important;'
    'width:auto!important;height:auto!important;object-fit:contain!important;object-position:left center!important;image-rendering:auto}'
    '</style>'
)


def _clean_fbm_journey_html(html: str) -> str:
    """Remove presentation-only numbering without changing persisted state."""
    value = str(html or "")
    for old, new in _JOURNEY_LABEL_REPLACEMENTS:
        value = value.replace(old, new)
    return value


def _align_fbm_tracking_link_html(html: str) -> str:
    """Keep marketplace tracking clickable without an underline below the ID."""
    value = str(html or "")
    if 'id="bt38FbmTrackingLinkAlignment"' in value:
        return value
    if "</head>" in value:
        return value.replace("</head>", _TRACKING_LINK_STYLE + "</head>", 1)
    return _TRACKING_LINK_STYLE + value


def _align_fbm_marketplace_badge_html(html: str) -> str:
    """Enlarge the existing marketplace assets without replacing or redrawing them."""
    value = str(html or "")
    if 'id="bt38FbmMarketplaceBadgeAlignment"' in value:
        return value
    if "</head>" in value:
        return value.replace("</head>", _MARKETPLACE_BADGE_STYLE + "</head>", 1)
    return _MARKETPLACE_BADGE_STYLE + value


def _align_fbm_buyer_messages_card(html: str) -> str:
    """Use the spare health-card slot for buyer messages instead of mapping review.

    Buyer-message ingestion is not yet wired into BT38, so the card deliberately
    shows zero rather than reusing the unrelated carrier-mapping count.
    """
    value = str(html or "")
    pattern = re.compile(
        r'<div class="fbm-period-card(?P<class_suffix>[^"]*)" tabindex="0">'
        r'<div class="fbm-period-label">Mapping review</div>'
        r'<div class="fbm-period-value">[^<]*</div>'
        r'<div class="fbm-period-tip" role="tooltip">.*?</div>'
        r'</div>',
        re.DOTALL,
    )
    replacement = (
        '<div class="fbm-period-card\\g<class_suffix>" tabindex="0">'
        '<div class="fbm-period-label">Buyer messages</div>'
        '<div class="fbm-period-value">0</div>'
        '<div class="fbm-period-tip" role="tooltip">'
        '<div>No buyer messages are currently ingested into BT38.</div>'
        '</div>'
        '</div>'
    )
    return pattern.sub(replacement, value, count=1)


def install_governed_order_clarity_alignment(app) -> None:
    if getattr(app, "_bt38_order_clarity_alignment_installed", False):
        return

    # Install before the bounded FBM page wrapper binds its view functions.
    # The lifecycle module patches only the existing governed authorities; this
    # clarity module itself stays presentation-only and performs no data reads.
    from services.governed_fbm_lifecycle_alignment import (
        install_governed_fbm_lifecycle_alignment,
    )
    from services.governed_fbm_fulfillment_guard import (
        install_governed_fbm_fulfillment_guard,
    )
    from services.fbm_db_delivery_promise_alignment import (
        install_fbm_db_delivery_promise_alignment,
    )
    from services.governed_fbm_global_search_alignment import (
        install_governed_fbm_global_search_alignment,
    )

    # Reuse the existing persisted operational-state promise reader. This does
    # not add a marketplace/API read: it restores the DB -> FBM handoff for the
    # template's existing delivery_promise field.
    install_fbm_db_delivery_promise_alignment(app)
    # FBM search must query persisted order history before the page-size limit;
    # it never calls a marketplace/provider and does not create a second order path.
    install_governed_fbm_global_search_alignment(app)
    install_governed_fbm_lifecycle_alignment(app)
    install_governed_fbm_fulfillment_guard()
    app._bt38_order_clarity_alignment_installed = True

    @app.after_request
    def bt38_order_clarity_response(response):
        path = request.path.rstrip("/") or "/"

        # Render-only alignment. All tracking, carrier, delivery and fulfillment
        # facts must already have been persisted by the existing governed event /
        # provider handoff before this GET. Never query or reconcile from a page
        # read.
        if (
            path == "/fbm"
            and response.status_code == 200
            and response.content_type
            and "text/html" in response.content_type
        ):
            html = _clean_fbm_journey_html(response.get_data(as_text=True))
            html = _align_fbm_tracking_link_html(html)
            html = _align_fbm_marketplace_badge_html(html)
            html = _align_fbm_buyer_messages_card(html)
            response.set_data(html)

        return response

    app.logger.info(
        "BT38 order clarity alignment installed: persisted delivery promises + global persisted FBM search + buyer-messages health slot + clean tracking links + sharper existing marketplace badges + render-only FBM Journey labels; event-persisted state remains authoritative"
    )
