"""Governed Amazon MCF execution using the MCF order's selected FBA store."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import json
import os

from sp_api.api import FulfillmentOutbound
from sp_api.base import Marketplaces

from extensions import db
from models import MCFOrder, MarketplaceOrder
from services.governed_mcf_tracking import (
    has_unforwarded_tracking,
    load_tracking_details,
    mark_tracking_forwarded,
    sync_amazon_tracking_details,
)

UK_MARKETPLACE_ID = "A1F83G8C2ARO7P"


def _credentials_for_store(store) -> dict[str, str]:
    """Use the same Store credential path as the governed Amazon importer."""
    raw = store.api_key or {}

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}

    credentials = {
        "refresh_token": (
            raw.get("refresh_token")
            or os.getenv("AMAZON_REFRESH_TOKEN")
            or os.getenv("SP_API_REFRESH_TOKEN")
        ),
        "lwa_app_id": (
            raw.get("lwa_app_id")
            or raw.get("lwa_client_id")
            or raw.get("client_id")
            or os.getenv("AMAZON_LWA_CLIENT_ID")
            or os.getenv("AMAZON_LWA_APP_ID")
            or os.getenv("SP_API_LWA_CLIENT_ID")
        ),
        "lwa_client_secret": (
            raw.get("lwa_client_secret")
            or raw.get("client_secret")
            or os.getenv("AMAZON_LWA_CLIENT_SECRET")
            or os.getenv("SP_API_LWA_CLIENT_SECRET")
        ),
    }

    aws_access_key = (
        raw.get("aws_access_key")
        or raw.get("aws_access_key_id")
        or os.getenv("AMAZON_AWS_ACCESS_KEY_ID")
        or os.getenv("SP_API_AWS_ACCESS_KEY_ID")
    )
    aws_secret_key = (
        raw.get("aws_secret_key")
        or raw.get("aws_secret_access_key")
        or os.getenv("AMAZON_AWS_SECRET_ACCESS_KEY")
        or os.getenv("SP_API_AWS_SECRET_ACCESS_KEY")
    )
    role_arn = (
        raw.get("role_arn")
        or raw.get("aws_user_arn")
        or os.getenv("AMAZON_AWS_ROLE_ARN")
        or os.getenv("SP_API_ROLE_ARN")
    )

    if aws_access_key:
        credentials["aws_access_key"] = aws_access_key
    if aws_secret_key:
        credentials["aws_secret_key"] = aws_secret_key
    if role_arn:
        credentials["role_arn"] = role_arn

    missing = [
        key
        for key in (
            "refresh_token",
            "lwa_app_id",
            "lwa_client_secret",
        )
        if not credentials.get(key)
    ]
    if missing:
        raise RuntimeError(
            "mcf_fba_store_credentials_missing:"
            + ",".join(missing)
        )

    return credentials


def _client(mcf_order: MCFOrder) -> FulfillmentOutbound:
    store = mcf_order.fba_store
    if store is None or not store.is_active:
        raise RuntimeError("mcf_fba_store_missing_or_inactive")

    return FulfillmentOutbound(
        marketplace=Marketplaces.UK,
        credentials=_credentials_for_store(store),
    )


def _bind_source_lines(mcf_order: MCFOrder) -> list[MarketplaceOrder]:
    lines = (
        MarketplaceOrder.query
        .filter(
            MarketplaceOrder.store_id == mcf_order.source_store_id,
            MarketplaceOrder.marketplace_order_id == mcf_order.source_order_id,
        )
        .all()
    )
    for line in lines:
        if line.mcf_order_id and line.mcf_order_id != mcf_order.id:
            raise RuntimeError(
                f"source_order_already_bound_to_mcf:{line.mcf_order_id}"
            )
        line.mcf_order_id = mcf_order.id
        line.fulfillment_type = "FBA"
        line.status = "mcf_pending_submission"
        line.processed_at = datetime.utcnow()
    db.session.commit()
    return lines


def _shipping_speed_category(value: str | None) -> str:
    normalized = str(value or "Standard").strip().lower()
    return {
        "standard": "Standard",
        "expedited": "Expedited",
        "priority": "Priority",
    }.get(normalized, "Standard")


def _amazon_visibility_pending(
    mcf_order: MCFOrder,
    error: Exception,
) -> bool:
    message = str(error)
    visibility_error = (
        "InvalidInput" in message
        and "Unable to get order info" in message
    )
    if not visibility_error:
        return False

    status = str(mcf_order.status or "").lower()
    amazon_status = str(mcf_order.amazon_status or "").upper()
    if status not in {"submitted", "processing"}:
        return False
    if amazon_status != "RECEIVED":
        return False

    submitted_at = (
        mcf_order.amazon_status_updated_at
        or mcf_order.updated_at
        or mcf_order.created_at
    )
    if submitted_at is None:
        return False

    return (
        datetime.utcnow() - submitted_at
        <= timedelta(minutes=30)
    )


def submit_mcf_order(mcf_order: MCFOrder) -> tuple[bool, str]:
    try:
        _bind_source_lines(mcf_order)
        items_payload = [
            {
                "sellerSku": item.fba_sku,
                "sellerFulfillmentOrderItemId": (
                    f"{mcf_order.seller_fulfillment_order_id}-{item.id}"
                ),
                "quantity": item.quantity,
                "perUnitDeclaredValue": {
                    "currencyCode": "GBP",
                    "value": str(item.unit_price or 0),
                },
            }
            for item in mcf_order.items.all()
        ]
        payload = {
            "sellerFulfillmentOrderId": mcf_order.seller_fulfillment_order_id,
            "displayableOrderId": mcf_order.displayable_order_id,
            "displayableOrderDate": mcf_order.created_at.isoformat() + "Z",
            "displayableOrderComment": mcf_order.displayable_comment or "",
            "shippingSpeedCategory": _shipping_speed_category(
                mcf_order.shipping_speed
            ),
            "destinationAddress": {
                "name": mcf_order.destination_name or "Customer",
                "addressLine1": mcf_order.destination_address_line1 or "",
                "addressLine2": mcf_order.destination_address_line2 or "",
                "city": mcf_order.destination_city or "",
                "stateOrRegion": mcf_order.destination_state or "",
                "postalCode": mcf_order.destination_postcode or "",
                "countryCode": mcf_order.destination_country or "GB",
                "phone": mcf_order.destination_phone or "",
            },
            "items": items_payload,
        }

        source_email = next(
            (
                str(line.ship_to_email or "").strip()
                for line in mcf_order.marketplace_orders.all()
                if str(line.ship_to_email or "").strip()
            ),
            "",
        )
        if source_email:
            payload["notificationEmails"] = [source_email]

        try:
            _client(mcf_order).create_fulfillment_order(**payload)
        except Exception as exc:
            error = str(exc)
            mcf_order.status = "failed"
            mcf_order.last_error = error
            mcf_order.retry_count = (mcf_order.retry_count or 0) + 1
            for line in mcf_order.marketplace_orders.all():
                line.status = "mcf_submission_failed"
                line.error_message = error
            db.session.commit()
            return False, f"Failed to submit to Amazon: {error}"

        mcf_order.status = "submitted"
        mcf_order.amazon_status = "RECEIVED"
        mcf_order.amazon_status_updated_at = datetime.utcnow()
        mcf_order.last_error = None
        for line in mcf_order.marketplace_orders.all():
            line.status = "mcf_accepted"
            line.error_message = None
        db.session.commit()
        return True, "MCF order submitted to Amazon successfully"
    except Exception as exc:
        mcf_order.status = "failed"
        mcf_order.last_error = str(exc)
        mcf_order.retry_count = (mcf_order.retry_count or 0) + 1
        db.session.commit()
        return False, f"Error submitting MCF order: {exc}"


def cancel_mcf_order(mcf_order: MCFOrder) -> tuple[bool, dict[str, Any]]:
    """Cancel one exact Amazon MCF order through the same live client path."""
    try:
        status = str(mcf_order.status or "").strip().lower()
        amazon_status = str(mcf_order.amazon_status or "").strip().upper()

        if status == "cancelled" or amazon_status == "CANCELLED":
            return True, {
                "cancelled": True,
                "already_cancelled": True,
                "status": mcf_order.amazon_status or "CANCELLED",
            }

        if not mcf_order.seller_fulfillment_order_id:
            return False, {
                "error": "seller_fulfillment_order_id_missing",
            }

        try:
            _client(mcf_order).cancel_fulfillment_order(
                sellerFulfillmentOrderId=(
                    mcf_order.seller_fulfillment_order_id
                )
            )
        except Exception as exc:
            error = str(exc)
            mcf_order.last_error = error
            mcf_order.retry_count = (mcf_order.retry_count or 0) + 1
            for line in mcf_order.marketplace_orders.all():
                line.status = "mcf_cancel_failed"
                line.error_message = error
            db.session.commit()
            return False, {"error": error}

        mcf_order.status = "cancelled"
        mcf_order.amazon_status = "CANCELLED"
        mcf_order.amazon_status_updated_at = datetime.utcnow()
        mcf_order.last_error = None

        for line in mcf_order.marketplace_orders.all():
            line.status = "cancelled"
            line.error_message = None
            line.updated_at = datetime.utcnow()

        db.session.commit()
        return True, {
            "cancelled": True,
            "status": "CANCELLED",
            "seller_fulfillment_order_id": (
                mcf_order.seller_fulfillment_order_id
            ),
        }
    except Exception as exc:
        return False, {"error": str(exc)}


def refresh_mcf_status(
    mcf_order: MCFOrder,
) -> tuple[bool, dict[str, Any]]:
    try:
        try:
            response = _client(mcf_order).get_fulfillment_order(
                sellerFulfillmentOrderId=(
                    mcf_order.seller_fulfillment_order_id
                )
            )
            payload = getattr(response, "payload", None) or {}
        except Exception as exc:
            if _amazon_visibility_pending(mcf_order, exc):
                return True, {
                    "status": mcf_order.amazon_status or "RECEIVED",
                    "carrier": mcf_order.carrier,
                    "tracking_number": mcf_order.tracking_number,
                    "tracking_details": load_tracking_details(mcf_order.id),
                    "ship_date": (
                        mcf_order.ship_date.isoformat()
                        if mcf_order.ship_date
                        else None
                    ),
                    "pending_visibility": True,
                    "message": (
                        "Amazon accepted the MCF order but it is not visible "
                        "in status lookup yet."
                    ),
                }
            return False, {"error": str(exc)}

        fulfillment_order = payload.get("fulfillmentOrder") or {}
        mcf_order.amazon_status = (
            fulfillment_order.get("fulfillmentOrderStatus")
            or mcf_order.amazon_status
        )
        mcf_order.amazon_order_id = (
            fulfillment_order.get("amazonOrderId")
            or fulfillment_order.get("AmazonOrderId")
            or mcf_order.amazon_order_id
        )
        mcf_order.amazon_status_updated_at = datetime.utcnow()

        status = str(mcf_order.amazon_status or "").upper()
        if status in {"COMPLETE", "COMPLETE_PARTIALLED"}:
            mcf_order.status = "completed"
        elif status in {"CANCELLED", "INVALID"}:
            mcf_order.status = "cancelled"
        elif status in {"PLANNING", "PROCESSING", "RECEIVED"}:
            mcf_order.status = "processing"

        tracking_details = sync_amazon_tracking_details(
            mcf_order.id,
            payload,
        )
        primary_tracking = tracking_details[0] if tracking_details else None

        if primary_tracking:
            mcf_order.carrier = (
                primary_tracking.get("carrier")
                or mcf_order.carrier
            )
            mcf_order.tracking_number = (
                primary_tracking.get("tracking_number")
                or mcf_order.tracking_number
            )

            if primary_tracking.get("ship_date"):
                mcf_order.ship_date = primary_tracking["ship_date"]
            if primary_tracking.get("estimated_arrival_date"):
                mcf_order.estimated_arrival_date = (
                    primary_tracking["estimated_arrival_date"]
                )

        db.session.commit()
        return True, {
            "status": mcf_order.amazon_status,
            "amazon_order_id": mcf_order.amazon_order_id,
            "carrier": mcf_order.carrier,
            "tracking_number": mcf_order.tracking_number,
            "tracking_details": tracking_details,
            "ship_date": (
                mcf_order.ship_date.isoformat()
                if mcf_order.ship_date
                else None
            ),
        }
    except Exception as exc:
        return False, {"error": str(exc)}


def _walk_payload(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk_payload(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_payload(item)


def _mcf_signal_identifiers(payload: dict) -> tuple[set[str], set[str]]:
    seller_fulfillment_ids: set[str] = set()
    amazon_order_ids: set[str] = set()

    for key, value in _walk_payload(payload or {}):
        if value in (None, ""):
            continue

        key_name = key.replace("_", "").strip().lower()
        text_value = str(value).strip()

        if key_name == "sellerfulfillmentorderid":
            seller_fulfillment_ids.add(text_value)
            continue

        if key_name == "sellerfulfillmentorderitemid":
            candidate = text_value.rsplit("-", 1)[0]
            if candidate:
                seller_fulfillment_ids.add(candidate)
            continue

        if key_name == "amazonorderid":
            amazon_order_ids.add(text_value)

    return seller_fulfillment_ids, amazon_order_ids


def refresh_mcf_from_amazon_signal(payload: dict) -> dict[str, Any]:
    """Use one Amazon signal to refresh one exact existing MCF lifecycle.

    The webhook is only the wake-up signal. Amazon Fulfillment Outbound remains
    the status/tracking authority, and the existing eBay complete-sale adapter
    remains the marketplace write path.
    """
    seller_ids, amazon_order_ids = _mcf_signal_identifiers(payload or {})

    if not seller_ids and not amazon_order_ids:
        return {
            "success": True,
            "skipped": True,
            "reason": "amazon_signal_has_no_mcf_identity",
            "database_touched": False,
        }

    mcf = None
    if seller_ids:
        mcf = (
            MCFOrder.query
            .filter(MCFOrder.seller_fulfillment_order_id.in_(seller_ids))
            .order_by(MCFOrder.id.desc())
            .first()
        )

    if mcf is None and amazon_order_ids:
        mcf = (
            MCFOrder.query
            .filter(MCFOrder.amazon_order_id.in_(amazon_order_ids))
            .order_by(MCFOrder.id.desc())
            .first()
        )

    if mcf is None:
        return {
            "success": True,
            "skipped": True,
            "reason": "amazon_signal_mcf_not_found",
            "database_touched": True,
        }

    refreshed, refresh_result = refresh_mcf_status(mcf)
    if not refreshed:
        return {
            "success": False,
            "skipped": False,
            "reason": "amazon_mcf_status_refresh_failed",
            "mcf_order_id": mcf.id,
            "error": refresh_result.get("error"),
            "database_touched": True,
        }

    tracking_details = (
        refresh_result.get("tracking_details")
        or load_tracking_details(mcf.id)
    )
    if not tracking_details:
        return {
            "success": True,
            "skipped": True,
            "reason": "amazon_mcf_tracking_not_available_yet",
            "mcf_order_id": mcf.id,
            "amazon_status": mcf.amazon_status,
            "database_touched": True,
        }

    if not has_unforwarded_tracking(mcf.id):
        return {
            "success": True,
            "skipped": True,
            "reason": "mcf_tracking_already_enriched",
            "mcf_order_id": mcf.id,
            "tracking_numbers": [
                item.get("tracking_number")
                for item in tracking_details
            ],
            "database_touched": True,
        }

    lines = (
        MarketplaceOrder.query
        .filter(MarketplaceOrder.mcf_order_id == mcf.id)
        .order_by(MarketplaceOrder.id)
        .all()
    )
    if not lines:
        return {
            "success": False,
            "skipped": False,
            "reason": "mcf_source_marketplace_order_missing",
            "mcf_order_id": mcf.id,
            "database_touched": True,
        }

    accepted_at = (
        mcf.amazon_status_updated_at
        or mcf.updated_at
        or mcf.created_at
    )
    release_at = (
        accepted_at + timedelta(hours=1)
        if accepted_at is not None
        else None
    )
    if release_at is not None and datetime.utcnow() < release_at:
        return {
            "success": True,
            "skipped": True,
            "reason": "tracking_received_inside_one_hour_cancellation_window",
            "mcf_order_id": mcf.id,
            "tracking_numbers": [
                item.get("tracking_number")
                for item in tracking_details
            ],
            "release_at": release_at.isoformat(),
            "database_touched": True,
        }

    anchor = lines[0]
    if anchor.store is None or "ebay" not in str(anchor.store.platform or "").lower():
        return {
            "success": True,
            "skipped": True,
            "reason": "mcf_source_marketplace_tracking_not_supported",
            "mcf_order_id": mcf.id,
            "tracking_numbers": [
                item.get("tracking_number")
                for item in tracking_details
            ],
            "database_touched": True,
        }

    from services.runtime_action_guard import is_runtime_action_allowed
    from services.governed_ebay_dispatch import complete_sale

    guard = is_runtime_action_allowed(
        anchor.store,
        "push",
        manual=False,
        context={
            "actor_user": None,
            "context": "mcf_tracking_amazon_webhook_enrichment",
        },
    )
    if not guard.get("allowed"):
        return {
            "success": False,
            "skipped": False,
            "reason": "mcf_tracking_ebay_enrichment_blocked",
            "mcf_order_id": mcf.id,
            "tracking_numbers": [
                item.get("tracking_number")
                for item in tracking_details
            ],
            "error": guard.get("reason"),
            "database_touched": True,
        }

    dispatch = complete_sale(
        anchor,
        carrier=mcf.carrier or "Other",
        tracking_number=mcf.tracking_number,
        tracking_details=tracking_details,
    )
    if not dispatch.get("success"):
        for line in lines:
            line.status = "mcf_tracking_update_failed"
            line.error_message = dispatch.get("error")
            line.updated_at = datetime.utcnow()
        db.session.commit()
        return {
            "success": False,
            "skipped": False,
            "reason": "mcf_tracking_ebay_update_failed",
            "mcf_order_id": mcf.id,
            "tracking_numbers": [
                item.get("tracking_number")
                for item in tracking_details
            ],
            "error": dispatch.get("error"),
            "database_touched": True,
        }

    primary_tracking = tracking_details[0]
    now = datetime.utcnow()
    for line in lines:
        line.carrier = primary_tracking.get("carrier") or mcf.carrier
        line.tracking_number = (
            primary_tracking.get("tracking_number")
            or mcf.tracking_number
        )
        line.shipped_at = line.shipped_at or anchor.shipped_at or now
        line.status = "mcf_tracking_updated"
        line.error_message = None
        line.updated_at = now

    mark_tracking_forwarded(mcf.id)
    db.session.commit()

    return {
        "success": True,
        "skipped": False,
        "reason": "mcf_tracking_enriched_from_amazon_signal",
        "mcf_order_id": mcf.id,
        "amazon_status": mcf.amazon_status,
        "carrier": mcf.carrier,
        "tracking_number": mcf.tracking_number,
        "tracking_numbers": [
            item.get("tracking_number")
            for item in tracking_details
        ],
        "database_touched": True,
    }
