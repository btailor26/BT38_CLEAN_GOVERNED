"""Post-deploy eBay webhook health alignment and bounded catch-up.

This is an explicit deployment/recovery action, not a worker or polling loop.
It reuses the existing Notification API registration authority and existing
governed marketplace order importer. If the last durable eBay webhook is older
than the normal 24-hour order-import window, it widens that existing importer
only for this one bounded recovery run, then returns to event-driven operation.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from sqlalchemy import or_, text

from extensions import db
from models import MarketplaceOrder, Store
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
        text(
            "SELECT MAX(received_at) FROM webhooks.ebay_notifications "
            "WHERE topic = 'ORDER_CONFIRMATION'"
        )
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


def _marketplace_tracking_by_line(
    *,
    access_token: str,
    order_id: str,
) -> dict[str, dict[str, Any]]:
    """Read marketplace-owned tracking without creating or confirming fulfilment."""
    if not order_id:
        return {}

    try:
        response = requests.get(
            f"{EBAY_ORDERS_URL}/{order_id}/shipping_fulfillment",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
    except Exception:
        return {}

    if response.status_code >= 400:
        return {}

    payload = response.json() or {}
    fulfilments = payload.get("fulfillments") or []
    resolved: dict[str, dict[str, Any]] = {}

    for fulfilment in fulfilments:
        tracking_number = _text(fulfilment.get("trackingNumber"))
        carrier = _text(fulfilment.get("shippingCarrierCode"))
        shipped_at = _parse_ebay_datetime(fulfilment.get("shippedDate"))
        if not tracking_number and not carrier:
            continue

        value = {
            "carrier": carrier or None,
            "tracking_number": tracking_number or None,
            "shipped_at": shipped_at,
            "source": "ebay_shipping_fulfillment",
        }
        line_items = fulfilment.get("lineItems") or []
        for line_item in line_items:
            line_id = _text(line_item.get("lineItemId"))
            if line_id:
                resolved[line_id] = value

        if "__fallback__" not in resolved:
            resolved["__fallback__"] = value

    return resolved


def _persist_notification_auth_state(store: Store, error: str) -> None:
    """Persist only the known seller-consent-required state for the Stores UI."""
    text_error = str(error or "")
    if "HTTP 403" not in text_error or "Insufficient permissions" not in text_error:
        return

    creds: dict[str, Any] = {}
    raw = getattr(store, "api_key", None)
    if isinstance(raw, dict):
        creds = dict(raw)
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                creds = parsed
        except Exception:
            creds = {}

    now = datetime.utcnow().isoformat()
    creds.update({
        "ebay_notification_registration_status": "AUTHORIZATION_REQUIRED",
        "ebay_notification_registration_error": text_error,
        "ebay_notification_registration_attempted_at": now,
        "ebay_reauthorization_required": True,
    })
    store.api_key = json.dumps(creds)

    if hasattr(store, "auth_status"):
        store.auth_status = "auth_error"
    if hasattr(store, "auth_error_code"):
        store.auth_error_code = "ebay_notification_reauthorization_required"
    if hasattr(store, "auth_error_message"):
        store.auth_error_message = (
            "eBay requires one-time approval for the notification permissions."
        )
    if hasattr(store, "auth_error_at"):
        store.auth_error_at = datetime.utcnow()

    db.session.commit()


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
        marketplace_tracking = (
            _marketplace_tracking_by_line(access_token=access_token, order_id=order_id)
            if fulfillment_status == "FULFILLED"
            else {}
        )

        for item in order.get("lineItems") or []:
            sku = _text(item.get("sku")) or _text(item.get("legacyItemId"))
            line_id = _text(item.get("lineItemId")) or f"{order_id}:{sku}"
            price = item.get("lineItemCost") or {}
            tracking = marketplace_tracking.get(line_id) or marketplace_tracking.get("__fallback__") or {}
            result = upsert_governed_marketplace_order_line(
                store=store,
                marketplace_order_id=order_id,
                marketplace_order_item_id=line_id,
                sku=sku,
                quantity=_safe_int(item.get("quantity")),
                unit_price=_safe_float(price.get("value") if isinstance(price, dict) else 0),
                fulfillment_type="FBM",
                status="pending",
                carrier=_text(tracking.get("carrier")) or None,
                tracking_number=_text(tracking.get("tracking_number")) or None,
                shipped_at=tracking.get("shipped_at") or shipped_at,
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
                result.pop("_order_row", None)
                result["processing"] = {
                    "success": True,
                    "skipped": True,
                    "reason": "existing_order_line_no_duplicate_stock_processing",
                }
            result["marketplace_tracking_hydrated"] = bool(
                tracking.get("tracking_number") or tracking.get("carrier")
            )
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


def _recover_recent_missing_tracking(
    store: Store,
    *,
    max_days: int,
    limit: int = 100,
) -> dict[str, Any]:
    """Read exact eBay fulfilments for recent BT38 orders still missing tracking.

    This deliberately does not use the latest webhook timestamp. A newer order
    confirmation must never hide an older recent order whose shipment tracking
    was missed. The scan is bounded by age and count, selects existing FBM rows
    only, and reuses the exact read-only hydration authority.
    """
    from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max(1, int(max_days)))
    ).replace(tzinfo=None)

    candidates = (
        db.session.query(MarketplaceOrder.marketplace_order_id)
        .filter(
            MarketplaceOrder.store_id == store.id,
            MarketplaceOrder.fulfillment_type == "FBM",
            MarketplaceOrder.marketplace_order_id.isnot(None),
            or_(
                MarketplaceOrder.tracking_number.is_(None),
                MarketplaceOrder.tracking_number == "",
            ),
            MarketplaceOrder.created_at >= cutoff,
        )
        .distinct()
        .order_by(MarketplaceOrder.marketplace_order_id)
        .limit(max(1, min(int(limit), 100)))
        .all()
    )

    results: list[dict[str, Any]] = []
    exceptions = 0
    tracking_updates = 0
    for (order_id,) in candidates:
        clean_order_id = _text(order_id)
        if not clean_order_id:
            continue
        try:
            result = hydrate_exact_ebay_order(
                store=store,
                marketplace_order_id=clean_order_id,
                source="ebay_post_deploy_missing_tracking_recovery",
            )
        except Exception as exc:
            db.session.rollback()
            exceptions += 1
            result = {
                "success": False,
                "order_id": clean_order_id,
                "reason": "exact_tracking_recovery_exception",
                "error": str(exc),
                "marketplace_write_started": False,
            }
        tracking_updates += int(result.get("tracking_updates") or 0)
        results.append(result)

    return {
        "success": exceptions == 0,
        "bounded": True,
        "max_days": max(1, int(max_days)),
        "candidate_orders": len(candidates),
        "tracking_updates": tracking_updates,
        "exceptions": exceptions,
        "results": results,
        "marketplace_write_started": False,
        "polling_started": False,
        "scheduler_started": False,
    }


def align_ebay_notifications_and_recover_missed_changes(
    *, store_id: int = 23, max_days: int = 7
) -> dict[str, Any]:
    """Reconcile eBay registration and always run one bounded missed-event scan."""
    store = db.session.get(Store, int(store_id))
    if store is None:
        return {"success": False, "reason": "ebay_store_missing", "store_id": store_id}

    last_webhook_at = _last_durable_ebay_webhook_at()
    since = _bounded_since(last_webhook_at, max_days=max_days)

    registration: dict[str, Any]
    access_token = None
    try:
        access_token = _ebay_access_token(store)
        registration = ensure_ebay_order_notification_registration(
            store=store,
            access_token=access_token,
        )
    except Exception as exc:
        db.session.rollback()
        registration_error = str(exc)
        registration = {
            "ok": False,
            "success": False,
            "reason": "ebay_notification_registration_failed",
            "error": registration_error,
        }
        try:
            _persist_notification_auth_state(store, registration_error)
        except Exception:
            db.session.rollback()

    shipping_notification: dict[str, Any]
    if access_token and (registration.get("destination_id") or registration.get("ok")):
        try:
            from services.governed_ebay_shipping_notification_alignment import (
                ensure_ebay_shipping_notification_alignment,
            )
            shipping_notification = ensure_ebay_shipping_notification_alignment(
                store=store,
                access_token=access_token,
                destination_id=registration.get("destination_id"),
            )
        except Exception as exc:
            shipping_notification = {
                "success": False,
                "ok": False,
                "enabled": False,
                "topic_id": "ITEM_MARKED_SHIPPED",
                "reason": "shipping_notification_alignment_failed",
                "error": str(exc),
                "marketplace_write_started": False,
            }
    else:
        shipping_notification = {
            "success": True,
            "ok": True,
            "enabled": False,
            "topic_id": "ITEM_MARKED_SHIPPED",
            "reason": "base_notification_registration_unavailable",
            "marketplace_write_started": False,
        }

    try:
        orders = _catch_up_ebay_orders(store, since=since)
    except Exception as exc:
        db.session.rollback()
        orders = {
            "success": False,
            "reason": "ebay_bounded_order_catchup_failed",
            "error": str(exc),
            "since": since.isoformat(),
        }

    try:
        tracking_recovery = _recover_recent_missing_tracking(
            store,
            max_days=max_days,
        )
    except Exception as exc:
        db.session.rollback()
        tracking_recovery = {
            "success": False,
            "bounded": True,
            "reason": "ebay_missing_tracking_recovery_failed",
            "error": str(exc),
            "marketplace_write_started": False,
            "polling_started": False,
            "scheduler_started": False,
        }

    try:
        from services.governed_ebay_missed_listing_recovery import recover_missed_ebay_listings
        listing_recovery = recover_missed_ebay_listings(store_id=store.id)
    except Exception as exc:
        db.session.rollback()
        listing_recovery = {"success": False, "error": str(exc)}

    registration_ok = bool(registration.get("ok") or registration.get("success"))
    orders_ok = bool(orders.get("success"))

    return {
        "success": registration_ok and orders_ok,
        "store_id": int(store.id),
        "last_durable_webhook_at": (
            last_webhook_at.isoformat() if last_webhook_at is not None else None
        ),
        "bounded_since": since.isoformat(),
        "registration": registration,
        "shipping_notification": shipping_notification,
        "order_catchup": orders,
        "tracking_catchup": tracking_recovery,
        "listing_catchup": listing_recovery,
        "event_driven_primary": True,
        "recovery_ran_even_if_registration_unhealthy": True,
        "polling_started": False,
        "scheduler_started": False,
    }
