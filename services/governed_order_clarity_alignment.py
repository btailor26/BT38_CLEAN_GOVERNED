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


def _clean_fbm_journey_html(html: str) -> str:
    """Remove presentation-only numbering without changing persisted state."""
    value = str(html or "")
    for old, new in _JOURNEY_LABEL_REPLACEMENTS:
        value = value.replace(old, new)
    return value


def install_governed_order_clarity_alignment(app) -> None:
    if getattr(app, "_bt38_order_clarity_alignment_installed", False):
        return

    # Install before the bounded FBM page wrapper binds its view functions.
    # The lifecycle module patches only the existing governed authorities; this
    # clarity module itself stays presentation-only and performs no data reads.
    from services.governed_fbm_lifecycle_alignment import (
        install_governed_fbm_lifecycle_alignment,
    )

    install_governed_fbm_lifecycle_alignment(app)
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
            response.set_data(_clean_fbm_journey_html(response.get_data(as_text=True)))

        return response

    app.logger.info(
        "BT38 order clarity alignment installed: render-only FBM Journey labels; event-persisted state remains authoritative"
    )
