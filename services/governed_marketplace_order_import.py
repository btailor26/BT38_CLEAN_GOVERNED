"""
BT38 governed marketplace order import.

One clear path only:
existing store connection
-> governed marketplace order import
-> MarketplaceOrder
-> governed order stock mutation bridge

No new store connection.
No legacy processor restore.
No marketplace push.
No app import side effects.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import json
import os

import requests
from sqlalchemy import text

from extensions import db
from models import Store, MarketplaceOrder, MarketplaceListing, SyncLog


EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
EBAY_ORDERS_URL = "https://api.ebay.com/sell/fulfillment/v1/order"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        number = int(value or default)
    except Exception:
        number = default
    return max(1, number)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return default


def _store_credentials(store: Store) -> dict[str, Any]:
    raw = store.api_key or {}
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _find_listing(store: Store, sku: str):
    if not sku:
        return None

    return (
        MarketplaceListing.query
        .filter(
            MarketplaceListing.store_id == store.id,
            MarketplaceListing.external_sku == sku,
            MarketplaceListing.is_active == True,  # noqa: E712
        )
        .order_by(
            MarketplaceListing.warehouse_stock_id.is_(None),
            MarketplaceListing.updated_at.desc(),
            MarketplaceListing.id.desc(),
        )
        .first()
    )


def upsert_governed_marketplace_order_line(
    *,
    store: Store,
    marketplace_order_id: str,
    marketplace_order_item_id: str,
    sku: str,
    quantity: int,
    unit_price: float = 0.0,
    fulfillment_type: str = "FBM",
    status: str = "order",
    carrier: str | None = None,
    tracking_number: str | None = None,
    shipped_at: datetime | None = None,
    ship_to_name: str | None = None,
    ship_to_address: str | None = None,
    ship_to_city: str | None = None,
    ship_to_postcode: str | None = None,
    ship_to_country: str | None = None,
    ship_to_email: str | None = None,
    ship_to_phone: str | None = None,
    marketplace_created_at: datetime | None = None,
    import_source: str | None = None,
    listing: MarketplaceListing | None = None,
) -> dict[str, Any]:
    sku = _text(sku)
    order_id = _text(marketplace_order_id)
    item_id = _text(marketplace_order_item_id) or order_id
    qty = _safe_int(quantity)

    if not order_id or not sku:
        return {
            "success": False,
            "skipped": True,
            "reason": "missing_order_id_or_sku",
            "order_id": order_id,
            "sku": sku,
        }

    listing = listing or _find_listing(store, sku)
    warehouse_stock_id = (
        getattr(listing, "warehouse_stock_id", None)
        if listing
        else None
    )
    key = f"{store.id}:{order_id}:{item_id}:{sku}"

    order = MarketplaceOrder.query.filter_by(idempotency_key=key).first()
    created = False

    if not order:
        order = MarketplaceOrder(
            store_id=store.id,
            marketplace_order_id=order_id,
            marketplace_order_item_id=item_id,
            sku=sku,
            quantity=qty,
            warehouse_stock_id=warehouse_stock_id,
            fulfillment_type=fulfillment_type,
            status=status or "order",
            idempotency_key=key,
        )
        db.session.add(order)
        created = True

    order.store_id = store.id
    order.marketplace_order_id = order_id
    order.marketplace_order_item_id = item_id
    order.sku = sku
    order.quantity = qty
    order.warehouse_stock_id = warehouse_stock_id
    order.fulfillment_type = fulfillment_type
    order.unit_price = _safe_float(unit_price)
    order.line_total = _safe_float(unit_price) * qty
    if created or not getattr(order, "processed_at", None):
        order.status = status or "pending"

    order.carrier = carrier or order.carrier
    order.tracking_number = tracking_number or order.tracking_number
    order.shipped_at = shipped_at or order.shipped_at

    resolved_name = _text(ship_to_name)
    resolved_address = _text(ship_to_address)
    resolved_city = _text(ship_to_city)
    resolved_postcode = _text(ship_to_postcode)
    resolved_country = _text(ship_to_country).upper()[:2]
    resolved_email = _text(ship_to_email)
    resolved_phone = _text(ship_to_phone)

    if resolved_name:
        order.ship_to_name = resolved_name
    if resolved_address:
        order.ship_to_address = resolved_address
    if resolved_city:
        order.ship_to_city = resolved_city
    if resolved_postcode:
        order.ship_to_postcode = resolved_postcode
    if resolved_country:
        order.ship_to_country = resolved_country
    if resolved_email:
        order.ship_to_email = resolved_email
    if resolved_phone:
        order.ship_to_phone = resolved_phone

    order.updated_at = datetime.utcnow()
    db.session.flush()

    resolved_import_source = _text(import_source)
    if marketplace_created_at is not None or resolved_import_source:
        db.session.execute(
            text(
                """
                UPDATE marketplace_orders
                SET marketplace_created_at = COALESCE(:marketplace_created_at, marketplace_created_at),
                    import_source = CASE
                        WHEN :import_source <> '' THEN :import_source
                        ELSE import_source
                    END
                WHERE id = :order_id
                """
            ),
            {
                "marketplace_created_at": marketplace_created_at,
                "import_source": resolved_import_source,
                "order_id": order.id,
            },
        )

    # MCF timing is owned by the MCF lifecycle only.
    # The exact sale is handed to the existing MCF submission authority after
    # stock processing. Only successful Amazon acceptance may arm the one-hour
    # source-marketplace dispatch window.
    mcf_release_event = None

    return {
        "success": True,
        "created": created,
        "order_id": order.marketplace_order_id,
        "item_id": order.marketplace_order_item_id,
        "sku": order.sku,
        "quantity": order.quantity,
        "warehouse_stock_id": order.warehouse_stock_id,
        "idempotency_key": order.idempotency_key,
        "marketplace_created_at": (
            marketplace_created_at.isoformat()
            if marketplace_created_at is not None
            else None
        ),
        "import_source": resolved_import_source or None,
        "listing_matched": bool(listing),
        "mcf_release_event": mcf_release_event,
        "_order_row": order,
    }


def _process_exact_imported_order(
    result: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    order = result.pop("_order_row", None)

    if order is None:
        return {
            "success": False,
            "skipped": True,
            "reason": "exact_order_row_missing",
        }

    from services.governed_order_stock_mutation import process_exact_marketplace_order_line

    return process_exact_marketplace_order_line(order, source=source)


def _write_sync_log(store: Store, *, status: str, message: str, items_synced: int = 0) -> None:
    db.session.add(SyncLog(
        store_id=store.id,
        status=status,
        items_synced=items_synced,
        message=message,
        created_at=datetime.utcnow(),
    ))


def _not_wired(store: Store, marketplace: str) -> dict[str, Any]:
    _write_sync_log(
        store,
        status="success",
        items_synced=0,
        message=f"governed_{marketplace}_order_import skipped: marketplace order reader not yet wired",
    )
    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "marketplace": marketplace,
        "imported": 0,
        "created": 0,
        "skipped": True,
        "reason": "marketplace_order_reader_not_yet_wired",
    }


def _ebay_access_token(store: Store) -> str:
    creds = _store_credentials(store)

    refresh_token = creds.get("refresh_token")
    client_id = os.getenv("EBAY_CLIENT_ID") or creds.get("client_id")
    client_secret = os.getenv("EBAY_CLIENT_SECRET") or creds.get("client_secret")

    if not refresh_token or not client_id or not client_secret:
        raise RuntimeError("missing_ebay_credentials_for_order_import")

    response = requests.post(
        EBAY_TOKEN_URL,
        auth=(client_id, client_secret),
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
        },
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"ebay_token_refresh_failed:{response.status_code}:{response.text[:500]}")

    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("ebay_token_refresh_missing_access_token")

    return token


def _parse_ebay_datetime(value: Any) -> datetime | None:
    text_value = _text(value)
    if not text_value:
        return None
    try:
        return datetime.fromisoformat(text_value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _run_ebay_order_import(store: Store, *, source: str) -> dict[str, Any]:
    access_token = _ebay_access_token(store)
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    response = requests.get(
        EBAY_ORDERS_URL,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        params={"filter": f"creationdate:[{since}..]", "limit": "100"},
        timeout=30,
    )

    if response.status_code >= 400:
        raise RuntimeError(f"ebay_order_import_failed:{response.status_code}:{response.text[:1000]}")

    payload = response.json() or {}
    orders = payload.get("orders") or []

    imported = 0
    created = 0
    skipped = 0
    unmatched = 0
    line_results = []

    for order in orders:
        order_id = _text(order.get("orderId"))
        marketplace_created_at = _parse_ebay_datetime(order.get("creationDate"))
        payment_status = _text(order.get("orderPaymentStatus")).upper()
        fulfillment_status = _text(order.get("orderFulfillmentStatus")).upper()

        if payment_status and payment_status != "PAID":
            skipped += 1
            continue

        status = "pending"
        shipped_at = None

        if fulfillment_status == "FULFILLED":
            status = "pending"
            shipped_at = _parse_ebay_datetime(order.get("lastModifiedDate"))

        instructions = order.get("fulfillmentStartInstructions") or []
        instruction = instructions[0] if instructions else {}
        shipping_step = instruction.get("shippingStep") or {}
        ship_to = shipping_step.get("shipTo") or {}
        contact_address = ship_to.get("contactAddress") or {}

        address_parts = [
            _text(contact_address.get("addressLine1")),
            _text(contact_address.get("addressLine2")),
        ]
        delivery_address = ", ".join(part for part in address_parts if part)

        delivery_name = _text(ship_to.get("fullName"))
        delivery_city = _text(contact_address.get("city"))
        delivery_postcode = _text(contact_address.get("postalCode"))
        delivery_country = _text(contact_address.get("countryCode")).upper()[:2]
        delivery_email = _text(ship_to.get("email"))
        primary_phone = ship_to.get("primaryPhone") or {}
        delivery_phone = _text(primary_phone.get("phoneNumber")) or _text(ship_to.get("phoneNumber"))

        for item in order.get("lineItems") or []:
            sku = _text(item.get("sku")) or _text(item.get("legacyItemId"))
            line_id = _text(item.get("lineItemId")) or f"{order_id}:{sku}"
            qty = _safe_int(item.get("quantity"))

            price_value = 0.0
            price = item.get("lineItemCost") or {}
            if isinstance(price, dict):
                price_value = _safe_float(price.get("value"))

            result = upsert_governed_marketplace_order_line(
                store=store,
                marketplace_order_id=order_id,
                marketplace_order_item_id=line_id,
                sku=sku,
                quantity=qty,
                unit_price=price_value,
                fulfillment_type="FBM",
                status=status,
                shipped_at=shipped_at,
                ship_to_name=delivery_name,
                ship_to_address=delivery_address,
                ship_to_city=delivery_city,
                ship_to_postcode=delivery_postcode,
                ship_to_country=delivery_country,
                ship_to_email=delivery_email,
                ship_to_phone=delivery_phone,
                marketplace_created_at=marketplace_created_at,
                import_source=source,
            )

            processing_result = _process_exact_imported_order(
                result,
                source=f"{source}:ebay_exact_order",
            )
            result["processing"] = processing_result
            line_results.append(result)

            if result.get("success") and not result.get("skipped"):
                imported += 1
                if result.get("created"):
                    created += 1
                if not result.get("warehouse_stock_id"):
                    unmatched += 1
            else:
                skipped += 1

    _write_sync_log(
        store,
        status="success",
        items_synced=imported,
        message=(
            f"governed_ebay_order_import imported={imported} "
            f"created={created} skipped={skipped} unmatched={unmatched} "
            f"source={source}"
        ),
    )
    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "marketplace": "ebay",
        "source": source,
        "orders_seen": len(orders),
        "imported": imported,
        "created": created,
        "skipped": skipped,
        "unmatched": unmatched,
        "results": line_results[:50],
    }


def _amazon_credentials(store: Store) -> dict[str, Any]:
    creds = _store_credentials(store)

    credentials = {
        "refresh_token": (
            creds.get("refresh_token")
            or os.getenv("AMAZON_REFRESH_TOKEN")
            or os.getenv("SP_API_REFRESH_TOKEN")
        ),
        "lwa_app_id": (
            creds.get("lwa_app_id")
            or creds.get("lwa_client_id")
            or creds.get("client_id")
            or os.getenv("AMAZON_LWA_CLIENT_ID")
            or os.getenv("AMAZON_LWA_APP_ID")
            or os.getenv("SP_API_LWA_CLIENT_ID")
        ),
        "lwa_client_secret": (
            creds.get("lwa_client_secret")
            or creds.get("client_secret")
            or os.getenv("AMAZON_LWA_CLIENT_SECRET")
            or os.getenv("SP_API_LWA_CLIENT_SECRET")
        ),
    }

    aws_access_key = (
        creds.get("aws_access_key")
        or creds.get("aws_access_key_id")
        or os.getenv("AMAZON_AWS_ACCESS_KEY_ID")
        or os.getenv("SP_API_AWS_ACCESS_KEY_ID")
    )
    aws_secret_key = (
        creds.get("aws_secret_key")
        or creds.get("aws_secret_access_key")
        or os.getenv("AMAZON_AWS_SECRET_ACCESS_KEY")
        or os.getenv("SP_API_AWS_SECRET_ACCESS_KEY")
    )
    role_arn = (
        creds.get("role_arn")
        or creds.get("aws_user_arn")
        or os.getenv("AMAZON_AWS_ROLE_ARN")
        or os.getenv("SP_API_ROLE_ARN")
        or os.getenv("SP_API_AWS_ROLE_ARN")
    )

    if aws_access_key:
        credentials["aws_access_key"] = aws_access_key
    if aws_secret_key:
        credentials["aws_secret_key"] = aws_secret_key
    if role_arn:
        credentials["role_arn"] = role_arn

    missing = [
        key for key in ("refresh_token", "lwa_app_id", "lwa_client_secret")
        if not credentials.get(key)
    ]
    if missing:
        raise RuntimeError(f"missing_amazon_credentials_for_order_import:{','.join(missing)}")

    return credentials


def _amazon_delivery_fields(client, order_id: str, order_payload: dict[str, Any]) -> tuple[dict[str, str], str | None]:
    shipping_address = order_payload.get("ShippingAddress") or {}
    address_error = None

    if not shipping_address:
        try:
            address_response = client.get_order_address(order_id)
            address_payload = address_response.payload or {}
            shipping_address = address_payload.get("ShippingAddress") or {}
        except Exception as exc:
            address_error = str(exc)

    address_parts = [
        _text(shipping_address.get("AddressLine1")),
        _text(shipping_address.get("AddressLine2")),
        _text(shipping_address.get("AddressLine3")),
    ]

    return {
        "name": _text(shipping_address.get("Name")),
        "address": ", ".join(part for part in address_parts if part),
        "city": _text(shipping_address.get("City")) or _text(shipping_address.get("District")),
        "postcode": _text(shipping_address.get("PostalCode")),
        "country": _text(shipping_address.get("CountryCode")).upper()[:2],
        "phone": _text(shipping_address.get("Phone")),
    }, address_error


def _hydrate_existing_amazon_rows(
    rows: list[MarketplaceOrder],
    *,
    delivery: dict[str, str],
    shipped_at: datetime | None,
) -> None:
    for row in rows:
        if delivery.get("name"):
            row.ship_to_name = delivery["name"]
        if delivery.get("address"):
            row.ship_to_address = delivery["address"]
        if delivery.get("city"):
            row.ship_to_city = delivery["city"]
        if delivery.get("postcode"):
            row.ship_to_postcode = delivery["postcode"]
        if delivery.get("country"):
            row.ship_to_country = delivery["country"]
        if delivery.get("phone"):
            row.ship_to_phone = delivery["phone"]
        if shipped_at is not None:
            row.shipped_at = shipped_at
        row.updated_at = datetime.utcnow()

    db.session.flush()


def _run_amazon_order_import(store: Store, *, source: str) -> dict[str, Any]:
    from sp_api.api import Orders
    from sp_api.base import Marketplaces
    from models import MarketplaceOrder

    creds = _store_credentials(store)
    marketplace_id = creds.get("marketplace_id") or "A1F83G8C2ARO7P"

    client = Orders(
        marketplace=Marketplaces.UK,
        credentials=_amazon_credentials(store),
    )

    created_after = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace("+00:00", "Z")

    response = client.get_orders(
        CreatedAfter=created_after,
        MarketplaceIds=[marketplace_id],
    )

    payload = response.payload or {}
    orders = payload.get("Orders") or []

    imported = 0
    created = 0
    skipped = 0
    unmatched = 0
    existing_skipped = 0
    existing_hydrated = 0
    item_read_attempts = 0
    address_read_attempts = 0
    address_read_failures = 0
    line_results = []

    allowed_statuses = {"UNSHIPPED", "PARTIALLYSHIPPED", "SHIPPED", "PENDING"}

    for order in orders:
        order_id = _text(order.get("AmazonOrderId"))
        marketplace_created_at = _parse_ebay_datetime(order.get("PurchaseDate"))
        order_status = _text(order.get("OrderStatus")).upper()
        fulfillment_channel = _text(order.get("FulfillmentChannel")).upper()

        if not order_id:
            skipped += 1
            continue

        if order_status not in allowed_statuses:
            skipped += 1
            continue

        shipped_at = None
        if order_status == "SHIPPED":
            shipped_at = (
                _parse_ebay_datetime(order.get("LastUpdateDate"))
                or _parse_ebay_datetime(order.get("LatestShipDate"))
                or datetime.utcnow()
            )

        delivery = {
            "name": "",
            "address": "",
            "city": "",
            "postcode": "",
            "country": "",
            "phone": "",
        }
        address_error = None

        if fulfillment_channel != "AFN":
            address_read_attempts += 1
            delivery, address_error = _amazon_delivery_fields(client, order_id, order)
            if address_error:
                address_read_failures += 1

        existing_rows = (
            MarketplaceOrder.query
            .filter(MarketplaceOrder.store_id == store.id)
            .filter(MarketplaceOrder.marketplace_order_id == order_id)
            .all()
        )

        if existing_rows:
            _hydrate_existing_amazon_rows(
                existing_rows,
                delivery=delivery,
                shipped_at=shipped_at,
            )
            existing_hydrated += 1
            existing_skipped += 1
            skipped += 1
            line_results.append({
                "success": True,
                "skipped": True,
                "reason": "existing_order_hydrated_stock_processing_skipped",
                "order_id": order_id,
                "rows_hydrated": len(existing_rows),
                "address_hydrated": bool(delivery.get("postcode") or delivery.get("address")),
                "address_error": address_error,
                "shipped": shipped_at is not None,
            })
            continue

        try:
            item_read_attempts += 1
            items_response = client.get_order_items(order_id)
            items_payload = items_response.payload or {}
            items = items_payload.get("OrderItems") or []
        except Exception as exc:
            skipped += 1
            line_results.append({
                "success": False,
                "skipped": True,
                "reason": "amazon_order_items_read_failed",
                "order_id": order_id,
                "error": str(exc),
            })
            continue

        fulfillment_type = "FBA" if fulfillment_channel == "AFN" else "FBM"

        for item in items:
            sku = _text(item.get("SellerSKU"))
            item_id = _text(item.get("OrderItemId")) or f"{order_id}:{sku}"
            qty = _safe_int(item.get("QuantityOrdered"))

            price_value = 0.0
            item_price = item.get("ItemPrice") or {}
            if isinstance(item_price, dict):
                price_value = _safe_float(item_price.get("Amount"))

            result = upsert_governed_marketplace_order_line(
                store=store,
                marketplace_order_id=order_id,
                marketplace_order_item_id=item_id,
                sku=sku,
                quantity=qty,
                unit_price=price_value,
                fulfillment_type=fulfillment_type,
                status="pending",
                shipped_at=shipped_at,
                ship_to_name=delivery.get("name"),
                ship_to_address=delivery.get("address"),
                ship_to_city=delivery.get("city"),
                ship_to_postcode=delivery.get("postcode"),
                ship_to_country=delivery.get("country"),
                ship_to_phone=delivery.get("phone"),
                marketplace_created_at=marketplace_created_at,
                import_source=source,
            )

            if result.get("created"):
                processing_result = _process_exact_imported_order(
                    result,
                    source=f"{source}:amazon_exact_order",
                )
                result["processing"] = processing_result
            else:
                result.pop("_order_row", None)
                result["processing"] = {
                    "success": True,
                    "skipped": True,
                    "reason": "existing_line_stock_processing_skipped",
                }

            if address_error:
                result["address_error"] = address_error
            line_results.append(result)

            if result.get("success") and not result.get("skipped"):
                imported += 1
                if result.get("created"):
                    created += 1
                if not result.get("warehouse_stock_id"):
                    unmatched += 1
            else:
                skipped += 1

    _write_sync_log(
        store,
        status="success",
        items_synced=imported,
        message=(
            f"governed_amazon_order_import imported={imported} "
            f"created={created} skipped={skipped} existing_skipped={existing_skipped} "
            f"existing_hydrated={existing_hydrated} item_read_attempts={item_read_attempts} "
            f"address_read_attempts={address_read_attempts} address_read_failures={address_read_failures} "
            f"unmatched={unmatched} window_hours=24 source={source}"
        ),
    )
    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "marketplace": "amazon",
        "source": source,
        "window_hours": 24,
        "orders_seen": len(orders),
        "imported": imported,
        "created": created,
        "skipped": skipped,
        "existing_skipped": existing_skipped,
        "existing_hydrated": existing_hydrated,
        "item_read_attempts": item_read_attempts,
        "address_read_attempts": address_read_attempts,
        "address_read_failures": address_read_failures,
        "unmatched": unmatched,
        "results": line_results[:50],
    }


def run_governed_marketplace_order_import(store_id=None, source: str = "governed_marketplace_order_import") -> dict[str, Any]:
    stores = (
        Store.query
        .filter(Store.is_active == True)  # noqa: E712
        .filter(Store.store_mode == "live")
        .order_by(Store.id)
        .all()
    )

    if store_id:
        stores = [s for s in stores if int(s.id) == int(store_id)]

    results = []

    for store in stores:
        platform = str(store.platform or "").strip().lower()

        if "amazon" in platform:
            try:
                order_import = _run_amazon_order_import(store, source=source)
            except Exception as exc:
                db.session.rollback()
                _write_sync_log(
                    store,
                    status="error",
                    items_synced=0,
                    message=f"governed_amazon_order_import failed: {exc}",
                )
                db.session.commit()
                order_import = {
                    "success": False,
                    "governed": True,
                    "marketplace": "amazon",
                    "error": str(exc),
                }

            results.append({
                "store_id": store.id,
                "store": store.name,
                "platform": store.platform,
                "order_import": order_import,
            })
            continue

        if "ebay" in platform:
            try:
                order_import = _run_ebay_order_import(store, source=source)
            except Exception as exc:
                db.session.rollback()
                _write_sync_log(
                    store,
                    status="error",
                    items_synced=0,
                    message=f"governed_ebay_order_import failed: {exc}",
                )
                db.session.commit()
                order_import = {
                    "success": False,
                    "governed": True,
                    "marketplace": "ebay",
                    "error": str(exc),
                }

            results.append({
                "store_id": store.id,
                "store": store.name,
                "platform": store.platform,
                "order_import": order_import,
            })
            continue

    failed_stores = [
        item
        for item in results
        if not bool(item.get("order_import", {}).get("success"))
    ]
    overall_success = not failed_stores

    return {
        "success": overall_success,
        "status": "success" if overall_success else "partial",
        "governed": True,
        "source": source,
        "failed_store_count": len(failed_stores),
        "results": results,
    }