"""Runtime alignment for the FBM operational shipping desk.

This module is intentionally narrow: it adds read-only operational hydration,
order-level parcel persistence, and the browser alignment asset. It does not buy
postage, dispatch marketplace orders, mutate inventory, or alter Product Linking.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from flask import jsonify, request
from flask_login import login_required

from extensions import db
from fbm_models import FBMOrderProfile, FBMShipment
from models import MarketplaceOrder
from services.fbm_operational_state import (
    operational_state,
    promise_state,
    save_order_parcel,
    saved_order_parcel,
    update_marketplace_facts,
)
from services.fbm_order_mapper import apply_parcel_overrides, parcel_from_db
from services.fbm_shipping_state import shipment_confirmation_state


_REFRESH_TTL = timedelta(minutes=5)
_SCRIPT_MARKER = "fbm-operational-alignment-v2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_iso(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _platform(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return _text(getattr(store, "platform", None)).lower()


def _latest_shipment(order: MarketplaceOrder) -> FBMShipment | None:
    return (
        FBMShipment.query
        .filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
        )
        .order_by(FBMShipment.updated_at.desc(), FBMShipment.id.desc())
        .first()
    )


def _profile(order: MarketplaceOrder) -> FBMOrderProfile | None:
    return FBMOrderProfile.query.filter_by(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
    ).first()


def _marketplace_state_fresh(order: MarketplaceOrder) -> bool:
    state = operational_state(order, create=False)
    checked = getattr(state, "marketplace_checked_at", None) if state is not None else None
    return bool(checked and datetime.utcnow() - checked < _REFRESH_TTL and state.promise_available)


def _refresh_marketplace_facts(order: MarketplaceOrder, *, force: bool = False) -> str | None:
    """Refresh only this exact marketplace order when operational facts are stale."""
    if not force and _marketplace_state_fresh(order):
        return None

    platform = _platform(order)
    try:
        if platform == "amazon":
            from services.fbm_amazon_order_profile import (
                get_amazon_delivery_promise,
                get_or_refresh_amazon_profile,
            )

            profile = get_or_refresh_amazon_profile(order, force=True)
            promise = get_amazon_delivery_promise(order)
            update_marketplace_facts(
                order,
                platform="amazon",
                shipping_service=(
                    promise.get("shipment_service_level")
                    or getattr(profile, "shipment_service_level", None)
                ),
                ship_by_at=getattr(profile, "latest_ship_at", None),
                earliest_delivery_at=_parse_iso(promise.get("earliest_delivery_at")),
                latest_delivery_at=_parse_iso(promise.get("latest_delivery_at")),
            )
            db.session.commit()
            return None

        if platform == "ebay":
            from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

            result = hydrate_exact_ebay_order(
                store=order.store,
                marketplace_order_id=str(order.marketplace_order_id),
                source="fbm_operational_alignment",
            ) or {}
            # The exact eBay hydrator owns extraction/persistence of eBay's
            # shipping service and delivery window. Keep a readable warning if
            # the marketplace returned an incomplete result instead of inventing
            # a promise in BT38.
            if result.get("success") is False and result.get("reason"):
                return _text(result.get("reason"))
            return None
    except Exception as exc:
        db.session.rollback()
        return str(exc)

    return "marketplace_operational_reader_not_configured"


def _operational_payload(order: MarketplaceOrder, warning: str | None = None) -> dict[str, Any]:
    shipment = _latest_shipment(order)
    profile = _profile(order)
    state = operational_state(order, create=False)
    promise = promise_state(order, shipment)
    journey_state = shipment_confirmation_state(shipment) if shipment else "not_started"

    parcel = parcel_from_db(order).to_dict()
    parcel.update(saved_order_parcel(order))

    return {
        "success": True,
        "order_id": order.id,
        "marketplace_order_id": order.marketplace_order_id,
        "platform": _platform(order),
        "is_prime": bool(profile and profile.is_prime is True),
        "prime_locked": bool(profile and profile.is_prime is True),
        "shipping_service": getattr(state, "shipping_service", None) if state else None,
        "promise": {
            "available": bool(promise.get("available")),
            "label": promise.get("label"),
            "latest_delivery_at": (
                promise.get("latest_delivery_at").isoformat()
                if isinstance(promise.get("latest_delivery_at"), datetime)
                else promise.get("latest_delivery_at")
            ),
            "late": bool(promise.get("late")),
            "delivered_late": bool(promise.get("delivered_late")),
            "delivered_on_time": bool(promise.get("delivered_on_time")),
        },
        "journey_state": journey_state,
        "parcel": parcel,
        "warning": warning,
    }


def install_fbm_operational_ui_alignment(app) -> None:
    if app.extensions.get("bt38_fbm_operational_ui_alignment"):
        return
    app.extensions["bt38_fbm_operational_ui_alignment"] = True

    @login_required
    def fbm_order_operational(order_id: int):
        order = db.session.get(MarketplaceOrder, order_id)
        if order is None:
            return jsonify({"success": False, "message": "FBM order not found."}), 404
        warning = _refresh_marketplace_facts(
            order,
            force=str(request.args.get("refresh") or "").lower() in {"1", "true", "yes"},
        )
        return jsonify(_operational_payload(order, warning))

    @login_required
    def fbm_order_parcel(order_id: int):
        order = db.session.get(MarketplaceOrder, order_id)
        if order is None:
            return jsonify({"success": False, "message": "FBM order not found."}), 404
        body = request.get_json(silent=True) or {}
        values = body.get("parcel") if isinstance(body.get("parcel"), dict) else body
        allowed = {
            key: values.get(key)
            for key in ("weight_kg", "length_cm", "width_cm", "height_cm")
            if values.get(key) not in (None, "")
        }
        if not allowed:
            return jsonify({"success": False, "message": "Enter at least one parcel value."}), 400

        try:
            save_order_parcel(order, allowed)
            # Keep the existing safe reusable-SKU behaviour as a second layer;
            # order-level persistence above is authoritative for this packed order.
            apply_parcel_overrides(parcel_from_db(order), allowed)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            return jsonify({"success": False, "message": str(exc)}), 500

        parcel = parcel_from_db(order).to_dict()
        parcel.update(saved_order_parcel(order))
        return jsonify({
            "success": True,
            "order_id": order.id,
            "parcel": parcel,
            "saved": True,
            "message": "Packed parcel saved for this order.",
        })

    app.add_url_rule(
        "/governed/fbm/orders/<int:order_id>/operational",
        endpoint="bt38_fbm_order_operational",
        view_func=fbm_order_operational,
        methods=["GET"],
    )
    app.add_url_rule(
        "/governed/fbm/orders/<int:order_id>/parcel",
        endpoint="bt38_fbm_order_parcel",
        view_func=fbm_order_parcel,
        methods=["POST"],
    )

    @app.after_request
    def inject_fbm_operational_alignment(response):
        if request.path.rstrip("/") != "/fbm":
            return response
        if response.status_code != 200 or "text/html" not in _text(response.content_type).lower():
            return response
        html = response.get_data(as_text=True)
        if _SCRIPT_MARKER in html:
            return response
        tag = (
            f'<script id="{_SCRIPT_MARKER}" '
            'src="/static/js/fbm_operational_alignment_v2.js"></script>'
        )
        if "</body>" in html:
            html = html.replace("</body>", f"{tag}</body>", 1)
        else:
            html += tag
        response.set_data(html)
        return response
