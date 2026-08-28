"""Bounded DB-only performance alignment for the canonical /fbm page.

The live shipping desk must render from persisted Neon facts quickly.  It must
not call Amazon, eBay or Packlink while the page itself is loading.  Explicit
Shipping Options/provider actions own live marketplace refreshes.
"""
from __future__ import annotations

from app import app
from extensions import db
from models import MarketplaceOrder


def install_fbm_page_batch_alignment() -> None:
    import governed_fbm_routes as routes
    from services import fbm_operational_state as ops

    if getattr(routes._profile_for, "_bt38_page_batch_aligned", False):
        return

    original_profile_for = routes._profile_for

    def batched_profile_for(order):
        maps = ops._request_page_maps()
        if isinstance(maps, dict):
            key = (
                getattr(order, "store_id", None),
                str(getattr(order, "marketplace_order_id", "") or "").strip(),
            )
            return maps.get("profile", {}).get(key)
        return original_profile_for(order)

    batched_profile_for._bt38_page_batch_aligned = True
    routes._profile_for = batched_profile_for

    if getattr(app, "_bt38_fbm_page_preload_installed", False):
        return

    @app.before_request
    def bt38_preload_fbm_page_relationships():
        from flask import request
        if request.method != "GET" or request.path.rstrip("/") != "/fbm":
            return None

        # Preload only the bounded set the existing /fbm route can render.
        # This fills SQLAlchemy's identity map and prevents template-level N+1
        # Store/Warehouse lazy loads without altering order coverage.
        try:
            from sqlalchemy.orm import selectinload
            query = MarketplaceOrder.query.options(
                selectinload(MarketplaceOrder.store),
                selectinload(MarketplaceOrder.warehouse_stock),
            )
            query.order_by(
                MarketplaceOrder.created_at.desc(),
                MarketplaceOrder.id.desc(),
            ).limit(300).all()
            ops._request_page_maps()
        except Exception:
            db.session.rollback()
            app.logger.exception("FBM DB-only page preload failed; canonical route will continue")
        return None

    app._bt38_fbm_page_preload_installed = True


install_fbm_page_batch_alignment()
