"""Post-deploy eBay webhook health alignment and bounded catch-up.

This is an explicit deployment/recovery action, not a worker or polling loop.
It reuses the existing Notification API registration authority and existing
governed marketplace order importer. If the last durable eBay webhook is older
than the normal 24-hour order-import window, it widens that existing importer
only for this one bounded recovery run, then returns to event-driven operation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy import text

from extensions import db
from models import Store
from services.governed_ebay_notification_registration import (
    ensure_ebay_order_notification_registration,
)
from services.governed_marketplace_order_import import (
    EBAY_ORDERS_URL,
    _ebay_access_token,
    _parse_ebay_datetime,
    _process_exact_imported_order,
    _safe_float,
    _safe_int,
    _text,
    upsert_governed_marketplace_order_line,
)


def _last_durable_ebay_webhook_at() -> datetime | None:
    return db.session.execute(
        text("SELECT MAX(received_at) FROM webhooks.ebay_notifications")
    ).scalar()


def _bounded_since(last_webhook_at: datetime | None, max_days: int) -> datetime:
    floor = datetime.now(timezone.utc) - timedelta(days=max(1, int(max_days)))
    if last_webhook_at is None:
        return floor
    value = last_webhook_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return max(floor, value - timedelta(minutes=5))


def _catch_up_ebay_orders(store: Store, *, since: datetime) -> dict[str, Any]:
    """Read only the bounded missed window and reuse canonical upsert/processing."""
    access_token = _ebay_access_token(store)
    since_text = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    response = requests.get(
        EBAY_ORDERS_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        params={"filter": f"creationdate:[{since_text}..]", "limit": "100"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"ebay_bounded_catchup_failed:{response.status_code}:{response.text[:1000]}"
        )

    orders = (response.json() or {}).get("orders") or []
    created = imported = skipped = 0
    results: list[dict[str, Any]] = []

    for order in orders:
        order_id = _text(order.get("orderId"))
        payment_status = _text(order.get("orderPaymentStatus")).upper()
        if not order_id or (payment_status and payment_status != "PAID"):
            skipped += 1
            continue

        instructions = order.get("fulfillmentStartInstructions") or []
        instruction = instructions[0] if instructions else {}
        shipping_step = instruction.get("shippingStep") or {}
        ship_to = shipping_step.get("shipTo") or {}
        address = ship_to.get("contactAddress") or {}
        address_parts = [
            _text(address.get("addressLine1")),
            _text(address.get("addressLine2")),
        ]
        fulfillment_status = _text(order.get("orderFulfillmentStatus")).upper()
        shipped_at = (
            _parse_ebay_datetime(order.get("lastModifiedDate"))
            if fulfillment_status == "FULFILLED"
            else None
        )

        for item in order.get("lineItems") or []:
            sku = _text(item.get("sku")) or _text(item.get("legacyItemId"))
            line_id = _text(item.get("lineItemId")) or f"{order_id}:{sku}"
            price = item.get("lineItemCost") or {}
            result = upsert_governed_marketplace_order_line(
                store=store,
                marketplace_order_id=order_id,
                marketplace_order_item_id=line_id,
                sku=sku,
                quantity=_safe_int(item.get("quantity")),
                unit_price=_safe_float(price.get("value") if isinstance(price, dict) else 0),
                fulfillment_type="FBM",
                status="pending",
                shipped_at=shipped_at,
                ship_to_name=_text(ship_to.get("fullName")),
                ship_to_address=", ".join(part for part in address_parts if part),
                ship_to_city=_text(address.get("city")),
                ship_to_postcode=_text(address.get("postalCode")),
                ship_to_country=_text(address.get("countryCode")).upper()[:2],
                ship_to_email=_text(ship_to.get("email")),
                ship_to_phone=(
                    _text((ship_to.get("primaryPhone") or {}).get("phoneNumber"))
                    or _text(ship_to.get("phoneNumber"))
                ),
                marketplace_created_at=_parse_ebay_datetime(order.get("creationDate")),
                import_source="ebay_webhook_restored_bounded_catchup",
            )
            was_created = bool(result.get("created"))
            if was_created:
                result["processing"] = _process_exact_imported_order(
                    result,
                    source="ebay_webhook_restored_bounded_catchup:exact_order",
                )
                created += 1
            else:
                # Existing idempotency identity means the event/order was already
                # represented. Never process stock a second time.
                result.pop("_order_row", None)
                result["processing"] = {
                    "success": True,
                    "skipped": True,
                    "reason": "existing_order_line_no_duplicate_stock_processing",
                }
            imported += 1
            results.append(result)

    db.session.commit()
    return {
        "success": True,
        "since": since.isoformat(),
        "orders_seen": len(orders),
        "lines_seen": imported,
        "created": created,
        "skipped": skipped,
        "results": results[:100],
        "polling_started": False,
        "scheduler_started": False,
    }


def align_ebay_notifications_and_recover_missed_changes(
    *, store_id: int = 23, max_days: int = 7
) -> dict[str, Any]:
    """Reconcile live eBay registration, then run one bounded missed-event scan."""
    store = db.session.get(Store, int(store_id))
    if store is None:
        return {"success": False, "reason": "ebay_store_missing", "store_id": store_id}

    access_token = _ebay_access_token(store)
    registration = ensure_ebay_order_notification_registration(
        store=store,
        access_token=access_token,
    )
    if not registration.get("ok"):
        return {
            "success": False,
            "reason": "ebay_notification_registration_not_healthy",
            "registration": registration,
        }

    last_webhook_at = _last_durable_ebay_webhook_at()
    since = _bounded_since(last_webhook_at, max_days=max_days)
    orders = _catch_up_ebay_orders(store, since=since)

    listing_recovery = None
    try:
        from services.governed_ebay_missed_listing_recovery import recover_missed_ebay_listings
        listing_recovery = recover_missed_ebay_listings(store_id=store.id)
    except Exception as exc:
        db.session.rollback()
        listing_recovery = {"success": False, "error": str(exc)}

    return {
        "success": bool(orders.get("success")) and bool(registration.get("ok")),
        "store_id": int(store.id),
        "last_durable_webhook_at": (
            last_webhook_at.isoformat() if last_webhook_at is not None else None
        ),
        "bounded_since": since.isoformat(),
        "registration": registration,
        "order_catchup": orders,
        "listing_catchup": listing_recovery,
        "event_driven_primary": True,
        "polling_started": False,
        "scheduler_started": False,
    }
