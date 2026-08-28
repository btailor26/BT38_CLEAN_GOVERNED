"""Runtime FBM desk alignment for PR 528 testing only.

This module does not import orders, buy postage, dispatch orders, mutate stock, or
change Product Linking. On the explicit /fbm page read it hydrates only the exact
visible marketplace orders whose shipping facts are incomplete, then exposes a
small operational snapshot used by the FBM UI.
"""
from __future__ import annotations

from flask import jsonify, request

from app import app
from extensions import db
from fbm_models import FBMOrderProfile, FBMShipment
from models import MarketplaceOrder
from services.fbm_amazon_order_profile import AmazonOrderProfileError, get_or_refresh_amazon_profile
from services.fbm_operational_state import operational_state, promise_state
from services.fbm_shipping_state import shipment_confirmation_state
from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order


def _platform(order: MarketplaceOrder) -> str:
    return str(getattr(getattr(order, "store", None), "platform", "") or "").strip().lower()


def _eligible(order: MarketplaceOrder) -> bool:
    fulfillment = str(getattr(order, "fulfillment_type", "") or "").upper()
    status = str(getattr(order, "status", "") or "").lower()
    return fulfillment not in {"FBA", "AFN", "MCF"} and not status.startswith("mcf_")


def _latest_shipment(order: MarketplaceOrder):
    return (
        FBMShipment.query
        .filter_by(store_id=order.store_id, marketplace_order_id=order.marketplace_order_id)
        .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
        .first()
    )


def _missing_address(order: MarketplaceOrder) -> list[str]:
    fields = (
        ("ship_to_name", "name"),
        ("ship_to_address", "address"),
        ("ship_to_city", "city"),
        ("ship_to_postcode", "postcode"),
        ("ship_to_country", "country"),
    )
    return [label for attr, label in fields if not str(getattr(order, attr, "") or "").strip()]


def _hydrate_exact_shipping(order: MarketplaceOrder) -> str | None:
    platform = _platform(order)
    try:
        if platform == "amazon":
            get_or_refresh_amazon_profile(order, force=False)
        elif platform == "ebay":
            state = operational_state(order, create=False)
            needs_shipping_facts = not state or not (
                getattr(state, "shipping_service", None)
                and (getattr(state, "earliest_delivery_at", None) or getattr(state, "latest_delivery_at", None))
            )
            if _missing_address(order) or needs_shipping_facts:
                hydrate_exact_ebay_order(
                    store=order.store,
                    marketplace_order_id=str(order.marketplace_order_id),
                    source="fbm_page_alignment",
                )
        return None
    except AmazonOrderProfileError as exc:
        db.session.rollback()
        return str(exc)
    except Exception as exc:
        db.session.rollback()
        app.logger.exception("FBM exact shipping hydration failed for %s", order.marketplace_order_id)
        return str(exc)


@app.get("/fbm/alignment-snapshot")
def fbm_alignment_snapshot():
    raw = str(request.args.get("order_ids") or "")
    order_ids: list[int] = []
    for token in raw.split(","):
        try:
            value = int(token.strip())
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in order_ids:
            order_ids.append(value)
        if len(order_ids) >= 80:
            break

    if not order_ids:
        return jsonify({"success": False, "message": "No FBM orders supplied."}), 400

    rows = MarketplaceOrder.query.filter(MarketplaceOrder.id.in_(order_ids)).all()
    by_id = {row.id: row for row in rows if _eligible(row)}
    result = []

    for order_id in order_ids:
        order = by_id.get(order_id)
        if order is None:
            continue

        hydration_error = _hydrate_exact_shipping(order)
        profile = FBMOrderProfile.query.filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
        ).first()
        shipment = _latest_shipment(order)
        journey = shipment_confirmation_state(shipment) if shipment else "not_started"
        promise = promise_state(order, shipment)
        state = operational_state(order, create=False)
        missing_address = _missing_address(order)
        is_prime = bool(_platform(order) == "amazon" and profile and profile.is_prime is True)

        result.append({
            "id": order.id,
            "marketplace_order_id": order.marketplace_order_id,
            "platform": _platform(order),
            "is_prime": is_prime,
            "prime_locked": is_prime,
            "postcode": str(getattr(order, "ship_to_postcode", "") or "").strip() or None,
            "address_complete": not missing_address,
            "missing_address": missing_address,
            "shipping_service": (
                getattr(state, "shipping_service", None)
                or (getattr(profile, "shipment_service_level", None) if profile else None)
            ),
            "promise": promise.get("label"),
            "promise_available": bool(promise.get("available")),
            "promise_late": bool(promise.get("late")),
            "delivered_late": bool(promise.get("delivered_late")),
            "delivered_on_time": bool(promise.get("delivered_on_time")),
            "journey_state": journey,
            "picked_up": journey in {"accepted", "in_transit", "out_for_delivery", "delivered"},
            "in_transit": journey in {"in_transit", "out_for_delivery", "delivered"},
            "delivered": journey == "delivered",
            "hydration_error": hydration_error,
        })

    return jsonify({"success": True, "orders": result})


@app.after_request
def inject_fbm_live_alignment(response):
    """Load the alignment client on the FBM page without replacing the template."""
    if request.path.rstrip("/") != "/fbm":
        return response
    if response.status_code != 200 or not response.mimetype.startswith("text/html"):
        return response
    try:
        html = response.get_data(as_text=True)
        marker = '<script src="/static/js/fbm_live_alignment.js"></script>'
        if marker not in html and "</body>" in html:
            html = html.replace("</body>", marker + "</body>")
            response.set_data(html)
            response.content_length = len(response.get_data())
    except Exception:
        app.logger.exception("Failed to inject FBM live alignment client")
    return response
