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


def _is_fbm_eligible(order: MarketplaceOrder) -> bool:
    """Return True only for orders that belong in the merchant-fulfilled queue.

    MCF and FBA are Amazon-fulfilled workflows and must never be surfaced in
    FBM. Filtering happens before shipment lookup/counting so the FBM page has
    no dependency on, or visibility into, the MCF execution queue.
    """
    fulfillment = str(getattr(order, "fulfillment_type", "") or "").upper()
    status = str(getattr(order, "status", "") or "").lower()

    if fulfillment in {"FBA", "AFN", "MCF"}:
        return False
    if status.startswith("mcf_"):
        return False

    return True


def _route_state(order: MarketplaceOrder) -> str:
    """Describe committed DB FBM fulfilment state without executing anything."""
    if getattr(order, "tracking_number", None):
        return "Tracking recorded"
    if getattr(order, "shipped_at", None):
        return "Dispatched"
    return "Ready for FBM routing"


def _marketplace_shipping_mode(order: MarketplaceOrder, platform: str) -> dict:
    """Return the governed shipping choices FBM is allowed to present."""
    normalized = platform.strip().lower()

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
            "marketplace_buy_shipping": True,
            "external_provider": True,
            "manual": True,
            "reason": "Show eBay-native shipping when the connected account/API exposes it; otherwise Packlink/direct carrier remains available.",
        }

    return {
        "recommended": "Best connected provider",
        "marketplace_buy_shipping": False,
        "external_provider": True,
        "manual": True,
        "reason": "Provider availability is determined by the seller's connected accounts.",
    }


def _shipping_provider_options(order: MarketplaceOrder) -> list[dict]:
    """Describe shipping routes available to the chooser without buying anything.

    This endpoint intentionally returns capability/connection truth only. Live
    rates are fetched by the individual provider adapter after the user chooses
    an order/route. No shipment is created here.
    """
    platform = _platform(order).strip().lower()
    store = getattr(order, "store", None)
    options: list[dict] = []

    if platform == "amazon":
        creds = getattr(store, "amazon_credentials", None) if store is not None else None
        configured = bool(creds and getattr(creds, "is_valid", lambda: False)())
        options.append({
            "provider": "amazon_buy_shipping",
            "label": "Amazon Buy Shipping",
            "kind": "marketplace",
            "configured": configured,
            "available": configured,
            "recommended": True,
            "supports_prime_sfp": True,
            "message": (
                "Connected Amazon credentials are available. Amazon must return the eligible services for this order."
                if configured
                else "Amazon credentials are not available for this store."
            ),
        })

    if platform == "ebay":
        creds = getattr(store, "ebay_credentials", None) if store is not None else None
        configured = bool(creds and getattr(creds, "is_valid", lambda: False)())
        options.append({
            "provider": "ebay_shipping",
            "label": "eBay Shipping",
            "kind": "marketplace",
            "configured": configured,
            # Native UK label buying is capability-gated. Do not pretend access
            # exists merely because the Trading API credentials are connected.
            "available": False,
            "recommended": False,
            "supports_prime_sfp": False,
            "message": (
                "eBay account is connected. Native label buying will enable only when the eBay app/account exposes a supported UK shipping-label API."
                if configured
                else "eBay credentials are not available for this store."
            ),
        })

    packlink = PacklinkAdapter()
    options.append({
        "provider": "packlink",
        "label": "Packlink PRO",
        "kind": "provider",
        "configured": packlink.configured,
        "available": packlink.configured,
        "recommended": platform != "amazon",
        "supports_prime_sfp": False,
        "message": (
            "Packlink PRO is connected. BT38 will use Packlink for rates/label execution but keep marketplace dispatch mapping under BT38 control."
            if packlink.configured
            else "PACKLINK_API_KEY is not configured."
        ),
    })

    options.append({
        "provider": "manual",
        "label": "Manual / own carrier",
        "kind": "manual",
        "configured": True,
        "available": True,
        "recommended": False,
        "supports_prime_sfp": False,
        "message": "Use a label bought outside BT38. BT38 will still normalise the carrier, service and tracking before marketplace confirmation.",
    })
    return options


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

    raw_rows = (
        MarketplaceOrder.query
        .order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc())
        .limit(300)
        .all()
    )

    # MCF/FBA are excluded before any FBM shipment lookup or page counting.
    rows = [row for row in raw_rows if _is_fbm_eligible(row)]
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


@governed_fbm_bp.get("/fbm/shipping-options")
@login_required
def fbm_shipping_options():
    """Return chooser data for one or more selected DB-backed FBM orders."""
    raw_ids = str(request.args.get("order_ids") or "")
    order_ids: list[int] = []
    for value in raw_ids.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            order_id = int(value)
        except ValueError:
            continue
        if order_id > 0 and order_id not in order_ids:
            order_ids.append(order_id)
        if len(order_ids) >= 50:
            break

    if not order_ids:
        return jsonify({"success": False, "message": "Select at least one FBM order."}), 400

    rows = MarketplaceOrder.query.filter(MarketplaceOrder.id.in_(order_ids)).all()
    by_id = {row.id: row for row in rows if _is_fbm_eligible(row)}

    result = []
    for order_id in order_ids:
        row = by_id.get(order_id)
        if row is None:
            continue
        result.append({
            "id": row.id,
            "marketplace_order_id": row.marketplace_order_id,
            "platform": _platform(row),
            "store_name": _store_name(row),
            "sku": getattr(row, "sku", None),
            "quantity": getattr(row, "quantity", None),
            "postcode": getattr(row, "ship_to_postcode", None),
            "route_state": _route_state(row),
            "providers": _shipping_provider_options(row),
        })

    if not result:
        return jsonify({"success": False, "message": "No selected orders are eligible for FBM shipping."}), 404

    return jsonify({
        "success": True,
        "read_only": True,
        "orders": result,
        "selected_count": len(result),
        "message": "Shipping routes prepared. No label has been purchased and no marketplace has been updated.",
    })


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
