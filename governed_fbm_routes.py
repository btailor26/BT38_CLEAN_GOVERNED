"""Governed FBM fulfilment workspace.

The BT38 database remains the single source of truth. FBM reads existing
MarketplaceOrder rows only; it does not run a second marketplace order import.
Shipping execution is isolated from inventory, Product Linking and MCF.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import MarketplaceOrder
from fbm_models import (
    FBMCarrierServiceMapping,
    FBMOrderProfile,
    FBMRateQuote,
    FBMShipment,
)
from services.fbm_amazon_order_profile import AmazonOrderProfileError, get_or_refresh_amazon_profile
from services.fbm_amazon_shipping_adapter import AmazonShippingAdapter, AmazonShippingError
from services.fbm_carrier_mapping import mapping_payload, verify_mapping
from services.fbm_order_mapper import apply_parcel_overrides, order_lines, parcel_from_db, provider_parcel, ship_to
from services.fbm_packlink_adapter import PacklinkAdapter, PacklinkConfigurationError, PacklinkRequestError
from services.fbm_post_purchase import persist_external_label
from services.fbm_shipping_state import provider_case_eligibility, shipment_confirmation_state


governed_fbm_bp = Blueprint("governed_fbm", __name__)


def _platform(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "platform", "") or "").strip() or "Unknown"


def _store_name(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "name", "") or "").strip() or "Unknown store"


def _is_fbm_eligible(order: MarketplaceOrder) -> bool:
    fulfillment = str(getattr(order, "fulfillment_type", "") or "").upper()
    status = str(getattr(order, "status", "") or "").lower()
    return fulfillment not in {"FBA", "AFN", "MCF"} and not status.startswith("mcf_")


def _route_state(order: MarketplaceOrder) -> str:
    if getattr(order, "tracking_number", None):
        return "Tracking recorded"
    if getattr(order, "shipped_at", None):
        return "Dispatched"
    return "Ready for FBM routing"


def _profile_for(order: MarketplaceOrder) -> FBMOrderProfile | None:
    return FBMOrderProfile.query.filter_by(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
    ).first()


def _amazon_profile(order: MarketplaceOrder, *, refresh: bool = False) -> tuple[FBMOrderProfile | None, str | None]:
    if _platform(order).strip().lower() != "amazon":
        return None, None
    try:
        return get_or_refresh_amazon_profile(order, force=refresh), None
    except AmazonOrderProfileError as exc:
        return _profile_for(order), str(exc)


def _marketplace_shipping_mode(order: MarketplaceOrder, platform: str, profile: FBMOrderProfile | None = None) -> dict:
    normalized = platform.strip().lower()
    if normalized == "amazon":
        is_prime = bool(profile and profile.is_prime is True)
        profile_known = bool(profile and profile.is_prime is not None)
        return {
            "recommended": "Amazon Buy Shipping",
            "marketplace_buy_shipping": True,
            # Unknown Prime status must not be treated as Prime. Only a positive
            # Amazon Prime/SFP classification locks external/manual routes.
            "external_provider": not is_prime,
            "manual": not is_prime,
            "prime_locked": is_prime,
            "profile_known": profile_known,
            "reason": (
                "Prime/SFP order: Amazon Buy Shipping only; Amazon decides the eligible carrier/service."
                if is_prime
                else "Amazon Buy Shipping is available. Packlink / external carrier and manual dispatch remain available unless Amazon positively identifies this order as Prime/SFP."
            ),
        }
    if normalized == "ebay":
        return {
            "recommended": "Best connected provider",
            "marketplace_buy_shipping": True,
            "external_provider": True,
            "manual": True,
            "prime_locked": False,
            "profile_known": True,
            "reason": "Use eBay-native shipping where supported, otherwise connected provider/carrier.",
        }
    return {
        "recommended": "Best connected provider",
        "marketplace_buy_shipping": False,
        "external_provider": True,
        "manual": True,
        "prime_locked": False,
        "profile_known": True,
        "reason": "Provider availability is determined by connected accounts.",
    }


def _shipping_provider_options(order: MarketplaceOrder, profile: FBMOrderProfile | None = None, profile_error: str | None = None) -> list[dict]:
    platform = _platform(order).strip().lower()
    store = getattr(order, "store", None)
    is_prime = bool(profile and profile.is_prime is True)
    profile_known = bool(profile and profile.is_prime is not None)
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
            "prime_locked": is_prime,
            "label_formats": ["PDF", "PNG", "ZPL"],
            "auto_print_supported": True,
            "requires_terms_acceptance": True,
            "message": (
                "Prime/SFP: Amazon controls the eligible carrier/service and this is the only allowed purchase route."
                if is_prime
                else "Amazon uses its own order context for Buy Shipping. BT38 supplies the packed parcel details; the buyer address is not a BT38 prerequisite for this marketplace-native route."
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
            "available": False,
            "recommended": False,
            "supports_prime_sfp": False,
            "prime_locked": False,
            "label_formats": ["PDF"],
            "auto_print_supported": True,
            "requires_terms_acceptance": False,
            "message": "eBay account is connected. Native UK label buying stays capability-gated until eBay exposes it for this app/account." if configured else "eBay credentials are not available for this store.",
        })

    packlink = PacklinkAdapter()
    external_allowed = platform != "amazon" or not is_prime
    options.append({
        "provider": "packlink",
        "label": "Packlink PRO",
        "kind": "provider",
        "configured": packlink.configured,
        "available": packlink.configured and external_allowed,
        "recommended": platform != "amazon",
        "supports_prime_sfp": False,
        "prime_locked": is_prime,
        "label_formats": ["PDF"],
        "auto_print_supported": True,
        "requires_terms_acceptance": False,
        "payment_mode": "provider_checkout_required",
        "message": (
            "Prime/SFP is locked to Amazon Buy Shipping."
            if is_prime
            else "Packlink PRO live rates and shipment drafting are connected. Full BT38 delivery details are required only when this external route is used. Packlink-side payment is still required before the label becomes available."
            if packlink.configured
            else "PACKLINK_API_KEY is not configured."
        ),
    })

    manual_allowed = platform != "amazon" or not is_prime
    options.append({
        "provider": "manual",
        "label": "Manual / own carrier",
        "kind": "manual",
        "configured": True,
        "available": manual_allowed,
        "recommended": False,
        "supports_prime_sfp": False,
        "prime_locked": is_prime,
        "label_formats": [],
        "auto_print_supported": False,
        "requires_terms_acceptance": False,
        "message": "Prime/SFP is locked to Amazon Buy Shipping." if is_prime else "Use a label bought outside BT38; BT38 will normalise carrier/service/tracking before marketplace confirmation.",
    })
    return options


def _shipment_map(rows: list[MarketplaceOrder]) -> dict[tuple[int, str], FBMShipment]:
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


def _get_fbm_order(order_id: int) -> MarketplaceOrder | None:
    row = db.session.get(MarketplaceOrder, order_id)
    return row if row is not None and _is_fbm_eligible(row) else None


def _find_rate(quote: FBMRateQuote, rate_id: str) -> dict | None:
    return next(
        (
            rate for rate in (quote.rates or [])
            if isinstance(rate, dict)
            and str(rate.get("rate_id") or rate.get("id") or rate.get("service_id") or "") == str(rate_id)
        ),
        None,
    )


def _money_value(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "amount", "total", "price"):
            if value.get(key) is not None:
                try:
                    return float(value[key])
                except (TypeError, ValueError):
                    pass
    return None


def _tracking_code(shipment_payload: dict) -> str | None:
    values = shipment_payload.get("trackings") or shipment_payload.get("tracking_codes") or []
    if isinstance(values, str):
        return values.strip() or None
    if isinstance(values, list):
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                candidate = value.get("code") or value.get("tracking_number") or value.get("tracking")
                if candidate:
                    return str(candidate).strip() or None
    return None


@governed_fbm_bp.get("/fbm")
@login_required
def fbm_page():
    platform_filter = str(request.args.get("platform") or "").strip().lower()
    status_filter = str(request.args.get("status") or "").strip().lower()
    raw_rows = MarketplaceOrder.query.order_by(MarketplaceOrder.created_at.desc(), MarketplaceOrder.id.desc()).limit(300).all()
    rows = [row for row in raw_rows if _is_fbm_eligible(row)]
    shipments = _shipment_map(rows)
    seen, orders = set(), []
    for row in rows:
        key = (row.store_id, row.marketplace_order_id)
        if key in seen:
            continue
        seen.add(key)
        platform, route_state = _platform(row), _route_state(row)
        if platform_filter and platform.lower() != platform_filter:
            continue
        if status_filter and route_state.lower() != status_filter:
            continue
        profile = _profile_for(row)
        shipment = shipments.get(key)
        shipment_state = shipment_confirmation_state(shipment) if shipment else "not_started"
        case = provider_case_eligibility(shipment) if shipment else {"eligible": False, "reason": "shipment_not_started", "case_type": None}
        mapping_review = getattr(shipment, "mapping_review", None) if shipment else None
        orders.append({
            "order": row,
            "platform": platform,
            "store_name": _store_name(row),
            "route_state": route_state,
            "shipping_mode": _marketplace_shipping_mode(row, platform, profile),
            "shipment": shipment,
            "shipment_state": shipment_state,
            "case": case,
            "profile": profile,
            "mapping_review": mapping_review,
        })
    counts = {
        "total": len(orders),
        "ready": sum(1 for i in orders if i["route_state"] == "Ready for FBM routing"),
        "tracking": sum(1 for i in orders if i["route_state"] == "Tracking recorded"),
        "dispatched": sum(1 for i in orders if i["route_state"] == "Dispatched"),
        "marketplace_shipping": sum(1 for i in orders if i["shipping_mode"]["marketplace_buy_shipping"]),
        "awaiting_acceptance": sum(1 for i in orders if i["shipment_state"] == "awaiting_carrier_acceptance"),
        "overdue": sum(1 for i in orders if i["shipment_state"] == "acceptance_overdue"),
        "mapping_review": sum(1 for i in orders if i["mapping_review"] and i["mapping_review"].status == "under_review"),
    }
    return render_template("fbm.html", orders=orders, counts=counts, platform_filter=platform_filter, status_filter=status_filter)


@governed_fbm_bp.get("/fbm/shipping-options")
@login_required
def fbm_shipping_options():
    raw_ids = str(request.args.get("order_ids") or "")
    order_ids: list[int] = []
    for value in raw_ids.split(","):
        try:
            order_id = int(value.strip())
        except (ValueError, TypeError):
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
        profile, profile_error = _amazon_profile(row) if _platform(row).strip().lower() == "amazon" else (_profile_for(row), None)
        parcel = parcel_from_db(row)
        result.append({
            "id": row.id,
            "marketplace_order_id": row.marketplace_order_id,
            "platform": _platform(row),
            "store_name": _store_name(row),
            "sku": getattr(row, "sku", None),
            "quantity": sum(max(1, int(getattr(line, "quantity", 1) or 1)) for line in order_lines(row)),
            "postcode": getattr(row, "ship_to_postcode", None),
            "route_state": _route_state(row),
            "is_prime": profile.is_prime if profile else None,
            "prime_profile_error": profile_error,
            "parcel": parcel.to_dict(),
            "providers": _shipping_provider_options(row, profile, profile_error),
        })
    if not result:
        return jsonify({"success": False, "message": "No selected orders are eligible for FBM shipping."}), 404
    return jsonify({
        "success": True,
        "orders": result,
        "selected_count": len(result),
        "printing": {"mode": "qz_tray", "auto_print_after_purchase": True, "printer_preference_required": True, "fallback": "download_label"},
        "message": "Shipping routes and DB parcel defaults prepared.",
    })


@governed_fbm_bp.post("/fbm/orders/<int:order_id>/packlink/rates")
@login_required
def packlink_rates(order_id: int):
    order = _get_fbm_order(order_id)
    if order is None:
        return jsonify({"success": False, "message": "FBM order not found."}), 404
    if _platform(order).strip().lower() == "amazon":
        profile, error = _amazon_profile(order)
        # Unknown Prime status is not Prime. Only a positive Prime/SFP result
        # blocks external shipping; Packlink itself still requires the full
        # BT38 destination address before rates can be requested.
        if profile is not None and profile.is_prime is True:
            return jsonify({"success": False, "message": "Prime/SFP orders must use Amazon Buy Shipping."}), 409

    body = request.get_json(silent=True) or {}
    parcel = provider_parcel(order, body.get("parcel") or {})
    destination = ship_to(order)
    missing = []
    for key, label in (("name", "destination name"), ("address1", "destination address"), ("city", "destination city"), ("postcode", "destination postcode"), ("country", "destination country")):
        if not destination.get(key):
            missing.append(label)
    for name in ("weight_kg", "length_cm", "width_cm", "height_cm"):
        if not parcel.get(name):
            missing.append(name)
    if missing:
        return jsonify({"success": False, "message": "External shipping data is incomplete.", "missing": missing, "parcel": parcel}), 422
    try:
        rates = PacklinkAdapter().get_rates(order=order, parcel=parcel)
    except (PacklinkConfigurationError, PacklinkRequestError) as exc:
        return jsonify({"success": False, "message": str(exc)}), getattr(exc, "status_code", None) or 502

    quote = FBMRateQuote(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
        provider="packlink",
        parcel=parcel,
        rates=rates,
        expires_at=datetime.utcnow() + timedelta(minutes=15),
    )
    db.session.add(quote)
    db.session.commit()
    return jsonify({
        "success": True,
        "provider": "packlink",
        "order_id": order.id,
        "marketplace_order_id": order.marketplace_order_id,
        "quote_id": quote.id,
        "expires_at": quote.expires_at.isoformat(),
        "rates": rates,
        "parcel": parcel,
        "payment_mode": "packlink_checkout_required",
    })


@governed_fbm_bp.post("/fbm/orders/<int:order_id>/packlink/draft")
@login_required
def packlink_create_draft(order_id: int):
    """Create one Packlink shipment draft; this does not claim postage is paid."""
    order = _get_fbm_order(order_id)
    if order is None:
        return jsonify({"success": False, "message": "FBM order not found."}), 404
    if _platform(order).strip().lower() == "amazon":
        profile, error = _amazon_profile(order, refresh=True)
        if profile is not None and profile.is_prime is True:
            return jsonify({"success": False, "message": "Prime/SFP orders must use Amazon Buy Shipping."}), 409

    body = request.get_json(silent=True) or {}
    if body.get("confirm_create") != "CREATE_PACKLINK_DRAFT":
        return jsonify({"success": False, "message": "Explicit CREATE_PACKLINK_DRAFT confirmation is required."}), 400
    try:
        quote_id = int(body.get("quote_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Valid quote_id is required."}), 400
    rate_id = str(body.get("rate_id") or "").strip()
    quote = db.session.get(FBMRateQuote, quote_id)
    if quote is None or quote.provider != "packlink" or quote.store_id != order.store_id or quote.marketplace_order_id != order.marketplace_order_id:
        return jsonify({"success": False, "message": "Packlink rate quote does not belong to this order."}), 409
    if quote.expired:
        return jsonify({"success": False, "message": "Packlink rate quote expired. Get fresh rates."}), 409
    selected = _find_rate(quote, rate_id)
    if selected is None:
        return jsonify({"success": False, "message": "Selected Packlink service is not in the stored quote."}), 409

    purchase_key = f"packlink_draft:{order.store_id}:{order.marketplace_order_id}"
    existing = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
    if existing is not None:
        return jsonify({
            "success": True,
            "already_created": True,
            "shipment_id": existing.id,
            "provider_reference": existing.provider_shipment_id,
            "payment_status": existing.purchase_status,
            "message": "A Packlink shipment draft already exists for this order. No duplicate draft was created.",
        })

    shipment = FBMShipment(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
        provider="packlink",
        provider_service_id=str(selected.get("service_id") or selected.get("id") or "") or None,
        carrier=str(selected.get("carrier_name") or selected.get("carrier") or "").strip() or None,
        service=str(selected.get("service_name") or selected.get("service") or "").strip() or None,
        purchase_key=purchase_key,
        selected_rate_id=rate_id,
        purchase_status="draft_creating",
        status="awaiting_provider_payment",
    )
    db.session.add(shipment)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
        return jsonify({"success": False, "message": "A Packlink draft already exists or is being created.", "shipment_id": existing.id if existing else None}), 409

    try:
        draft = PacklinkAdapter().create_shipment_draft(order=order, parcel=quote.parcel or {}, rate=selected)
    except Exception as exc:
        shipment.purchase_status = "draft_verification_required"
        shipment.purchase_error = str(exc)
        shipment.status = "draft_verification_required"
        db.session.commit()
        return jsonify({
            "success": False,
            "message": "Packlink did not return a confirmed draft reference. BT38 blocked automatic retry until this attempt is checked.",
            "detail": str(exc),
            "shipment_id": shipment.id,
        }), 502

    shipment.provider_shipment_id = draft["reference"]
    shipment.purchase_status = "pending_provider_payment"
    shipment.purchase_error = None
    shipment.status = "awaiting_provider_payment"
    quote.used_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        "success": True,
        "shipment_id": shipment.id,
        "provider_reference": shipment.provider_shipment_id,
        "payment_status": "pending_provider_payment",
        "label_ready": False,
        "message": "Packlink draft created. Packlink payment is still required before the carrier label becomes available.",
    })


@governed_fbm_bp.get("/fbm/shipments/<int:shipment_id>/packlink/status")
@login_required
def packlink_shipment_status(shipment_id: int):
    shipment = db.session.get(FBMShipment, shipment_id)
    if shipment is None or shipment.provider != "packlink" or not shipment.provider_shipment_id:
        return jsonify({"success": False, "message": "Packlink shipment not found."}), 404
    order = MarketplaceOrder.query.filter_by(store_id=shipment.store_id, marketplace_order_id=shipment.marketplace_order_id).first()
    if order is None:
        return jsonify({"success": False, "message": "Marketplace order for this shipment is missing."}), 404

    adapter = PacklinkAdapter()
    try:
        provider_payload = adapter.get_shipment(shipment.provider_shipment_id)
        labels = adapter.get_labels(shipment.provider_shipment_id)
        tracking_history = adapter.get_tracking_status(reference=shipment.provider_shipment_id)
    except (PacklinkConfigurationError, PacklinkRequestError) as exc:
        return jsonify({"success": False, "message": str(exc)}), getattr(exc, "status_code", None) or 502

    provider_state = str(provider_payload.get("state") or provider_payload.get("status") or "").strip()
    shipment.last_provider_status = provider_state or shipment.last_provider_status
    shipment.last_provider_checked_at = datetime.utcnow()
    carrier = str(provider_payload.get("carrier") or shipment.carrier or "").strip() or None
    service = str(provider_payload.get("service") or shipment.service or "").strip() or None
    tracking = _tracking_code(provider_payload) or shipment.tracking_number

    if labels:
        first_label = labels[0]
        label_url = first_label if isinstance(first_label, str) else (first_label.get("url") if isinstance(first_label, dict) else None)
        result = persist_external_label(
            shipment=shipment,
            marketplace=_platform(order),
            provider="packlink",
            provider_shipment_id=shipment.provider_shipment_id,
            carrier=carrier,
            service=service,
            tracking_number=tracking,
            provider_service_id=str(provider_payload.get("service_id") or shipment.provider_service_id or "") or None,
            label={"type": "LABEL", "format": "PDF", "url": label_url, "storage_ref": shipment.provider_shipment_id},
        )
        return jsonify({
            "success": True,
            "payment_complete": True,
            "label_ready": True,
            "label": {"format": "PDF", "url": label_url},
            "provider_status": provider_state,
            "tracking": tracking,
            "tracking_history": tracking_history,
            **result,
        })

    db.session.commit()
    return jsonify({
        "success": True,
        "payment_complete": False,
        "label_ready": False,
        "shipment_id": shipment.id,
        "provider_reference": shipment.provider_shipment_id,
        "provider_status": provider_state,
        "payment_status": shipment.purchase_status,
        "message": "Packlink label is not available yet. The shipment may still require Packlink-side payment or label generation.",
    })


@governed_fbm_bp.post("/fbm/orders/<int:order_id>/amazon/rates")
@login_required
def amazon_rates(order_id: int):
    order = _get_fbm_order(order_id)
    if order is None or _platform(order).strip().lower() != "amazon":
        return jsonify({"success": False, "message": "Amazon FBM order not found."}), 404

    profile, profile_error = _amazon_profile(order, refresh=True)
    if profile is None:
        return jsonify({"success": False, "message": profile_error or "Amazon shipping profile could not be verified."}), 502
    if str(profile.fulfillment_channel or "").upper() not in {"MFN", "FBM", "SELLERFULFILLED", "SELLER_FULFILLED"}:
        return jsonify({"success": False, "message": f"Amazon order is not confirmed merchant-fulfilled ({profile.fulfillment_channel or 'unknown'})."}), 409

    body = request.get_json(silent=True) or {}
    resolved = apply_parcel_overrides(parcel_from_db(order), body.get("parcel") or {})
    missing = []
    for field in ("weight_kg", "length_cm", "width_cm", "height_cm"):
        if not getattr(resolved, field):
            missing.append(field)
    if missing:
        return jsonify({"success": False, "message": "Parcel data is incomplete.", "missing": missing, "parcel": resolved.to_dict(), "is_prime": profile.is_prime}), 422

    try:
        result = AmazonShippingAdapter(order.store).get_rates(order=order, parcel=resolved.to_dict())
    except AmazonShippingError as exc:
        return jsonify({"success": False, "message": str(exc), "is_prime": profile.is_prime}), 502
    if not result.request_token:
        return jsonify({"success": False, "message": "Amazon returned rates without a purchase token."}), 502

    quote = FBMRateQuote(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
        provider="amazon_buy_shipping",
        request_token=result.request_token,
        parcel=resolved.to_dict(),
        rates=result.rates,
        ineligible_rates=result.ineligible_rates,
        expires_at=datetime.utcnow() + timedelta(minutes=9),
    )
    db.session.add(quote)
    db.session.commit()
    return jsonify({
        "success": True,
        "provider": "amazon_buy_shipping",
        "order_id": order.id,
        "marketplace_order_id": order.marketplace_order_id,
        "is_prime": profile.is_prime,
        "prime_locked": profile.is_prime is True,
        "quote_id": quote.id,
        "expires_at": quote.expires_at.isoformat(),
        "rates": result.rates,
        "ineligible_rates": result.ineligible_rates,
        "parcel": resolved.to_dict(),
    })


@governed_fbm_bp.post("/fbm/orders/<int:order_id>/amazon/purchase")
@login_required
def amazon_purchase(order_id: int):
    """Purchase one Amazon Buy Shipping label with DB-backed idempotency."""
    order = _get_fbm_order(order_id)
    if order is None or _platform(order).strip().lower() != "amazon":
        return jsonify({"success": False, "message": "Amazon FBM order not found."}), 404

    body = request.get_json(silent=True) or {}
    if body.get("confirm_purchase") != "BUY_POSTAGE":
        return jsonify({"success": False, "message": "Explicit BUY_POSTAGE confirmation is required."}), 400
    try:
        quote_id = int(body.get("quote_id"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Valid quote_id is required."}), 400
    rate_id = str(body.get("rate_id") or "").strip()
    try:
        document_index = int(body.get("document_index", 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid document selection."}), 400

    quote = db.session.get(FBMRateQuote, quote_id)
    if quote is None or quote.provider != "amazon_buy_shipping" or quote.store_id != order.store_id or quote.marketplace_order_id != order.marketplace_order_id:
        return jsonify({"success": False, "message": "Amazon rate quote does not belong to this order."}), 409
    if quote.used_at:
        return jsonify({"success": False, "message": "This Amazon rate quote has already been used."}), 409
    if quote.expired:
        return jsonify({"success": False, "message": "Amazon rate quote expired. Get fresh rates before buying."}), 409

    selected = _find_rate(quote, rate_id)
    if selected is None:
        return jsonify({"success": False, "message": "Selected Amazon rate is not in the stored quote."}), 409
    documents = selected.get("supported_documents") or []
    if document_index < 0 or document_index >= len(documents) or not isinstance(documents[document_index], dict):
        return jsonify({"success": False, "message": "Choose one of Amazon's supported label formats for this rate."}), 400
    document_spec = documents[document_index]

    carrier_name = str(selected.get("carrier_name") or "").strip()
    if "royal mail" in carrier_name.lower() and body.get("accept_carrier_terms") is not True:
        return jsonify({"success": False, "message": "Royal Mail terms must be accepted before purchasing this service."}), 400

    profile, profile_error = _amazon_profile(order, refresh=True)
    if profile is None:
        return jsonify({"success": False, "message": profile_error or "Amazon shipping profile could not be verified."}), 502
    if str(profile.fulfillment_channel or "").upper() not in {"MFN", "FBM", "SELLERFULFILLED", "SELLER_FULFILLED"}:
        return jsonify({"success": False, "message": "Amazon order is no longer confirmed merchant-fulfilled."}), 409

    purchase_key = f"amazon_buy_shipping:{order.store_id}:{order.marketplace_order_id}"
    existing = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
    if existing is not None:
        if existing.purchase_status == "purchased":
            return jsonify({
                "success": True,
                "already_purchased": True,
                "shipment_id": existing.id,
                "provider_shipment_id": existing.provider_shipment_id,
                "tracking_number": existing.tracking_number,
                "carrier": existing.carrier,
                "service": existing.service,
                "label": None,
                "message": "Postage was already purchased for this order. No second charge was made.",
            })
        return jsonify({"success": False, "message": "A previous purchase attempt exists for this order and must be verified before any retry.", "purchase_status": existing.purchase_status, "shipment_id": existing.id}), 409

    shipment = FBMShipment(
        store_id=order.store_id,
        marketplace_order_id=order.marketplace_order_id,
        provider="amazon_buy_shipping",
        provider_carrier_id=str(selected.get("carrier_id") or "") or None,
        provider_service_id=str(selected.get("service_id") or "") or None,
        carrier=carrier_name or None,
        service=str(selected.get("service_name") or "").strip() or None,
        purchase_key=purchase_key,
        selected_rate_id=rate_id,
        purchase_status="pending",
        status="awaiting_label",
    )
    db.session.add(shipment)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
        return jsonify({"success": False, "message": "A purchase for this order is already in progress or completed.", "shipment_id": existing.id if existing else None}), 409

    try:
        purchase = AmazonShippingAdapter(order.store).purchase_shipment(
            request_token=quote.request_token,
            rate_id=rate_id,
            requested_document_specification=document_spec,
            requested_value_added_services=body.get("requested_value_added_services") or None,
            additional_inputs=body.get("additional_inputs") or None,
        )
    except Exception as exc:
        shipment.purchase_status = "verification_required"
        shipment.purchase_error = str(exc)
        shipment.status = "purchase_verification_required"
        db.session.commit()
        return jsonify({
            "success": False,
            "message": "Amazon purchase did not return a confirmed success. BT38 has blocked automatic retry to prevent a duplicate postage charge.",
            "detail": str(exc),
            "shipment_id": shipment.id,
        }), 502

    now = datetime.utcnow()
    label = purchase.get("label") or {}
    shipment.provider_shipment_id = str(purchase.get("shipment_id") or "").strip() or None
    shipment.tracking_number = str(purchase.get("tracking_id") or "").strip() or None
    shipment.label_format = str(label.get("format") or document_spec.get("format") or "").upper() or None
    shipment.label_document_type = str(label.get("type") or "LABEL")
    size = document_spec.get("size") if isinstance(document_spec.get("size"), dict) else {}
    shipment.label_width = _money_value(size.get("width"))
    shipment.label_length = _money_value(size.get("length"))
    shipment.label_size_unit = str(size.get("unit") or "").strip() or None
    try:
        shipment.label_dpi = int(document_spec.get("dpi")) if document_spec.get("dpi") is not None else None
    except (TypeError, ValueError):
        shipment.label_dpi = None
    shipment.label_page_layout = str(document_spec.get("pageLayout") or "").strip() or None
    shipment.label_source = "amazon"
    shipment.label_storage_ref = str(purchase.get("package_client_reference_id") or f"BT38-{order.store_id}-{order.marketplace_order_id}")
    shipment.purchase_status = "purchased"
    shipment.purchase_error = None
    shipment.status = "awaiting_carrier_acceptance"
    shipment.label_purchased_at = now
    shipment.marketplace_confirmed_at = now
    shipment.marketplace_confirmation_status = "amazon_buy_shipping_managed_by_amazon"
    quote.used_at = now

    for line in order_lines(order):
        line.carrier = shipment.carrier
        if shipment.tracking_number:
            line.tracking_number = shipment.tracking_number
    shipping_cost = _money_value(selected.get("price"))
    if shipping_cost is not None:
        order.shipping_cost = shipping_cost
    db.session.commit()

    printable_label = None
    if label.get("contents"):
        printable_label = {
            "format": shipment.label_format,
            "base64": label.get("contents"),
            "width": shipment.label_width,
            "height": shipment.label_length,
            "units": shipment.label_size_unit,
            "dpi": shipment.label_dpi,
        }
    return jsonify({
        "success": True,
        "already_purchased": False,
        "shipment_id": shipment.id,
        "provider_shipment_id": shipment.provider_shipment_id,
        "tracking_number": shipment.tracking_number,
        "carrier": shipment.carrier,
        "service": shipment.service,
        "label": printable_label,
        "mapping_status": "marketplace_native",
        "marketplace_confirmation": shipment.marketplace_confirmation_status,
        "message": "Amazon Buy Shipping purchase succeeded and was persisted before printing. Amazon manages the on-Amazon shipment confirmation for this marketplace-native label.",
    })


@governed_fbm_bp.get("/fbm/shipments/<int:shipment_id>/amazon/tracking")
@login_required
def amazon_tracking(shipment_id: int):
    shipment = db.session.get(FBMShipment, shipment_id)
    if shipment is None or shipment.provider != "amazon_buy_shipping":
        return jsonify({"success": False, "message": "Amazon Buy Shipping shipment not found."}), 404
    if not shipment.tracking_number or not shipment.provider_carrier_id:
        return jsonify({"success": False, "message": "Amazon has not returned a trackable parcel identifier for this shipment yet."}), 409
    order = MarketplaceOrder.query.filter_by(store_id=shipment.store_id, marketplace_order_id=shipment.marketplace_order_id).first()
    if order is None:
        return jsonify({"success": False, "message": "Marketplace order for this shipment is missing."}), 404
    try:
        payload = AmazonShippingAdapter(order.store).get_tracking(tracking_id=shipment.tracking_number, carrier_id=shipment.provider_carrier_id)
    except AmazonShippingError as exc:
        return jsonify({"success": False, "message": str(exc)}), 502

    now = datetime.utcnow()
    summary = payload.get("summary") or {}
    summary_status = str(summary.get("status") if isinstance(summary, dict) else summary or "").strip()
    normalized = summary_status.upper().replace(" ", "_")
    shipment.last_provider_status = summary_status or shipment.last_provider_status
    shipment.last_provider_checked_at = now
    if "DELIVER" in normalized:
        shipment.status = "delivered"
        shipment.delivered_at = shipment.delivered_at or now
    elif any(token in normalized for token in ("TRANSIT", "OUT_FOR_DELIVERY", "PICKED_UP")):
        shipment.status = "in_transit"
    elif any(token in normalized for token in ("ACCEPT", "PRE_TRANSIT", "CREATED", "LABEL")):
        shipment.status = "accepted"
        shipment.carrier_accepted_at = shipment.carrier_accepted_at or now
    db.session.commit()
    return jsonify({
        "success": True,
        "shipment_id": shipment.id,
        "tracking_number": shipment.tracking_number,
        "carrier": shipment.carrier,
        "service": shipment.service,
        "provider_status": summary_status,
        "state": shipment_confirmation_state(shipment),
        "tracking": payload,
    })


@governed_fbm_bp.post("/fbm/carrier-mappings/<int:mapping_id>/verify")
@login_required
def verify_carrier_mapping(mapping_id: int):
    mapping = db.session.get(FBMCarrierServiceMapping, mapping_id)
    if mapping is None:
        return jsonify({"success": False, "message": "Carrier mapping not found."}), 404
    body = request.get_json(silent=True) or {}
    marketplace_carrier_code = str(body.get("marketplace_carrier_code") or "").strip()
    marketplace_service_code = str(body.get("marketplace_service_code") or "").strip() or None
    if not marketplace_carrier_code:
        return jsonify({"success": False, "message": "Marketplace carrier code is required."}), 400
    verified_by = str(getattr(current_user, "username", None) or getattr(current_user, "email", None) or getattr(current_user, "id", "user"))
    mapping = verify_mapping(
        mapping=mapping,
        marketplace_carrier_code=marketplace_carrier_code,
        marketplace_service_code=marketplace_service_code,
        verified_by=verified_by,
    )
    return jsonify({"success": True, "mapping": mapping_payload(mapping), "message": "Carrier/service mapping verified and saved for future matching shipments."})


@governed_fbm_bp.get("/fbm/packlink/connection")
@login_required
def packlink_connection():
    adapter = PacklinkAdapter()
    if not adapter.configured:
        return jsonify({"success": False, "message": "PACKLINK_API_KEY is not configured."}), 503
    try:
        result = adapter.test_connection()
    except (PacklinkConfigurationError, PacklinkRequestError) as exc:
        return jsonify({"success": False, "message": str(exc)}), getattr(exc, "status_code", None) or 502
    return jsonify({"success": True, "provider": "packlink", "result": result})
