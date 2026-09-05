"""Hydrate missing Amazon FBM shipping facts only for the current ready desk.

This is not recovery, not startup backfill, and not a bell authority. When /fbm
is opened, only current merchant-fulfilled Amazon orders that are still ready to
dispatch and have no FBMOrderProfile are handed to the existing exact Amazon
profile reader. That reader persists Prime, service level, ship-by and delivery
promise onto the existing FBM profile/operational state, then the normal FBM
page renders DB truth.
"""
from __future__ import annotations

from flask import request


def _hydrate_current_missing_profiles(limit: int = 20) -> None:
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
        .limit(max(1, min(int(limit), 20)))
        .all()
    )

    seen: set[tuple[int, str]] = set()
    for row in rows:
        key = (int(row.store_id), str(row.marketplace_order_id))
        if key in seen:
            continue
        seen.add(key)
        try:
            get_or_refresh_amazon_profile(row, force=True)
        except AmazonOrderProfileError:
            db.session.rollback()
        except Exception:
            db.session.rollback()


def install_governed_fbm_current_amazon_profile_alignment(app) -> None:
    """Hydrate current missing Ready-to-dispatch Amazon facts before /fbm renders."""
    if getattr(app, "_bt38_fbm_current_amazon_profile_alignment", False):
        return

    @app.before_request
    def _hydrate_current_ready_amazon_fbm():
        if request.method != "GET" or (request.path.rstrip("/") or "/") != "/fbm":
            return None
        _hydrate_current_missing_profiles()
        return None

    app._bt38_fbm_current_amazon_profile_alignment = True
    app.logger.info(
        "BT38 current FBM Amazon profile alignment installed: missing ready rows only; no startup recovery, no bell publish"
    )
