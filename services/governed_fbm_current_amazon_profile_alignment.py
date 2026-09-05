"""Temporarily align one missing Amazon FBM profile when the Ready desk opens.

This compatibility hook exists only while older/misaligned FBM records are being
repaired. It is deliberately bounded to one exact merchant-fulfilled Amazon
order per /fbm request. It must never fan out across the Ready desk, force fresh
reads for already-cached truth, or compete with the visible-row profile wrapper.

Steady state remains event-driven: marketplace events persist exact affected
records and the FBM page reads DB truth only. This hook can be removed once the
historical/current alignment is complete.
"""
from __future__ import annotations

from flask import g, request


def _hydrate_current_missing_profiles(limit: int = 1) -> None:
    from extensions import db
    from fbm_models import FBMOrderProfile
    from models import MarketplaceOrder, Store
    from services.fbm_amazon_order_profile import (
        AmazonOrderProfileError,
        get_or_refresh_amazon_profile,
    )

    rows = (
        db.session.query(MarketplaceOrder)
        .join(Store, Store.id == MarketplaceOrder.store_id)
        .outerjoin(
            FBMOrderProfile,
            (FBMOrderProfile.store_id == MarketplaceOrder.store_id)
            & (
                FBMOrderProfile.marketplace_order_id
                == MarketplaceOrder.marketplace_order_id
            ),
        )
        .filter(Store.platform.ilike("%amazon%"))
        .filter(FBMOrderProfile.id.is_(None))
        .filter(MarketplaceOrder.shipped_at.is_(None))
        .filter(
            (MarketplaceOrder.tracking_number.is_(None))
            | (MarketplaceOrder.tracking_number == "")
        )
        .filter(
            ~MarketplaceOrder.fulfillment_type.in_(
                ("FBA", "AFN", "MCF", "AMAZON")
            )
        )
        .order_by(
            MarketplaceOrder.created_at.desc(),
            MarketplaceOrder.id.desc(),
        )
        .limit(1)
        .all()
    )

    for row in rows:
        try:
            # Do not force through the profile cache. This temporary page hook
            # may fill one genuinely missing profile; exact shipping actions and
            # marketplace events own any later refresh.
            get_or_refresh_amazon_profile(row, force=False)
        except AmazonOrderProfileError:
            db.session.rollback()
        except Exception:
            # Quota/rate-limit/readback failures are best-effort here. One bad
            # marketplace read must never poison the request transaction or fan
            # out into more Amazon calls during this page load.
            db.session.rollback()
        finally:
            # The visible-row wrapper honours this request-local flag. Mark the
            # page hydration attempt complete even on failure so /fbm cannot
            # immediately make a second Amazon profile read in the same request.
            g._bt38_fbm_amazon_profile_hydration_checked = True


def install_governed_fbm_current_amazon_profile_alignment(app) -> None:
    """Install the temporary one-order Ready-desk compatibility hydration."""
    if getattr(app, "_bt38_fbm_current_amazon_profile_alignment", False):
        return

    @app.before_request
    def _hydrate_current_ready_amazon_fbm():
        if request.method != "GET" or (request.path.rstrip("/") or "/") != "/fbm":
            return None
        _hydrate_current_missing_profiles(limit=1)
        return None

    app._bt38_fbm_current_amazon_profile_alignment = True
    app.logger.info(
        "BT38 temporary FBM Amazon alignment installed: at most one missing ready profile per /fbm request; no force, no fan-out"
    )
