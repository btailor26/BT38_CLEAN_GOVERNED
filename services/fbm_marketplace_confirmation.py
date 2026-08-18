"""Marketplace confirmation for FBM shipments purchased outside the marketplace.

Marketplace-native postage remains owned by that marketplace. In particular,
Amazon Buy Shipping must not be confirmed a second time by BT38. External
providers (Packlink/manual carrier) are confirmed only after the persistent
carrier/service mapping has been verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from extensions import db
from fbm_models import FBMCarrierServiceMapping, FBMShipment
from models import MarketplaceOrder
from amazon_service_live_patch import _marketplace_for_id, _sp_api_credentials
from services.fbm_order_mapper import order_lines


class FBMMarketplaceConfirmationError(RuntimeError):
    pass


def _platform(order: MarketplaceOrder) -> str:
    store = getattr(order, "store", None)
    return str(getattr(store, "platform", "") or "").strip().lower()


def _amazon_client(order: MarketplaceOrder):
    try:
        from sp_api.api import Orders
        from sp_api.base import Marketplaces
    except Exception as exc:
        raise FBMMarketplaceConfirmationError("Installed amazon-sp-api library does not expose Orders API.") from exc

    store = getattr(order, "store", None)
    creds = getattr(store, "amazon_credentials", None) if store is not None else None
    if not creds or not getattr(creds, "is_valid", lambda: False)():
        raise FBMMarketplaceConfirmationError("Amazon credentials are not configured for this store.")

    normalized = {
        "refresh_token": creds.refresh_token,
        "lwa_app_id": creds.lwa_app_id,
        "lwa_client_secret": creds.lwa_client_secret,
        "seller_id": creds.seller_id,
        "marketplace_id": creds.marketplace_id,
        "aws_access_key_id": getattr(creds, "aws_access_key_id", None),
        "aws_secret_access_key": getattr(creds, "aws_secret_access_key", None),
        "role_arn": getattr(creds, "aws_user_arn", None),
    }
    return (
        Orders(
            credentials=_sp_api_credentials(normalized),
            marketplace=_marketplace_for_id(creds.marketplace_id, Marketplaces),
        ),
        str(creds.marketplace_id),
    )


def _amazon_order_items(order: MarketplaceOrder) -> list[dict[str, Any]]:
    quantities: dict[str, int] = {}
    missing = []
    for line in order_lines(order):
        item_id = str(getattr(line, "marketplace_order_item_id", "") or "").strip()
        if not item_id:
            missing.append(getattr(line, "id", None))
            continue
        quantities[item_id] = quantities.get(item_id, 0) + max(1, int(getattr(line, "quantity", 1) or 1))
    if missing:
        raise FBMMarketplaceConfirmationError("Amazon order item ID is missing from one or more BT38 order lines.")
    if not quantities:
        raise FBMMarketplaceConfirmationError("Amazon order has no confirmable order items in BT38.")
    return [{"orderItemId": item_id, "quantity": quantity} for item_id, quantity in quantities.items()]


def _amazon_ship_date(shipment: FBMShipment) -> str:
    value = shipment.label_purchased_at or shipment.created_at or datetime.utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _confirm_ebay_external(
    *,
    order: MarketplaceOrder,
    shipment: FBMShipment,
    mapping: FBMCarrierServiceMapping,
    tracking: str,
) -> dict[str, Any]:
    """Use BT38's existing governed eBay CompleteSale implementation.

    Packlink/manual shipping is already a deliberate dispatch action by the time
    it reaches this function: the provider label/tracking has been persisted and
    the carrier/service mapping has been verified. Reuse the canonical eBay
    writer rather than introducing a second eBay shipping client.
    """
    from services.governed_ebay_dispatch import complete_sale

    carrier = str(
        mapping.marketplace_carrier_name
        or mapping.marketplace_carrier_code
        or shipment.carrier
        or "Other"
    ).strip() or "Other"

    result = complete_sale(
        order,
        carrier=carrier,
        tracking_number=tracking,
    )
    if not result.get("success"):
        raise FBMMarketplaceConfirmationError(
            str(result.get("error") or "eBay CompleteSale did not confirm the external shipment.")
        )
    return result


def confirm_external_shipment(
    *,
    shipment: FBMShipment,
    mapping: FBMCarrierServiceMapping,
) -> dict[str, Any]:
    """Confirm one externally fulfilled shipment after mapping verification.

    The call is idempotent at BT38 level: once ``marketplace_confirmed_at`` is
    stored, ordinary retries return the persisted result without another
    marketplace write. Amazon receives a stable packageReferenceId based on the
    BT38 shipment ID. eBay uses BT38's existing governed CompleteSale writer.
    """
    if str(shipment.provider or "").strip().lower() == "amazon_buy_shipping":
        shipment.marketplace_confirmation_status = "amazon_buy_shipping_managed_by_amazon"
        shipment.marketplace_confirmation_error = None
        db.session.commit()
        return {"success": True, "managed_by_marketplace": True, "already_confirmed": True}

    if mapping.verification_status != "verified" or not mapping.marketplace_carrier_code:
        shipment.marketplace_confirmation_status = "mapping_under_review"
        shipment.marketplace_confirmation_error = "Carrier/service mapping is not verified yet."
        db.session.commit()
        return {"success": False, "held": True, "reason": "mapping_under_review"}

    if shipment.marketplace_confirmed_at:
        return {
            "success": True,
            "already_confirmed": True,
            "confirmed_at": shipment.marketplace_confirmed_at.isoformat(),
        }

    tracking = str(shipment.tracking_number or "").strip()
    if not tracking:
        shipment.marketplace_confirmation_status = "tracking_required"
        shipment.marketplace_confirmation_error = "Tracking number is required before marketplace confirmation."
        db.session.commit()
        return {"success": False, "held": True, "reason": "tracking_required"}

    order = MarketplaceOrder.query.filter_by(
        store_id=shipment.store_id,
        marketplace_order_id=shipment.marketplace_order_id,
    ).order_by(MarketplaceOrder.id.asc()).first()
    if order is None:
        shipment.marketplace_confirmation_status = "order_missing"
        shipment.marketplace_confirmation_error = "Marketplace order is missing from BT38."
        db.session.commit()
        return {"success": False, "held": True, "reason": "order_missing"}

    platform = _platform(order)
    try:
        if platform == "amazon":
            client, marketplace_id = _amazon_client(order)
            amazon_items = _amazon_order_items(order)
            carrier_code = str(mapping.marketplace_carrier_code or "").strip()
            carrier_name = str(mapping.marketplace_carrier_name or shipment.carrier or carrier_code).strip()
            shipping_method = str(
                mapping.marketplace_service_name
                or mapping.marketplace_service_code
                or shipment.service
                or carrier_name
            ).strip()

            client.confirm_shipment(
                str(order.marketplace_order_id),
                marketplaceId=marketplace_id,
                codCollectionMethod="",
                packageDetail={
                    "packageReferenceId": int(shipment.id),
                    "carrierCode": carrier_code,
                    "carrierName": carrier_name,
                    "shippingMethod": shipping_method,
                    "trackingNumber": tracking,
                    "shipDate": _amazon_ship_date(shipment),
                    "orderItems": amazon_items,
                },
            )
        elif platform == "ebay":
            _confirm_ebay_external(
                order=order,
                shipment=shipment,
                mapping=mapping,
                tracking=tracking,
            )
        else:
            shipment.marketplace_confirmation_status = "marketplace_confirmation_not_implemented"
            shipment.marketplace_confirmation_error = (
                f"External confirmation is not implemented for {platform or 'this marketplace'} yet."
            )
            db.session.commit()
            return {"success": False, "held": True, "reason": "marketplace_confirmation_not_implemented"}
    except Exception as exc:
        shipment.marketplace_confirmation_status = "confirmation_failed"
        shipment.marketplace_confirmation_error = str(exc)
        db.session.commit()
        return {"success": False, "held": False, "reason": "confirmation_failed", "error": str(exc)}

    now = datetime.utcnow()
    shipment.marketplace_confirmed_at = now
    shipment.marketplace_confirmation_status = "confirmed"
    shipment.marketplace_confirmation_error = None
    carrier_for_db = (
        shipment.carrier
        or mapping.marketplace_carrier_name
        or mapping.marketplace_carrier_code
    )
    for line in order_lines(order):
        line.carrier = carrier_for_db
        line.tracking_number = tracking
        line.shipped_at = line.shipped_at or now
        line.updated_at = now
    db.session.commit()
    return {
        "success": True,
        "already_confirmed": False,
        "confirmed_at": now.isoformat(),
        "marketplace": platform,
        "carrier_code": mapping.marketplace_carrier_code,
        "service_code": mapping.marketplace_service_code,
        "tracking_number": tracking,
    }
