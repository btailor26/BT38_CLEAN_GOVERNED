"""Presentation-only alignment for governed FBM UI.

The marketplace/provider handoff owns collection and persistence. Page GETs must
not recover, reconcile, hydrate, or re-query historical order/shipment facts.
This module keeps presentation cleanup separate while installing the existing
DB-first marketplace lifecycle alignment before the FBM page wrapper is bound.
"""
from __future__ import annotations

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

    # Reuse the existing persisted operational-state promise reader. This does
    # not add a marketplace/API read: it restores the DB -> FBM handoff for the
    # template's existing delivery_promise field.
    install_fbm_db_delivery_promise_alignment(app)
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
            response.set_data(html)

        return response

    app.logger.info(
        "BT38 order clarity alignment installed: persisted delivery promises + clean tracking links + render-only FBM Journey labels; event-persisted state remains authoritative"
    )
