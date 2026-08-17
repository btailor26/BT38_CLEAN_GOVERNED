"""Governed FBM fulfilment workspace.

The BT38 database remains the single source of truth. FBM reads existing
MarketplaceOrder rows only; it does not import orders independently and does
not alter the existing webhook, inventory, Product Linking or MCF paths.

Shipping payments remain with the seller's marketplace/provider account.
This module may recommend a route, but the user can override it. BT38 owns the
carrier/service/tracking mapping submitted back to each marketplace.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from models import MarketplaceOrder
from fbm_models import FBMShipment
from services.fbm_packlink_adapter import PacklinkAdapter
from services.fbm_shipping_state import (
    provider_case_eligibility,
    shipment_confirmation_state,
)


governed_fbm_bp = Blueprint("governed_fbm", __name__)


def _platform(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "platform", "") or "").strip() or "Unknown"


def _store_name(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "name", "") or "").strip() or "Unknown store"


def _route_state(order: MarketplaceOrder) -> str:
    """Describe committed DB fulfilment state without executing anything."""
    fulfillment = str(getattr(order, "fulfillment_type", "") or "").upper()
    status = str(getattr(order, "status", "") or "").lower()
    if fulfillment == "FBA" or status.startswith("mcf_"):
        return "MCF / Amazon fulfilment"
    if getattr(order, "tracking_number", None):
        return "Tracking recorded"
    if getattr(order, "shipped_at", None):
        return "Dispatched"
    return "Ready for FBM routing"


def _marketplace_shipping_mode(order: MarketplaceOrder, platform: str) -> dict:
    """Return the governed shipping choices FBM is allowed to present.

    This is decision metadata only. No rates are fetched and no label is bought.
    Amazon marketplace orders are explicitly eligible for Amazon Buy Shipping,
    including Prime/SFP when Amazon returns an eligible service. External
    providers remain available as a user choice for ordinary FBM flows, while
    BT38 remains responsible for correct marketplace mapping.
    """
    normalized = platform.strip().lower()
    fulfillment = str(getattr(order, "fulfillment_type", "") or "").upper()
    status = str(getattr(order, "status", "") or "").lower()

    if fulfillment == "FBA" or status.startswith("mcf_"):
        return {
            "recommended": "Amazon MCF",
            "marketplace_buy_shipping": False,
            "external_provider": False,
            "manual": False,
            "reason": "Amazon fulfils this order; it is outside the FBM label-buying path.",
        }

    if normalized == "amazon":
        return {
            "recommended": "Amazon Buy Shipping",
            "marketplace_buy_shipping": True,
            "external_provider": True,
            "manual": True,
            "reason": "Amazon-native rates/labels are first-class; SFP/Prime eligibility must come from Amazon, not BT38.",
        }

    if normalized == "ebay":
        return {
            "recommended": "Best connected provider",
            "marketplace_buy_shipping": False,
            "external_provider": True,
            "manual": True,
            "reason": "Use native eBay label buying only when the account/API exposes a supported UK route; otherwise use the seller's connected provider.",
        }

    return {
        "recommended": "Best connected provider",
        "marketplace_buy_shipping": False,
        "external_provider": True,
        "manual": True,
        "reason": "Provider availability is determined by the seller's connected accounts.",
    }


def _shipment_map(rows: list[MarketplaceOrder]) -> dict[tuple[int, str], FBMShipment]:
    """Load the latest FBM shipment for visible DB orders in one bounded query."""
    keys = {(row.store_id, row.marketplace_order_id) for row in rows}
    if not keys:
        return {}

    store_ids = sorted({key[0] for key in keys if key[0] is not None})
    order_ids = sorted({key[1] for key in keys if key[1]})
    shipments = (
        FBMShipment.query
        .filter(FBMShipment.store_id.in_(store_ids))
        .filter(FBMShipment.marketplace_order_id.in_(order_ids))
        .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
        .all()
    )

    result = {}
    for shipment in shipments:
        key = (shipment.store_id, shipment.marketplace_order_id)
        if key in keys and key not in result:
            result[key] = shipment
    return result


@governed_fbm_bp.get("/fbm")
@login_required
def fbm_page():
    """Unified FBM queue backed only by existing MarketplaceOrder DB rows."""
    platform_filter = str(request.args.get("platform") or "").strip().lower()
    status_filter = str(request.args.get("status") or "").strip().lower()

    rows = (
        MarketplaceOrder.query
        .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
        .limit(300)
        .all()
    )
    shipments = _shipment_map(rows)

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

        shipment = shipments.get(key)
        shipment_state = shipment_confirmation_state(shipment) if shipment else "not_started"
        case = provider_case_eligibility(shipment) if shipment else {
            "eligible": False,
            "reason": "shipment_not_started",
            "case_type": None,
        }

        orders.append({
            "order": row,
            "platform": platform,
            "store_name": _store_name(row),
            "route_state": route_state,
            "shipping_mode": _marketplace_shipping_mode(row, platform),
            "shipment": shipment,
            "shipment_state": shipment_state,
            "case": case,
        })

    counts = {
        "total": len(orders),
        "ready": sum(1 for item in orders if item["route_state"] == "Ready for FBM routing"),
        "tracking": sum(1 for item in orders if item["route_state"] == "Tracking recorded"),
        "dispatched": sum(1 for item in orders if item["route_state"] == "Dispatched"),
        "marketplace_shipping": sum(1 for item in orders if item["shipping_mode"]["marketplace_buy_shipping"]),
        "awaiting_acceptance": sum(1 for item in orders if item["shipment_state"] == "awaiting_carrier_acceptance"),
        "overdue": sum(1 for item in orders if item["shipment_state"] == "acceptance_overdue"),
    }

    return render_template(
        "fbm.html",
        orders=orders,
        counts=counts,
        platform_filter=platform_filter,
        status_filter=status_filter,
    )


@governed_fbm_bp.get("/fbm/providers/packlink/connection")
@login_required
def packlink_connection_check():
    """Explicit read-only Packlink authentication check.

    This route never returns the API key and performs no shipment/order writes.
    It exists only so an authenticated BT38 user can prove that the Fly secret
    reaches Packlink successfully before rate or label workflows are enabled.
    """
    result = PacklinkAdapter().connection_check()
    status = 200 if result.ok else (result.status_code or 503)
    return jsonify({
        "success": result.ok,
        "provider": "packlink",
        "configured": result.configured,
        "authenticated": result.ok,
        "status_code": result.status_code,
        "account_country": result.account_country,
        "account_email": result.account_email,
        "message": result.message,
        "read_only": True,
    }), status
