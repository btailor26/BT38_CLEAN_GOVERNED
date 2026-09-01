"""Governed FBM fulfilment workspace.

The BT38 database remains the single source of truth. FBM reads existing
MarketplaceOrder rows only; it does not run a second marketplace order import.
Shipping execution is isolated from inventory, Product Linking and MCF.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import MarketplaceOrder, Store
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
from services.fbm_packlink_callback import extract_packlink_tracking, reconcile_packlink_tracking_lifecycle
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
                else "Amazon Buy Shipping is marketplace-native and remains controlled by Amazon."
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
        "message": "Prime/SFP is locked to Amazon Buy Shipping." if is_prime else "Use a label bought outside BT38; enter carrier/service/tracking and BT38 will map and confirm it to the marketplace.",
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

    def purchase_authority_rank(shipment: FBMShipment) -> int:
        provider = str(getattr(shipment, "provider", "") or "").strip().lower()
        if provider in {"", "marketplace"}:
            return 0
        purchase_status = str(getattr(shipment, "purchase_status", "") or "").strip().lower()
        purchase_key = str(getattr(shipment, "purchase_key", "") or "").strip().lower()
        tracking_number = str(getattr(shipment, "tracking_number", "") or "").strip()
        if getattr(shipment, "label_purchased_at", None) is not None:
            return 3
        if purchase_status == "purchased":
            return 2
        if provider == "packlink" and purchase_key.startswith("packlink_") and tracking_number:
            return 1
        return 0

    result = {}
    for shipment in shipments:
        key = (shipment.store_id, shipment.marketplace_order_id)
        if key not in keys:
            continue
        current = result.get(key)
        if current is None or purchase_authority_rank(shipment) > purchase_authority_rank(current):
            result[key] = shipment
    return result


def _get_fbm_order(order_id: int) -> MarketplaceOrder | None:
    row = db.session.get(MarketplaceOrder, order_id)
    return row if row is not None and _is_fbm_eligible(row) else None


def _find_rate(quote: FBMRateQuote, rate_id: str) -> dict | None:
    return next((rate for rate in (quote.rates or []) if isinstance(rate, dict) and str(rate.get("rate_id") or rate.get("id") or rate.get("service_id") or "") == str(rate_id)), None)


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


def _report_text(value) -> str:
    return str(value or "").strip()


def _amazon_report_delivery(row: dict) -> dict[str, str]:
    address_parts = [
        _report_text(row.get("ship-address-1")),
        _report_text(row.get("ship-address-2")),
        _report_text(row.get("ship-address-3")),
    ]
    return {
        "ship_to_name": _report_text(row.get("recipient-name")) or _report_text(row.get("buyer-name")),
        "ship_to_address": ", ".join(part for part in address_parts if part),
        "ship_to_city": _report_text(row.get("ship-city")),
        "ship_to_postcode": _report_text(row.get("ship-postal-code")),
        "ship_to_country": _report_text(row.get("ship-country")).upper()[:2],
        "ship_to_email": _report_text(row.get("buyer-email")),
        "ship_to_phone": _report_text(row.get("buyer-phone-number")),
    }


@governed_fbm_bp.get("/fbm/amazon-unshipped-report")
@login_required
def amazon_unshipped_report_page():
    return render_template("fbm_amazon_report.html")


@governed_fbm_bp.post("/fbm/amazon-unshipped-report")
@login_required
def amazon_unshipped_report_upload():
    uploaded = request.files.get("report")
    if uploaded is None or not uploaded.filename:
        return jsonify({"success": False, "message": "Choose an Amazon Unshipped Orders Report .txt file."}), 400

    raw = uploaded.stream.read((5 * 1024 * 1024) + 1)
    if len(raw) > 5 * 1024 * 1024:
        return jsonify({"success": False, "message": "Report is larger than the 5 MB safety limit."}), 413

    try:
        text_value = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"success": False, "message": "Amazon report must be UTF-8 text."}), 400

    reader = csv.DictReader(io.StringIO(text_value), delimiter="\t")
    headers = set(reader.fieldnames or [])
    required = {
        "order-id",
        "recipient-name",
        "ship-address-1",
        "ship-city",
        "ship-postal-code",
        "ship-country",
    }
    missing_headers = sorted(required - headers)
    if missing_headers:
        return jsonify({
            "success": False,
            "message": "This is not the expected Amazon FBM Unshipped Orders Report format.",
            "missing_columns": missing_headers,
        }), 400

    report_orders: dict[str, dict[str, str]] = {}
    report_rows = 0
    for report_row in reader:
        report_rows += 1
        order_id = _report_text(report_row.get("order-id"))
        if not order_id:
            continue
        delivery = _amazon_report_delivery(report_row)
        current = report_orders.setdefault(order_id, {})
        for field, value in delivery.items():
            if value and not current.get(field):
                current[field] = value

    if not report_orders:
        return jsonify({"success": False, "message": "No Amazon order IDs were found in the uploaded report."}), 400

    order_ids = sorted(report_orders)
    existing_rows = (
        MarketplaceOrder.query
        .join(Store, Store.id == MarketplaceOrder.store_id)
        .filter(MarketplaceOrder.marketplace_order_id.in_(order_ids))
        .filter(Store.platform.ilike("%amazon%"))
        .order_by(MarketplaceOrder.id)
        .all()
    )

    rows_by_order: dict[str, list[MarketplaceOrder]] = {}
    for order in existing_rows:
        rows_by_order.setdefault(str(order.marketplace_order_id), []).append(order)

    fields_filled = 0
    order_lines_updated = 0
    orders_unchanged = 0
    matched_order_ids = set(rows_by_order)
    now = datetime.utcnow()

    for order_id, lines in rows_by_order.items():
        delivery = report_orders.get(order_id) or {}
        order_changed = False
        for line in lines:
            line_changed = False
            for field in (
                "ship_to_name",
                "ship_to_address",
                "ship_to_city",
                "ship_to_postcode",
                "ship_to_country",
                "ship_to_email",
                "ship_to_phone",
            ):
                existing_value = _report_text(getattr(line, field, None))
                incoming_value = _report_text(delivery.get(field))
                if not existing_value and incoming_value:
                    setattr(line, field, incoming_value)
                    fields_filled += 1
                    line_changed = True
            if line_changed:
                line.updated_at = now
                order_lines_updated += 1
                order_changed = True
        if not order_changed:
            orders_unchanged += 1

    if fields_filled:
        db.session.commit()

    orders_not_found = len(set(order_ids) - matched_order_ids)
    return jsonify({
        "success": True,
        "report_rows": report_rows,
        "report_orders": len(report_orders),
        "orders_matched": len(matched_order_ids),
        "order_lines_updated": order_lines_updated,
        "fields_filled": fields_filled,
        "orders_unchanged": orders_unchanged,
        "orders_not_found": orders_not_found,
        "created_orders": 0,
        "overwritten_fields": 0,
        "stock_touched": False,
        "status_touched": False,
        "message": "Amazon FBM report applied to missing delivery fields only. Existing values were preserved and no orders were created.",
    })


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
        orders.append({"order": row, "platform": platform, "store_name": _store_name(row), "route_state": route_state, "shipping_mode": _marketplace_shipping_mode(row, platform, profile), "shipment": shipment, "shipment_state": shipment_state, "case": case, "profile": profile, "mapping_review": mapping_review})
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
        result.append({"id": row.id, "marketplace_order_id": row.marketplace_order_id, "platform": _platform(row), "store_name": _store_name(row), "sku": getattr(row, "sku", None), "quantity": sum(max(1, int(getattr(line, "quantity", 1) or 1)) for line in order_lines(row)), "postcode": getattr(row, "ship_to_postcode", None), "route_state": _route_state(row), "is_prime": profile.is_prime if profile else None, "prime_profile_error": profile_error, "parcel": parcel.to_dict(), "providers": _shipping_provider_options(row, profile, profile_error)})
    if not result:
        return jsonify({"success": False, "message": "No selected orders are eligible for FBM shipping."}), 404
    return jsonify({"success": True, "orders": result, "selected_count": len(result), "printing": {"mode": "qz_tray", "auto_print_after_purchase": True, "printer_preference_required": True, "fallback": "download_label"}, "message": "Shipping routes and DB parcel defaults prepared."})


@governed_fbm_bp.post("/fbm/orders/<int:order_id>/packlink/rates")
@login_required
def packlink_rates(order_id: int):
    order = _get_fbm_order(order_id)
    if order is None:
        return jsonify({"success": False, "message": "FBM order not found."}), 404
    if _platform(order).strip().lower() == "amazon":
        profile, error = _amazon_profile(order)
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
    quote = FBMRateQuote(store_id=order.store_id, marketplace_order_id=order.marketplace_order_id, provider="packlink", parcel=parcel, rates=rates, expires_at=datetime.utcnow() + timedelta(minutes=15))
    db.session.add(quote)
    db.session.commit()
    return jsonify({"success": True, "provider": "packlink", "order_id": order.id, "marketplace_order_id": order.marketplace_order_id, "quote_id": quote.id, "expires_at": quote.expires_at.isoformat(), "rates": rates, "parcel": parcel, "payment_mode": "packlink_checkout_required"})


@governed_fbm_bp.post("/fbm/orders/<int:order_id>/packlink/draft")
@login_required
def packlink_create_draft(order_id: int):
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

    completed = (
        FBMShipment.query
        .filter_by(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
            provider="packlink",
        )
        .filter(FBMShipment.tracking_number.isnot(None))
        .filter(FBMShipment.tracking_number != "")
        .order_by(FBMShipment.id.desc())
        .first()
    )
    purpose = str(body.get("shipment_purpose") or "").strip().lower()
    if completed is not None:
        if purpose not in {"return", "replacement"}:
            return jsonify({
                "success": False,
                "requires_shipment_purpose": True,
                "completed_shipment_id": completed.id,
                "tracking_number": completed.tracking_number,
                "options": ["return", "replacement"],
                "message": "Tracking already exists for this order. Confirm whether the new Packlink label is for a RETURN or a REPLACEMENT.",
            }), 409
        required_confirmation = f"CONFIRM_{purpose.upper()}"
        if body.get("confirm_additional_shipment") != required_confirmation:
            return jsonify({
                "success": False,
                "requires_shipment_purpose": True,
                "shipment_purpose": purpose,
                "message": f"Explicit {required_confirmation} confirmation is required for another label on this completed order.",
            }), 400
        purchase_key = (
            f"packlink_{purpose}:{order.store_id}:{order.marketplace_order_id}:"
            f"{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        )
        shipment = FBMShipment(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
            provider="packlink",
            provider_service_id=str(selected.get("service_id") or selected.get("id") or "") or None,
            carrier=str(selected.get("carrier_name") or selected.get("carrier") or "").strip() or None,
            service=str(selected.get("service_name") or selected.get("service") or "").strip() or None,
            purchase_key=purchase_key,
            selected_rate_id=rate_id,
            purchase_status=f"{purpose}_draft_creating",
            status="awaiting_provider_payment",
        )
        db.session.add(shipment)
    else:
        purchase_key = f"packlink_draft:{order.store_id}:{order.marketplace_order_id}"
        shipment = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
        if shipment is None:
            shipment = FBMShipment(
                store_id=order.store_id,
                marketplace_order_id=order.marketplace_order_id,
                provider="packlink",
                purchase_key=purchase_key,
            )
            db.session.add(shipment)
        shipment.provider_shipment_id = None
        shipment.provider_service_id = str(selected.get("service_id") or selected.get("id") or "") or None
        shipment.carrier = str(selected.get("carrier_name") or selected.get("carrier") or "").strip() or None
        shipment.service = str(selected.get("service_name") or selected.get("service") or "").strip() or None
        shipment.selected_rate_id = rate_id
        shipment.purchase_status = "draft_creating"
        shipment.purchase_error = None
        shipment.status = "awaiting_provider_payment"

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "Packlink shipment state changed while the draft was being prepared. Get fresh rates and try again."}), 409

    try:
        draft = PacklinkAdapter().create_shipment_draft(order=order, parcel=quote.parcel or {}, rate=selected)
    except Exception as exc:
        shipment.purchase_status = "draft_verification_required"
        shipment.purchase_error = str(exc)
        shipment.status = "draft_verification_required"
        db.session.commit()
        return jsonify({
            "success": False,
            "message": "Packlink did not return a confirmed draft reference. No tracking exists, so this order remains eligible for another explicit draft attempt.",
            "detail": str(exc),
            "shipment_id": shipment.id,
            "retry_allowed": True,
        }), 502
    shipment.provider_shipment_id = draft["reference"]
    shipment.purchase_status = (
        f"{purpose}_pending_provider_payment" if completed is not None else "pending_provider_payment"
    )
    shipment.purchase_error = None
    shipment.status = "awaiting_provider_payment"
    quote.used_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        "success": True,
        "shipment_id": shipment.id,
        "provider_reference": shipment.provider_shipment_id,
        "payment_status": shipment.purchase_status,
        "label_ready": False,
        "shipment_purpose": purpose or "original",
        "message": "Packlink draft created. The order remains open until tracking is provided.",
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
    observed_at = datetime.utcnow()
    reconcile_packlink_tracking_lifecycle(
        shipment,
        provider_state=provider_state,
        tracking_history=tracking_history,
        observed_at=observed_at,
    )
    blockers = adapter.draft_blockers(provider_payload)
    normalized_state = provider_state.upper().replace(" ", "_").replace("-", "_")
    ready_to_ship = bool(provider_state) and not blockers and normalized_state not in {"AWAITING_COMPLETION", "INCOMPLETE", "DRAFT", "DRAFT_INCOMPLETE"}
    blocking_reason = blockers[0]["label"] if blockers else None
    carrier = str(provider_payload.get("carrier") or shipment.carrier or "").strip() or None
    service = str(provider_payload.get("service") or shipment.service or "").strip() or None
    tracking = extract_packlink_tracking(provider_payload, tracking_history, shipment.tracking_number)
    if tracking:
        shipment.tracking_number = tracking
    if labels:
        first_label = labels[0]
        label_url = first_label if isinstance(first_label, str) else (first_label.get("url") if isinstance(first_label, dict) else None)
        result = persist_external_label(shipment=shipment, marketplace=_platform(order), provider="packlink", provider_shipment_id=shipment.provider_shipment_id, carrier=carrier, service=service, tracking_number=tracking, provider_service_id=str(provider_payload.get("service_id") or shipment.provider_service_id or "") or None, label={"type": "LABEL", "format": "PDF", "url": label_url, "storage_ref": shipment.provider_shipment_id})
        return jsonify({"success": True, "payment_complete": True, "label_ready": True, "ready_to_ship": True, "blockers": [], "blocking_reason": None, "label": {"format": "PDF", "url": label_url}, "provider_status": shipment.last_provider_status, "tracking": tracking, "tracking_history": tracking_history, **result})
    db.session.commit()
    return jsonify({
        "success": True,
        "payment_complete": False,
        "label_ready": False,
        "shipment_id": shipment.id,
        "provider_reference": shipment.provider_shipment_id,
        "provider_status": shipment.last_provider_status,
        "payment_status": shipment.purchase_status,
        "ready_to_ship": ready_to_ship,
        "blockers": blockers,
        "blocking_reason": blocking_reason,
        "tracking": tracking,
        "tracking_history": tracking_history,
        "message": "Packlink draft status read from the provider.",
    })


@governed_fbm_bp.post("/fbm/shipments/<int:shipment_id>/packlink/save")
@login_required
def packlink_save_draft(shipment_id: int):
    shipment = db.session.get(FBMShipment, shipment_id)
    if shipment is None or shipment.provider != "packlink" or not shipment.provider_shipment_id:
        return jsonify({"success": False, "message": "Packlink shipment not found."}), 404
    body = request.get_json(silent=True) or {}
    if body.get("confirm_save") != "SAVE_PACKLINK_DRAFT":
        return jsonify({"success": False, "message": "Explicit SAVE_PACKLINK_DRAFT confirmation is required."}), 400
    order = MarketplaceOrder.query.filter_by(store_id=shipment.store_id, marketplace_order_id=shipment.marketplace_order_id).first()
    if order is None:
        return jsonify({"success": False, "message": "Marketplace order for this shipment is missing."}), 404
    adapter = PacklinkAdapter()
    try:
        saved = adapter.save_shipment_draft(shipment.provider_shipment_id)
    except (PacklinkConfigurationError, PacklinkRequestError) as exc:
        return jsonify({"success": False, "message": str(exc)}), getattr(exc, "status_code", None) or 502
    shipment.last_provider_status = saved.get("provider_status") or shipment.last_provider_status
    shipment.last_provider_checked_at = datetime.utcnow()
    if saved.get("ready_to_ship") is True:
        shipment.purchase_status = "ready_to_ship"
        shipment.purchase_error = None
        shipment.status = "awaiting_provider_payment"
    else:
        shipment.purchase_status = "draft_requires_save"
        shipment.purchase_error = saved.get("blocking_reason") or saved.get("provider_status") or "Packlink draft still requires completion"
        shipment.status = "awaiting_provider_payment"
    db.session.commit()
    return jsonify({
        "success": True,
        "shipment_id": shipment.id,
        "provider_reference": shipment.provider_shipment_id,
        "same_reference": True,
        "save_applied": True,
        "provider_status": saved.get("provider_status"),
        "ready_to_ship": bool(saved.get("ready_to_ship")),
        "blockers": saved.get("blockers") or [],
        "blocking_reason": saved.get("blocking_reason"),
        "payment_status": shipment.purchase_status,
        "message": "Existing Packlink draft saved back to the same provider reference.",
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
    missing = [field for field in ("weight_kg", "length_cm", "width_cm", "height_cm") if not getattr(resolved, field)]
    if missing:
        return jsonify({"success": False, "message": "Parcel data is incomplete.", "missing": missing, "parcel": resolved.to_dict(), "is_prime": profile.is_prime}), 422
    try:
        result = AmazonShippingAdapter(order.store).get_rates(order=order, parcel=resolved.to_dict())
    except AmazonShippingError as exc:
        return jsonify({"success": False, "message": str(exc), "is_prime": profile.is_prime}), 502
    if not result.request_token:
        return jsonify({"success": False, "message": "Amazon returned rates without a purchase token."}), 502
    quote = FBMRateQuote(store_id=order.store_id, marketplace_order_id=order.marketplace_order_id, provider="amazon_buy_shipping", request_token=result.request_token, parcel=resolved.to_dict(), rates=result.rates, ineligible_rates=result.ineligible_rates, expires_at=datetime.utcnow() + timedelta(minutes=9))
    db.session.add(quote)
    db.session.commit()
    return jsonify({"success": True, "provider": "amazon_buy_shipping", "order_id": order.id, "marketplace_order_id": order.marketplace_order_id, "is_prime": profile.is_prime, "prime_locked": profile.is_prime is True, "quote_id": quote.id, "expires_at": quote.expires_at.isoformat(), "rates": result.rates, "ineligible_rates": result.ineligible_rates, "parcel": resolved.to_dict()})


@governed_fbm_bp.post("/fbm/orders/<int:order_id>/amazon/purchase")
@login_required
def amazon_purchase(order_id: int):
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
            return jsonify({"success": True, "already_purchased": True, "shipment_id": existing.id, "provider_shipment_id": existing.provider_shipment_id, "tracking_number": existing.tracking_number, "carrier": existing.carrier, "service": existing.service, "label": None, "message": "Postage was already purchased for this order. No second charge was made."})
        return jsonify({"success": False, "message": "A previous purchase attempt exists for this order and must be verified before any retry.", "purchase_status": existing.purchase_status, "shipment_id": existing.id}), 409
    shipment = FBMShipment(store_id=order.store_id, marketplace_order_id=order.marketplace_order_id, provider="amazon_buy_shipping", provider_carrier_id=str(selected.get("carrier_id") or "") or None, provider_service_id=str(selected.get("service_id") or "") or None, carrier=carrier_name or None, service=str(selected.get("service_name") or "").strip() or None, purchase_key=purchase_key, selected_rate_id=rate_id, purchase_status="pending", status="awaiting_label")
    db.session.add(shipment)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
        return jsonify({"success": False, "message": "A purchase for this order is already in progress or completed.", "shipment_id": existing.id if existing else None}), 409
    try:
        purchase = AmazonShippingAdapter(order.store).purchase_shipment(request_token=quote.request_token, rate_id=rate_id, requested_document_specification=document_spec, requested_value_added_services=body.get("requested_value_added_services") or None, additional_inputs=body.get("additional_inputs") or None)
    except Exception as exc:
        shipment.purchase_status = "verification_required"
        shipment.purchase_error = str(exc)
        shipment.status = "purchase_verification_required"
        db.session.commit()
        return jsonify({"success": False, "message": "Amazon purchase did not return a confirmed success. BT38 has blocked automatic retry to prevent a duplicate postage charge.", "detail": str(exc), "shipment_id": shipment.id}), 502
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
        printable_label = {"format": shipment.label_format, "base64": label.get("contents"), "width": shipment.label_width, "height": shipment.label_length, "units": shipment.label_size_unit, "dpi": shipment.label_dpi}
    return jsonify({"success": True, "already_purchased": False, "shipment_id": shipment.id, "provider_shipment_id": shipment.provider_shipment_id, "tracking_number": shipment.tracking_number, "carrier": shipment.carrier, "service": shipment.service, "label": printable_label, "mapping_status": "marketplace_native", "marketplace_confirmation": shipment.marketplace_confirmation_status, "message": "Amazon Buy Shipping purchase succeeded and was persisted before printing. Amazon manages the on-Amazon shipment confirmation for this marketplace-native label."})


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
    return jsonify({"success": True, "shipment_id": shipment.id, "tracking_number": shipment.tracking_number, "carrier": shipment.carrier, "service": shipment.service, "provider_status": summary_status, "state": shipment_confirmation_state(shipment), "tracking": payload})


@governed_fbm_bp.post("/fbm/orders/<int:order_id>/manual/dispatch")
@login_required
def manual_dispatch(order_id: int):
    """Record postage bought outside BT38 and confirm through the shared mapping path."""
    order = _get_fbm_order(order_id)
    if order is None:
        return jsonify({"success": False, "message": "FBM order not found."}), 404
    if _platform(order).strip().lower() == "amazon":
        profile, _ = _amazon_profile(order)
        if profile is not None and profile.is_prime is True:
            return jsonify({"success": False, "message": "Prime/SFP orders must use Amazon Buy Shipping."}), 409
    body = request.get_json(silent=True) or {}
    carrier = str(body.get("carrier") or "").strip()
    service = str(body.get("service") or "").strip() or None
    tracking = str(body.get("tracking_number") or "").strip()
    if not carrier or not tracking:
        return jsonify({"success": False, "message": "Carrier and tracking number are required."}), 400
    if body.get("confirm_dispatch") != "CONFIRM_MANUAL_DISPATCH":
        return jsonify({"success": False, "message": "Explicit manual dispatch confirmation is required."}), 400
    purchase_key = f"manual:{order.store_id}:{order.marketplace_order_id}"
    shipment = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
    if shipment is None:
        shipment = FBMShipment(store_id=order.store_id, marketplace_order_id=order.marketplace_order_id, provider="manual", purchase_key=purchase_key, purchase_status="recording", status="awaiting_carrier_acceptance")
        db.session.add(shipment)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            shipment = FBMShipment.query.filter_by(purchase_key=purchase_key).first()
    if shipment is None:
        return jsonify({"success": False, "message": "Manual shipment could not be created safely."}), 409
    if shipment.marketplace_confirmed_at:
        return jsonify({"success": True, "already_confirmed": True, "shipment_id": shipment.id, "tracking_number": shipment.tracking_number, "message": "This shipment has already been confirmed to the marketplace."})
    result = persist_external_label(shipment=shipment, marketplace=_platform(order), provider="manual", provider_shipment_id=tracking, carrier=carrier, service=service, tracking_number=tracking, label={})
    return jsonify({"success": True, "manual": True, **result, "print_allowed": False})


@governed_fbm_bp.get("/fbm/mappings/pending")
@login_required
def pending_carrier_mappings():
    mappings = FBMCarrierServiceMapping.query.filter_by(verification_status="pending_review").order_by(FBMCarrierServiceMapping.created_at.asc()).limit(100).all()
    return jsonify({"success": True, "count": len(mappings), "mappings": [mapping_payload(mapping) for mapping in mappings]})


@governed_fbm_bp.post("/fbm/mappings/<int:mapping_id>/verify")
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
    try:
        mapping = verify_mapping(
            mapping=mapping,
            marketplace_carrier_code=marketplace_carrier_code,
            marketplace_carrier_name=body.get("marketplace_carrier_name"),
            marketplace_service_code=marketplace_service_code,
            marketplace_service_name=body.get("marketplace_service_name"),
            verified_by=verified_by,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    payload = mapping_payload(mapping)
    release = (payload or {}).get("release_summary") or {"attempted": 0, "confirmed": 0, "failed": 0, "results": []}
    if release.get("failed"):
        return jsonify({
            "success": False,
            "mapping_saved": True,
            "mapping": payload,
            "release_summary": release,
            "message": mapping.last_error or "Mapping was saved, but one or more waiting shipments could not be confirmed to the marketplace.",
        })
    return jsonify({
        "success": True,
        "mapping_saved": True,
        "mapping": payload,
        "release_summary": release,
        "message": "Carrier/service mapping verified and saved. Waiting shipments were released successfully.",
    })


@governed_fbm_bp.get("/fbm/providers/packlink/connection")
@governed_fbm_bp.get("/fbm/packlink/connection")
@login_required
def packlink_connection():
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
