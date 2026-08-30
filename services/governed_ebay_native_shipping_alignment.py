"""Align the existing FBM eBay Shipping slot to eBay's native Logistics API.

This module does not create a second FBM workflow. It reuses the existing
MarketplaceOrder, FBMRateQuote, FBMShipment and QZ label-printing contracts.
The Seller Hub handoff remains superseded by the in-BT38 provider action.

The eBay Logistics API is Limited Release. When the connected application or
seller authorization does not have access, the existing button stays visible
but the action returns an explicit capability/reauthorization message. No
fallback browser automation or duplicate marketplace write is attempted.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta
from typing import Any

import requests
from flask import Response, jsonify, request
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from extensions import db
from fbm_models import FBMRateQuote, FBMShipment
from models import MarketplaceOrder
from services.fbm_order_mapper import (
    apply_parcel_overrides,
    order_lines,
    parcel_from_db,
    ship_from,
    ship_to,
)


EBAY_LOGISTICS_BASE_URL = "https://api.ebay.com/sell/logistics/v1_beta"
EBAY_LOGISTICS_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.logistics"
EBAY_MARKETPLACE_ID = "EBAY_GB"
EBAY_TIMEOUT_SECONDS = 15


class EbayNativeShippingError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        authorization_required: bool = False,
        limited_release_required: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.authorization_required = authorization_required
        self.limited_release_required = limited_release_required


def _platform(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "platform", "") or "").strip().lower()


def _get_ebay_order(order_id: int) -> MarketplaceOrder | None:
    row = db.session.get(MarketplaceOrder, order_id)
    if row is None or _platform(row) != "ebay":
        return None
    fulfillment = str(getattr(row, "fulfillment_type", "") or "").upper()
    status = str(getattr(row, "status", "") or "").lower()
    if fulfillment in {"FBA", "AFN", "MCF"} or status.startswith("mcf_"):
        return None
    return row


def _raw_credentials(store: Any) -> dict[str, Any]:
    raw = getattr(store, "api_key", None)
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
            return dict(payload) if isinstance(payload, dict) else {}
        except Exception:
            return {}
    return {}


def _expires_soon(value: Any) -> bool:
    if not value:
        return True
    try:
        expires_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if expires_at.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=None)
        return expires_at <= datetime.utcnow() + timedelta(minutes=10)
    except Exception:
        return True


def _access_token(store: Any) -> str:
    creds = _raw_credentials(store)
    token = str(
        creds.get("access_token")
        or creds.get("oauth_token")
        or creds.get("token")
        or ""
    ).strip()
    if token and not _expires_soon(creds.get("access_token_expires_at")):
        return token

    refresh_token = str(creds.get("refresh_token") or "").strip()
    client_id = str(os.getenv("EBAY_CLIENT_ID") or creds.get("app_id") or creds.get("client_id") or "").strip()
    client_secret = str(os.getenv("EBAY_CLIENT_SECRET") or creds.get("cert_id") or creds.get("client_secret") or "").strip()
    if not refresh_token or not client_id or not client_secret:
        if token:
            return token
        raise EbayNativeShippingError(
            "eBay OAuth refresh credentials are missing for native shipping.",
            authorization_required=True,
        )

    # Omit scope on refresh so eBay preserves the exact scopes granted by the
    # seller's consent instead of attempting to escalate permissions silently.
    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        auth=(client_id, client_secret),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=EBAY_TIMEOUT_SECONDS,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code >= 300 or not payload.get("access_token"):
        raise EbayNativeShippingError(
            "eBay could not refresh the seller token for native shipping.",
            status_code=response.status_code,
            authorization_required=response.status_code in {400, 401, 403},
        )

    now = datetime.utcnow()
    creds["access_token"] = payload.get("access_token")
    creds["token_type"] = payload.get("token_type")
    creds["access_token_expires_at"] = (
        now + timedelta(seconds=int(payload.get("expires_in", 7200)))
    ).isoformat()
    if payload.get("scope"):
        creds["oauth_granted_scope"] = payload.get("scope")
    creds["refreshed_at"] = now.isoformat()
    store.api_key = json.dumps(creds)
    db.session.commit()
    return str(payload["access_token"])


def _headers(token: str, *, accept: str = "application/json") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "Content-Type": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": EBAY_MARKETPLACE_ID,
        "User-Agent": "BT38-FBM/1.0",
    }


def _provider_error(response: requests.Response, default: str) -> EbayNativeShippingError:
    message = default
    try:
        payload = response.json()
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list) and errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            message = str(first.get("longMessage") or first.get("message") or message)
        elif isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("error") or message)
    except Exception:
        if response.text:
            message = response.text[:500]
    auth = response.status_code in {401, 403}
    return EbayNativeShippingError(
        message,
        status_code=response.status_code,
        authorization_required=auth,
        limited_release_required=response.status_code == 403,
    )


def _contact(values: dict[str, Any]) -> dict[str, Any]:
    address = {
        "addressLine1": str(values.get("address1") or "").strip(),
        "city": str(values.get("city") or "").strip(),
        "countryCode": str(values.get("country") or "GB").strip().upper(),
        "postalCode": str(values.get("postcode") or "").strip(),
    }
    if values.get("address2"):
        address["addressLine2"] = str(values["address2"]).strip()
    if values.get("region"):
        address["stateOrProvince"] = str(values["region"]).strip()

    result: dict[str, Any] = {
        "fullName": str(values.get("name") or "").strip(),
        "contactAddress": address,
    }
    if values.get("company"):
        result["companyName"] = str(values["company"]).strip()
    if values.get("email"):
        result["email"] = str(values["email"]).strip()
    if values.get("phone"):
        result["primaryPhone"] = {"phoneNumber": str(values["phone"]).strip()}
    return result


def _quote_payload(order: MarketplaceOrder, parcel: dict[str, Any]) -> dict[str, Any]:
    origin = ship_from()
    destination = ship_to(order)
    missing_destination = [
        field for field in ("name", "address1", "city", "postcode", "country")
        if not destination.get(field)
    ]
    if missing_destination:
        raise EbayNativeShippingError(
            "eBay shipping destination is incomplete: " + ", ".join(missing_destination)
        )
    missing_parcel = [
        field for field in ("weight_kg", "length_cm", "width_cm", "height_cm")
        if not parcel.get(field)
    ]
    if missing_parcel:
        raise EbayNativeShippingError(
            "Parcel data is incomplete: " + ", ".join(missing_parcel)
        )

    return {
        "orders": [{"orderId": str(order.marketplace_order_id)}],
        "packageSpecification": {
            "dimensions": {
                "height": str(parcel["height_cm"]),
                "length": str(parcel["length_cm"]),
                "unit": "CENTIMETER",
                "width": str(parcel["width_cm"]),
            },
            "weight": {
                "unit": "KILOGRAM",
                "value": str(parcel["weight_kg"]),
            },
        },
        "shipFrom": _contact(origin),
        "shipTo": _contact(destination),
    }


def _normalise_rate(rate: dict[str, Any]) -> dict[str, Any]:
    price = rate.get("totalShippingCost") or rate.get("baseShippingCost") or rate.get("price")
    return {
        **rate,
        "rate_id": str(rate.get("rateId") or rate.get("rate_id") or ""),
        "carrier_name": str(rate.get("shippingCarrierName") or rate.get("shippingCarrierCode") or ""),
        "service_name": str(rate.get("shippingServiceName") or rate.get("shippingServiceCode") or ""),
        "carrier_id": str(rate.get("shippingCarrierCode") or ""),
        "service_id": str(rate.get("shippingServiceCode") or ""),
        "price": price,
    }


def _create_quote(order: MarketplaceOrder, parcel: dict[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    token = _access_token(order.store)
    response = requests.post(
        f"{EBAY_LOGISTICS_BASE_URL}/shipping_quote",
        headers=_headers(token),
        json=_quote_payload(order, parcel),
        timeout=EBAY_TIMEOUT_SECONDS,
    )
    if response.status_code != 201:
        raise _provider_error(response, "eBay could not return native shipping rates.")
    payload = response.json() if response.content else {}
    quote_id = str(payload.get("shippingQuoteId") or "").strip()
    rates = [
        _normalise_rate(rate)
        for rate in (payload.get("rates") or [])
        if isinstance(rate, dict)
    ]
    if not quote_id:
        raise EbayNativeShippingError("eBay returned shipping rates without a shippingQuoteId.")
    return quote_id, rates, payload


def _create_shipment(order: MarketplaceOrder, quote_id: str, rate_id: str) -> dict[str, Any]:
    token = _access_token(order.store)
    response = requests.post(
        f"{EBAY_LOGISTICS_BASE_URL}/shipment/create_from_shipping_quote",
        headers=_headers(token),
        json={"shippingQuoteId": quote_id, "rateId": rate_id, "labelSize": '4"x6"'},
        timeout=EBAY_TIMEOUT_SECONDS,
    )
    if response.status_code != 201:
        raise _provider_error(response, "eBay did not create the native shipment.")
    payload = response.json() if response.content else {}
    if not payload.get("shipmentId"):
        raise EbayNativeShippingError("eBay created no shipment identifier; automatic retry is blocked.")
    return payload


def _download_label(order: MarketplaceOrder, provider_shipment_id: str) -> bytes:
    token = _access_token(order.store)
    response = requests.get(
        f"{EBAY_LOGISTICS_BASE_URL}/shipment/{provider_shipment_id}/download_label_file",
        headers=_headers(token, accept="application/pdf"),
        timeout=EBAY_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise _provider_error(response, "eBay label download failed.")
    return bytes(response.content or b"")


def _money_value(value: Any) -> float | None:
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


def _error_response(exc: EbayNativeShippingError):
    status = 403 if exc.authorization_required or exc.limited_release_required else (exc.status_code or 502)
    return jsonify({
        "success": False,
        "message": str(exc),
        "authorization_required": bool(exc.authorization_required),
        "limited_release_required": bool(exc.limited_release_required),
        "required_scope": EBAY_LOGISTICS_SCOPE,
        "seller_hub_fallback": False,
    }), status


def install_governed_ebay_native_shipping_alignment(app) -> None:
    """Install only the missing native eBay quote/purchase actions."""
    if getattr(app, "_bt38_ebay_native_shipping_alignment_installed", False):
        return

    import services.governed_fbm_page_alignment as page_alignment

    original_mode = page_alignment._workspace_shipping_mode
    original_options = page_alignment._workspace_provider_options

    def aligned_mode(row, platform, profile):
        mode = dict(original_mode(row, platform, profile))
        if str(platform or "").strip().lower() == "ebay":
            mode.update({
                "recommended": "eBay Shipping",
                "marketplace_buy_shipping": True,
                "external_provider": True,
                "manual": True,
                "prime_locked": False,
                "profile_known": True,
                "reason": "Use the existing eBay Shipping route in BT38 for native eBay rates and label purchase. Packlink/manual remain separate alternatives.",
            })
        return mode

    def aligned_options(row, profile):
        options = [dict(option) for option in original_options(row, profile)]
        if _platform(row) == "ebay":
            for option in options:
                if str(option.get("provider") or "") != "ebay_shipping":
                    continue
                option.update({
                    "available": True,
                    "recommended": True,
                    "label_formats": ["PDF"],
                    "auto_print_supported": True,
                    "requires_terms_acceptance": False,
                    "message": "Get eBay-native rates, buy the selected label in BT38 and print the returned PDF through the existing QZ path. If eBay has not enabled Logistics API access for this app/account, BT38 will stop before purchase and show that capability gate.",
                })
        return options

    page_alignment._workspace_shipping_mode = aligned_mode
    page_alignment._workspace_provider_options = aligned_options

    @login_required
    def ebay_rates(order_id: int):
        order = _get_ebay_order(order_id)
        if order is None:
            return jsonify({"success": False, "message": "eBay FBM order not found."}), 404
        body = request.get_json(silent=True) or {}
        resolved = apply_parcel_overrides(parcel_from_db(order), body.get("parcel") or {})
        parcel = resolved.to_dict()
        try:
            quote_id, rates, _ = _create_quote(order, parcel)
        except EbayNativeShippingError as exc:
            return _error_response(exc)
        quote = FBMRateQuote(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
            provider="ebay_shipping",
            request_token=quote_id,
            parcel=parcel,
            rates=rates,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        )
        db.session.add(quote)
        db.session.commit()
        return jsonify({
            "success": True,
            "provider": "ebay_shipping",
            "order_id": order.id,
            "marketplace_order_id": order.marketplace_order_id,
            "quote_id": quote.id,
            "shipping_quote_id": quote_id,
            "expires_at": quote.expires_at.isoformat(),
            "rates": rates,
            "parcel": parcel,
        })

    @login_required
    def ebay_purchase(order_id: int):
        order = _get_ebay_order(order_id)
        if order is None:
            return jsonify({"success": False, "message": "eBay FBM order not found."}), 404
        body = request.get_json(silent=True) or {}
        if body.get("confirm_purchase") != "BUY_POSTAGE":
            return jsonify({"success": False, "message": "Explicit BUY_POSTAGE confirmation is required."}), 400
        try:
            quote_id = int(body.get("quote_id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "message": "Valid quote_id is required."}), 400
        rate_id = str(body.get("rate_id") or "").strip()
        quote = db.session.get(FBMRateQuote, quote_id)
        if (
            quote is None
            or quote.provider != "ebay_shipping"
            or quote.store_id != order.store_id
            or quote.marketplace_order_id != order.marketplace_order_id
        ):
            return jsonify({"success": False, "message": "eBay rate quote does not belong to this order."}), 409
        if quote.used_at:
            return jsonify({"success": False, "message": "This eBay rate quote has already been used."}), 409
        if quote.expired:
            return jsonify({"success": False, "message": "eBay rate quote expired. Get fresh rates before buying."}), 409
        selected = next((rate for rate in (quote.rates or []) if str(rate.get("rate_id") or rate.get("rateId") or "") == rate_id), None)
        if selected is None:
            return jsonify({"success": False, "message": "Selected eBay rate is not in the stored quote."}), 409

        purchase_key = f"ebay_shipping:{order.store_id}:{order.marketplace_order_id}"
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
                    "label_url": f"/fbm/shipments/{existing.id}/ebay/label",
                    "message": "eBay postage was already purchased for this order. No second charge was made.",
                })
            return jsonify({
                "success": False,
                "message": "A previous eBay purchase attempt exists for this order and must be verified before any retry.",
                "purchase_status": existing.purchase_status,
                "shipment_id": existing.id,
            }), 409

        shipment = FBMShipment(
            store_id=order.store_id,
            marketplace_order_id=order.marketplace_order_id,
            provider="ebay_shipping",
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
            return jsonify({"success": False, "message": "An eBay postage purchase is already in progress for this order."}), 409

        try:
            purchase = _create_shipment(order, str(quote.request_token or ""), rate_id)
        except EbayNativeShippingError as exc:
            shipment.purchase_status = "verification_required"
            shipment.purchase_error = str(exc)
            shipment.status = "purchase_verification_required"
            db.session.commit()
            return _error_response(exc)
        except Exception as exc:
            shipment.purchase_status = "verification_required"
            shipment.purchase_error = str(exc)
            shipment.status = "purchase_verification_required"
            db.session.commit()
            return jsonify({
                "success": False,
                "message": "eBay purchase did not return a confirmed success. BT38 blocked automatic retry to prevent a duplicate postage charge.",
                "shipment_id": shipment.id,
            }), 502

        provider_shipment_id = str(purchase.get("shipmentId") or "").strip()
        tracking = str(purchase.get("shipmentTrackingNumber") or "").strip() or None
        purchased_rate = purchase.get("rate") if isinstance(purchase.get("rate"), dict) else {}
        carrier = str(
            purchased_rate.get("shippingCarrierName")
            or purchased_rate.get("shippingCarrierCode")
            or selected.get("carrier_name")
            or ""
        ).strip() or None
        service = str(
            purchased_rate.get("shippingServiceName")
            or purchased_rate.get("shippingServiceCode")
            or selected.get("service_name")
            or ""
        ).strip() or None
        now = datetime.utcnow()
        shipment.provider_shipment_id = provider_shipment_id
        shipment.provider_carrier_id = str(purchased_rate.get("shippingCarrierCode") or selected.get("carrier_id") or "").strip() or None
        shipment.provider_service_id = str(purchased_rate.get("shippingServiceCode") or selected.get("service_id") or "").strip() or None
        shipment.carrier = carrier
        shipment.service = service
        shipment.tracking_number = tracking
        shipment.label_format = "PDF"
        shipment.label_document_type = "LABEL"
        shipment.label_source = "ebay_shipping"
        shipment.label_storage_ref = provider_shipment_id
        shipment.purchase_status = "purchased"
        shipment.purchase_error = None
        shipment.status = "awaiting_carrier_acceptance" if tracking else "awaiting_tracking"
        shipment.label_purchased_at = now
        # Native eBay shipping owns its own marketplace shipment state. Do not
        # issue a second CompleteSale write for the same eBay-created label.
        shipment.marketplace_confirmed_at = now
        shipment.marketplace_confirmation_status = "ebay_shipping_managed_by_ebay"
        quote.used_at = now
        for line in order_lines(order):
            line.carrier = carrier
            if tracking:
                line.tracking_number = tracking
        shipping_cost = _money_value(purchased_rate.get("totalShippingCost") or selected.get("price"))
        if shipping_cost is not None:
            order.shipping_cost = shipping_cost
        db.session.commit()

        label = None
        label_error = None
        try:
            label_bytes = _download_label(order, provider_shipment_id)
            if label_bytes:
                label = {"format": "PDF", "base64": base64.b64encode(label_bytes).decode("ascii")}
        except EbayNativeShippingError as exc:
            label_error = str(exc)

        return jsonify({
            "success": True,
            "already_purchased": False,
            "shipment_id": shipment.id,
            "provider_shipment_id": provider_shipment_id,
            "tracking_number": tracking,
            "carrier": carrier,
            "service": service,
            "label": label,
            "label_url": f"/fbm/shipments/{shipment.id}/ebay/label",
            "label_error": label_error,
            "marketplace_confirmation": shipment.marketplace_confirmation_status,
            "message": "eBay native label purchase succeeded and was persisted before printing.",
        })

    @login_required
    def ebay_label(shipment_id: int):
        shipment = db.session.get(FBMShipment, shipment_id)
        if shipment is None or shipment.provider != "ebay_shipping" or not shipment.provider_shipment_id:
            return jsonify({"success": False, "message": "eBay shipping label not found."}), 404
        order = MarketplaceOrder.query.filter_by(
            store_id=shipment.store_id,
            marketplace_order_id=shipment.marketplace_order_id,
        ).order_by(MarketplaceOrder.id.asc()).first()
        if order is None:
            return jsonify({"success": False, "message": "Marketplace order for this label is missing."}), 404
        try:
            label_bytes = _download_label(order, shipment.provider_shipment_id)
        except EbayNativeShippingError as exc:
            return _error_response(exc)
        return Response(
            label_bytes,
            status=200,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'inline; filename="ebay-{shipment.marketplace_order_id}.pdf"'},
        )

    app.add_url_rule(
        "/fbm/orders/<int:order_id>/ebay/rates",
        endpoint="bt38_ebay_native_shipping_rates",
        view_func=ebay_rates,
        methods=["POST"],
    )
    app.add_url_rule(
        "/fbm/orders/<int:order_id>/ebay/purchase",
        endpoint="bt38_ebay_native_shipping_purchase",
        view_func=ebay_purchase,
        methods=["POST"],
    )
    app.add_url_rule(
        "/fbm/shipments/<int:shipment_id>/ebay/label",
        endpoint="bt38_ebay_native_shipping_label",
        view_func=ebay_label,
        methods=["GET"],
    )

    @app.after_request
    def inject_ebay_shipping_alignment_script(response):
        if request.path.rstrip("/") != "/fbm":
            return response
        if response.status_code != 200 or not response.mimetype.startswith("text/html"):
            return response
        html = response.get_data(as_text=True)
        marker = "</body>"
        script = '<script src="/static/js/fbm_ebay_shipping_alignment.js"></script>'
        if marker in html and script not in html:
            response.set_data(html.replace(marker, script + marker, 1))
            response.content_length = len(response.get_data())
        return response

    app._bt38_ebay_native_shipping_alignment_installed = True
    app.logger.info("BT38 native eBay shipping alignment installed on existing FBM path")
