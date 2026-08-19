"""Marketplace confirmation for FBM shipments purchased outside the marketplace.

Marketplace-native postage remains owned by that marketplace. Amazon Buy
Shipping must not be confirmed a second time by BT38. External Packlink labels
are saved and printable first; marketplace transfer waits for the persistent
carrier/service mapping to be verified once.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

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


def _amazon_order_for_shipment(shipment: FBMShipment) -> MarketplaceOrder | None:
    return MarketplaceOrder.query.filter_by(
        store_id=shipment.store_id,
        marketplace_order_id=shipment.marketplace_order_id,
    ).order_by(MarketplaceOrder.id.asc()).first()


def _persist_confirmed_order_lines(
    *,
    order: MarketplaceOrder,
    shipment: FBMShipment,
    carrier: str,
    tracking: str,
    now: datetime,
) -> None:
    for line in order_lines(order):
        line.carrier = carrier
        line.tracking_number = tracking
        line.shipped_at = line.shipped_at or now
        line.updated_at = now


def _mapping_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _amazon_package_reference_id(shipment: FBMShipment) -> str:
    """Amazon Orders v0 requires a string containing a positive numeric value."""
    value = int(getattr(shipment, "id", 0) or 0)
    if value <= 0:
        raise FBMMarketplaceConfirmationError("Amazon package reference requires a positive BT38 shipment ID.")
    return str(value)


def _record_dispatch_bell_event(
    *,
    order: MarketplaceOrder,
    shipment: FBMShipment,
    marketplace: str,
    carrier: str,
    service: str,
    tracking: str,
    now: datetime,
) -> bool:
    """Write one bell/system event after the marketplace accepted dispatch."""
    marketplace_name = (marketplace or "marketplace").strip().title()
    carrier = _mapping_text(carrier)
    service = _mapping_text(service)
    tracking = "".join(str(tracking or "").strip().split())
    description = f"{marketplace_name} order {order.marketplace_order_id} dispatched"
    if carrier:
        description += f" via {carrier}"
    if service:
        description += f" · {service}"

    details = {
        "source": "fbm_marketplace_confirmation",
        "marketplace": (marketplace or "").strip().lower(),
        "marketplace_order_id": str(order.marketplace_order_id),
        "shipment_id": int(shipment.id),
        "provider": str(shipment.provider or ""),
        "provider_shipment_id": str(shipment.provider_shipment_id or ""),
        "carrier": carrier,
        "service": service,
        "tracking_number": tracking,
        "confirmation_status": "confirmed",
    }

    try:
        db.session.execute(
            text(
                """
                INSERT INTO system_events
                    (timestamp, actor, actor_id, category, entity_id, entity_type, description, details_json)
                VALUES
                    (:timestamp, :actor, NULL, :category, :entity_id, :entity_type, :description, CAST(:details_json AS json))
                """
            ),
            {
                "timestamp": now,
                "actor": "marketplace_confirmation",
                "category": "marketplace_dispatch",
                "entity_id": int(order.id),
                "entity_type": "marketplace_order",
                "description": description,
                "details_json": json.dumps(details),
            },
        )
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def confirm_amazon_packlink_shipment(
    *,
    shipment: FBMShipment,
    mapping: FBMCarrierServiceMapping,
) -> dict[str, Any]:
    """Send a paid Packlink shipment to Amazon using its saved verified mapping."""
    if str(shipment.provider or "").strip().casefold() != "packlink":
        return {"success": False, "held": True, "reason": "not_packlink"}

    if mapping.verification_status != "verified":
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

    order = _amazon_order_for_shipment(shipment)
    if order is None or _platform(order) != "amazon":
        shipment.marketplace_confirmation_status = "order_missing"
        shipment.marketplace_confirmation_error = "Amazon marketplace order is missing from BT38."
        db.session.commit()
        return {"success": False, "held": True, "reason": "order_missing"}

    carrier_code = _mapping_text(mapping.marketplace_carrier_code)
    carrier_name = _mapping_text(mapping.marketplace_carrier_name or carrier_code)
    shipping_method = _mapping_text(mapping.marketplace_service_name or mapping.marketplace_service_code)
    tracking = "".join(str(shipment.tracking_number or "").strip().split())

    if not carrier_code or not carrier_name or not shipping_method:
        shipment.marketplace_confirmation_status = "mapping_under_review"
        shipment.marketplace_confirmation_error = "Verified Amazon mapping is incomplete."
        db.session.commit()
        return {"success": False, "held": True, "reason": "mapping_incomplete"}
    if any(value.casefold() == "other" for value in (carrier_code, carrier_name, shipping_method)):
        shipment.marketplace_confirmation_status = "amazon_vtr_mapping_blocked"
        shipment.marketplace_confirmation_error = "Amazon Packlink confirmation cannot use Other."
        db.session.commit()
        return {"success": False, "held": True, "reason": "amazon_vtr_mapping_blocked"}
    if not tracking:
        shipment.marketplace_confirmation_status = "tracking_required"
        shipment.marketplace_confirmation_error = "Packlink tracking number is required before Amazon confirmation."
        db.session.commit()
        return {"success": False, "held": True, "reason": "tracking_required"}

    try:
        client, marketplace_id = _amazon_client(order)
        amazon_items = _amazon_order_items(order)
        client.confirm_shipment(
            str(order.marketplace_order_id),
            marketplaceId=marketplace_id,
            packageDetail={
                "packageReferenceId": _amazon_package_reference_id(shipment),
                "carrierCode": carrier_code,
                "carrierName": carrier_name,
                "shippingMethod": shipping_method,
                "trackingNumber": tracking,
                "shipDate": _amazon_ship_date(shipment),
                "orderItems": amazon_items,
            },
        )
    except Exception as exc:
        shipment.marketplace_confirmation_status = "confirmation_failed"
        shipment.marketplace_confirmation_error = str(exc)
        db.session.commit()
        return {"success": False, "held": False, "reason": "confirmation_failed", "error": str(exc)}

    now = datetime.utcnow()
    shipment.marketplace_confirmed_at = now
    shipment.marketplace_confirmation_status = "confirmed"
    shipment.marketplace_confirmation_error = None
    _persist_confirmed_order_lines(
        order=order,
        shipment=shipment,
        carrier=carrier_name,
        tracking=tracking,
        now=now,
    )
    db.session.commit()
    bell_recorded = _record_dispatch_bell_event(
        order=order,
        shipment=shipment,
        marketplace="amazon",
        carrier=carrier_name,
        service=shipping_method,
        tracking=tracking,
        now=now,
    )
    return {
        "success": True,
        "already_confirmed": False,
        "confirmed_at": now.isoformat(),
        "marketplace": "amazon",
        "carrier_code": carrier_code,
        "carrier_name": carrier_name,
        "shipping_method": shipping_method,
        "tracking_number": tracking,
        "mapping_id": mapping.id,
        "vtr_safe_external": True,
        "bell_recorded": bell_recorded,
    }


def _confirm_ebay_external(
    *,
    order: MarketplaceOrder,
    shipment: FBMShipment,
    mapping: FBMCarrierServiceMapping,
    tracking: str,
) -> dict[str, Any]:
    """Use BT38's existing governed eBay CompleteSale implementation."""
    from services.governed_ebay_dispatch import complete_sale
    from services.runtime_action_guard import is_runtime_action_allowed

    store = getattr(order, "store", None)
    if store is None:
        raise FBMMarketplaceConfirmationError("eBay store is missing from this BT38 order.")

    provider = str(shipment.provider or "").strip().lower()
    manual = provider == "manual"
    guard_context = {
        "actor_user": None,
        "context": (
            "fbm_manual_external_confirmation"
            if manual
            else "fbm_packlink_event_confirmation"
        ),
    }
    if not manual:
        guard_context["automatic_push"] = True

    guard = is_runtime_action_allowed(
        store,
        "push",
        manual=manual,
        context=guard_context,
    )
    if not guard.get("allowed"):
        raise FBMMarketplaceConfirmationError(
            "eBay external shipment confirmation blocked by governed runtime: "
            + str(guard.get("reason") or "runtime_guard_blocked")
        )

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
    """Confirm one external shipment only after its saved mapping is verified."""
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

    order = _amazon_order_for_shipment(shipment)
    if order is None:
        shipment.marketplace_confirmation_status = "order_missing"
        shipment.marketplace_confirmation_error = "Marketplace order is missing from BT38."
        db.session.commit()
        return {"success": False, "held": True, "reason": "order_missing"}

    platform = _platform(order)
    if platform == "amazon" and str(shipment.provider or "").strip().casefold() == "packlink":
        return confirm_amazon_packlink_shipment(shipment=shipment, mapping=mapping)

    carrier_name_for_event = str(
        mapping.marketplace_carrier_name
        or mapping.marketplace_carrier_code
        or shipment.carrier
        or ""
    ).strip()
    service_name_for_event = str(
        mapping.marketplace_service_name
        or mapping.marketplace_service_code
        or shipment.service
        or ""
    ).strip()

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
            carrier_name_for_event = carrier_name
            service_name_for_event = shipping_method

            client.confirm_shipment(
                str(order.marketplace_order_id),
                marketplaceId=marketplace_id,
                packageDetail={
                    "packageReferenceId": _amazon_package_reference_id(shipment),
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
    _persist_confirmed_order_lines(
        order=order,
        shipment=shipment,
        carrier=carrier_for_db,
        tracking=tracking,
        now=now,
    )
    db.session.commit()
    bell_recorded = _record_dispatch_bell_event(
        order=order,
        shipment=shipment,
        marketplace=platform,
        carrier=carrier_name_for_event,
        service=service_name_for_event,
        tracking=tracking,
        now=now,
    )
    return {
        "success": True,
        "already_confirmed": False,
        "confirmed_at": now.isoformat(),
        "marketplace": platform,
        "carrier_code": mapping.marketplace_carrier_code,
        "service_code": mapping.marketplace_service_code,
        "tracking_number": tracking,
        "bell_recorded": bell_recorded,
    }
