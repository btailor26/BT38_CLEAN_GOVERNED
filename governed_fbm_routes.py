"""Governed FBM fulfilment workspace.

Phase 1 is deliberately read-only. It reuses MarketplaceOrder as the single
order source and does not create a second order/import path, buy postage,
dispatch marketplace orders, or alter the existing MCF/webhook runtime.
"""
from __future__ import annotations

from flask import Blueprint, render_template, request
from flask_login import login_required

from models import MarketplaceOrder


governed_fbm_bp = Blueprint("governed_fbm", __name__)


def _platform(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "platform", "") or "").strip() or "Unknown"


def _store_name(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "name", "") or "").strip() or "Unknown store"


def _route_state(order: MarketplaceOrder) -> str:
    """Describe the current fulfilment route without executing anything."""
    fulfillment = str(getattr(order, "fulfillment_type", "") or "").upper()
    status = str(getattr(order, "status", "") or "").lower()
    if fulfillment == "FBA" or status.startswith("mcf_"):
        return "MCF / Amazon fulfilment"
    if getattr(order, "tracking_number", None):
        return "Tracking recorded"
    if getattr(order, "shipped_at", None):
        return "Dispatched"
    return "Ready for FBM routing"


@governed_fbm_bp.get("/fbm")
@login_required
def fbm_page():
    """Unified read-only FBM queue backed by existing marketplace orders."""
    platform_filter = str(request.args.get("platform") or "").strip().lower()
    status_filter = str(request.args.get("status") or "").strip().lower()

    rows = (
        MarketplaceOrder.query
        .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
        .limit(300)
        .all()
    )

    seen = set()
    orders = []
    for row in rows:
        key = (row.store_id, row.marketplace_order_id)
        if key in seen:
            continue
        seen.add(key)

        platform = _platform(row)
        route_state = _route_state(row)
        if platform_filter and platform.lower() != platform_filter:
            continue
        if status_filter and route_state.lower() != status_filter:
            continue

        orders.append({
            "order": row,
            "platform": platform,
            "store_name": _store_name(row),
            "route_state": route_state,
        })

    counts = {
        "total": len(orders),
        "ready": sum(1 for item in orders if item["route_state"] == "Ready for FBM routing"),
        "tracking": sum(1 for item in orders if item["route_state"] == "Tracking recorded"),
        "dispatched": sum(1 for item in orders if item["route_state"] == "Dispatched"),
    }

    return render_template(
        "fbm.html",
        orders=orders,
        counts=counts,
        platform_filter=platform_filter,
        status_filter=status_filter,
    )
