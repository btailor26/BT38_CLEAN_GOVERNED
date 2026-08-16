"""
Governed Amazon listing fulfillment refresh.

Single responsibility:
- Read Amazon ListingsItems fulfillmentAvailability.
- Map DEFAULT -> MFN.
- Map AMAZON* -> AFN.
- Call refresh_governed_listing_from_snapshot().

Not allowed:
- No warehouse stock mutation.
- No FBA inventory mutation.
- No group mutation.
- No marketplace push.
- No stock transfer.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from sp_api.api import ListingsItems, Notifications
from sp_api.base import Marketplaces
from sp_api.base.notifications import NotificationType

from extensions import db
from models import Store, SyncLog
from services.runtime_status_writer import set_store_runtime_status
from services.governed_listing_refresh import refresh_governed_listing_from_snapshot


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _marketplace_for_store(store: Store):
    raw = store.api_key or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}

    marketplace_id = (
        raw.get("marketplace_id")
        or os.getenv("AMAZON_MARKETPLACE_ID")
        or "A1F83G8C2ARO7P"
    )

    marketplace_map = {
        "A1F83G8C2ARO7P": Marketplaces.UK,
        "A13V1IB3VIYZZH": Marketplaces.DE,
        "A1RKKUPIHCS9HS": Marketplaces.ES,
        "APJ6JRA9NG5V4": Marketplaces.IT,
        "A1PA6795UKMFR9": Marketplaces.FR,
    }

    return marketplace_map.get(marketplace_id, Marketplaces.UK), marketplace_id, raw


def _credentials(raw: dict[str, Any]) -> dict[str, Any]:
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

    return credentials


def _normalise_channel(code: Any) -> str:
    channel = _clean(code).upper()

    if channel == "DEFAULT":
        return "MFN"

    if "AMAZON" in channel or channel in {"AFN", "FBA"}:
        return "AFN"

    if channel in {"MFN", "FBM", "MERCHANT", "MERCHANT_FULFILLED"}:
        return "MFN"

    return ""


def _extract_listing_snapshot(item: dict[str, Any]) -> dict[str, Any] | None:
    sku = _clean(item.get("sku"))
    if not sku:
        return None

    summaries = item.get("summaries") or []
    summary = summaries[0] if summaries else {}

    fulfillment_rows = item.get("fulfillmentAvailability") or []
    fulfillment = fulfillment_rows[0] if fulfillment_rows else {}

    raw_channel = fulfillment.get("fulfillmentChannelCode")
    channel = _normalise_channel(raw_channel)

    if not channel:
        return None

    asin = _clean(summary.get("asin"))
    title = _clean(summary.get("itemName")) or sku

    return {
        "sku": sku,
        "external_listing_id": asin or sku,
        "asin": asin,
        "title": title,
        "amazon_fulfillment_channel": channel,
        "raw_fulfillment_channel": raw_channel,
    }


def refresh_governed_amazon_listing_exact(
    *,
    store_id: int,
    seller_sku: str,
    actor: str = "amazon_listing_event",
) -> dict[str, Any]:
    """Fetch one exact Amazon listing and use the existing canonical writer.

    This is not a second importer or writer. The Listings Items snapshot is
    handed directly to refresh_governed_listing_from_snapshot().
    """
    seller_sku = _clean(seller_sku)

    if not seller_sku:
        return {
            "success": False,
            "governed": True,
            "targeted": True,
            "reason": "missing_seller_sku",
        }

    store = (
        Store.query
        .filter(
            Store.id == int(store_id),
            Store.platform.ilike("%amazon%"),
            Store.is_active == True,  # noqa: E712
        )
        .first()
    )

    if store is None:
        return {
            "success": False,
            "governed": True,
            "targeted": True,
            "reason": "amazon_store_not_found",
            "store_id": store_id,
            "seller_sku": seller_sku,
        }

    marketplace, marketplace_id, raw = _marketplace_for_store(store)
    seller_id = _clean(
        raw.get("seller_id")
        or os.getenv("AMAZON_SELLER_ID")
    )

    if not seller_id:
        return {
            "success": False,
            "governed": True,
            "targeted": True,
            "reason": "missing_seller_id",
            "store_id": store.id,
            "seller_sku": seller_sku,
        }

    client = ListingsItems(
        marketplace=marketplace,
        credentials=_credentials(raw),
    )

    response = client.get_listings_item(
        sellerId=seller_id,
        sku=seller_sku,
        marketplaceIds=[marketplace_id],
        includedData=[
            "summaries",
            "fulfillmentAvailability",
        ],
    )

    item = dict(response.payload or {})
    item.setdefault("sku", seller_sku)

    snapshot = _extract_listing_snapshot(item)

    if not snapshot:
        return {
            "success": False,
            "governed": True,
            "targeted": True,
            "reason": "amazon_listing_snapshot_unresolved",
            "store_id": store.id,
            "seller_sku": seller_sku,
        }

    result = refresh_governed_listing_from_snapshot(
        store_id=store.id,
        sku=snapshot["sku"],
        external_listing_id=snapshot["external_listing_id"],
        amazon_fulfillment_channel=(
            snapshot["amazon_fulfillment_channel"]
        ),
        title=snapshot["title"],
        actor=actor,
    )

    return {
        "success": bool(result.get("success")),
        "governed": True,
        "targeted": True,
        "store_id": store.id,
        "seller_sku": seller_sku,
        "snapshot": snapshot,
        "result": result,
    }


AMAZON_LISTING_NOTIFICATION_TYPES = {
    "LISTINGS_ITEM_STATUS_CHANGE",
    "LISTINGS_ITEM_MFN_QUANTITY_CHANGE",
}


def ensure_governed_amazon_listing_notification_subscriptions(
    *,
    store_id: int,
) -> dict[str, Any]:
    """Explicitly reconcile Amazon listing notifications on the existing destination.

    This is a one-shot setup action for deployment/testing. It never runs at
    application startup and does not create another destination, importer,
    scheduler or marketplace write path.
    """
    store = (
        Store.query
        .filter(
            Store.id == int(store_id),
            Store.platform.ilike("%amazon%"),
            Store.is_active == True,  # noqa: E712
        )
        .first()
    )
    if store is None:
        return {
            "success": False,
            "governed": True,
            "reason": "amazon_store_not_found",
            "store_id": int(store_id),
        }

    marketplace, _marketplace_id, raw = _marketplace_for_store(store)
    client = Notifications(
        marketplace=marketplace,
        credentials=_credentials(raw),
    )
    destination_payload = client.get_destinations().payload or {}
    destinations = list(destination_payload.get("destinations") or [])
    destination = next(
        (
            row for row in destinations
            if str(row.get("resourceSpecification", {}).get("sqs", {}).get("arn") or "").strip()
        ),
        destinations[0] if destinations else None,
    )
    destination_id = str((destination or {}).get("destinationId") or "").strip()
    if not destination_id:
        return {
            "success": False,
            "governed": True,
            "reason": "amazon_existing_notification_destination_missing",
            "store_id": int(store_id),
            "destination_created": False,
        }

    results = []
    for event_type in sorted(AMAZON_LISTING_NOTIFICATION_TYPES):
        notification_type = NotificationType[event_type]
        existing = None
        try:
            existing = client.get_subscription(notification_type).payload or {}
        except Exception as exc:
            message = str(exc)
            if "404" not in message and "NotFound" not in message and "not found" not in message.lower():
                raise

        subscription_id = str((existing or {}).get("subscriptionId") or "").strip()
        created = False
        if not subscription_id:
            created_payload = client.create_subscription(
                notification_type,
                destination_id=destination_id,
            ).payload or {}
            subscription_id = str(created_payload.get("subscriptionId") or "").strip()
            created = True

        results.append({
            "notification_type": event_type,
            "subscription_id": subscription_id or None,
            "created": created,
        })

    return {
        "success": all(row.get("subscription_id") for row in results),
        "governed": True,
        "store_id": int(store_id),
        "destination_id": destination_id,
        "destination_created": False,
        "subscriptions": results,
    }


def recover_governed_amazon_listing_from_notification(
    *,
    store_id: int | None,
    event_type: str,
    seller_sku: str | None,
) -> dict[str, Any]:
    """Use the existing Amazon listing refresh path for one notification.

    Marketplace-specific event recognition, exact Listings Items fetching and
    bounded store recovery remain inside the Amazon listing service.

    Both exact and recovery paths continue to use
    refresh_governed_listing_from_snapshot() as the sole listing writer.
    """
    event_type = _clean(event_type).upper()
    seller_sku = _clean(seller_sku)

    if event_type not in AMAZON_LISTING_NOTIFICATION_TYPES:
        return {
            "success": False,
            "governed": True,
            "applicable": False,
            "targeted": True,
            "reason": "not_amazon_listing_notification",
            "event_type": event_type,
        }

    if store_id is None:
        return {
            "success": False,
            "governed": True,
            "applicable": True,
            "targeted": True,
            "reason": "amazon_listing_event_store_missing",
            "event_type": event_type,
            "seller_sku": seller_sku or None,
        }

    if not seller_sku:
        return {
            "success": False,
            "governed": True,
            "applicable": True,
            "targeted": True,
            "reason": "amazon_listing_event_sku_missing",
            "event_type": event_type,
            "store_id": int(store_id),
        }

    exact_result = None
    recovery_result = None

    try:
        exact_result = refresh_governed_amazon_listing_exact(
            store_id=int(store_id),
            seller_sku=seller_sku,
            actor=f"webhook_{event_type}",
        )
    except Exception as exc:
        exact_result = {
            "success": False,
            "governed": True,
            "targeted": True,
            "reason": "amazon_exact_listing_fetch_failed",
            "error": str(exc),
            "store_id": int(store_id),
            "seller_sku": seller_sku,
        }

    if bool((exact_result or {}).get("success")):
        return {
            "success": True,
            "governed": True,
            "applicable": True,
            "targeted": True,
            "store_id": int(store_id),
            "seller_sku": seller_sku,
            "event_type": event_type,
            "exact": exact_result,
            "recovery": None,
        }

    # One bounded exact-store recovery through the existing manual listing
    # refresh. This uses the same canonical listing writer and cannot loop.
    try:
        recovery_result = run_governed_amazon_listing_fulfillment_refresh(
            store_id=int(store_id),
        )
    except Exception as exc:
        recovery_result = {
            "success": False,
            "governed": True,
            "reason": "amazon_listing_manual_recovery_failed",
            "error": str(exc),
            "store_id": int(store_id),
        }

    return {
        "success": bool((recovery_result or {}).get("success")),
        "governed": True,
        "applicable": True,
        "targeted": False,
        "bounded_recovery": True,
        "store_id": int(store_id),
        "seller_sku": seller_sku,
        "event_type": event_type,
        "exact": exact_result,
        "recovery": recovery_result,
    }


def run_governed_amazon_listing_fulfillment_refresh(store_id=None, max_pages: int | None = None) -> dict[str, Any]:
    query = Store.query.filter(
        Store.platform.ilike("%amazon%"),
        Store.is_active == True,  # noqa: E712
    )

    if store_id:
        query = query.filter(Store.id == int(store_id))

    stores = query.order_by(Store.id).all()
    results = []

    for store in stores:
        marketplace, marketplace_id, raw = _marketplace_for_store(store)
        seller_id = _clean(raw.get("seller_id") or os.getenv("AMAZON_SELLER_ID"))

        if not seller_id:
            results.append({
                "store_id": store.id,
                "store": store.name,
                "success": False,
                "reason": "missing_seller_id",
            })
            continue

        client = ListingsItems(
            marketplace=marketplace,
            credentials=_credentials(raw),
        )

        page_token = None
        pages = 0
        refreshed = 0
        skipped = 0
        afn = 0
        mfn = 0

        while True:
            pages += 1

            params = {
                "sellerId": seller_id,
                "marketplaceIds": [marketplace_id],
                "includedData": ["summaries", "fulfillmentAvailability"],
                "pageSize": 20,
                # Listing recovery must discover newly-created listings first.
                # Amazon otherwise defaults this search to lastUpdatedDate,
                # which can repeatedly surface an older recently-updated slice.
                "sortBy": "createdDate",
                "sortOrder": "DESC",
            }

            if page_token:
                params["pageToken"] = page_token

            response = client.search_listings_items(**params)
            payload = response.payload or {}

            for item in payload.get("items", []) or []:
                snapshot = _extract_listing_snapshot(item)

                if not snapshot:
                    skipped += 1
                    continue

                result = refresh_governed_listing_from_snapshot(
                    store_id=store.id,
                    sku=snapshot["sku"],
                    external_listing_id=snapshot["external_listing_id"],
                    amazon_fulfillment_channel=snapshot["amazon_fulfillment_channel"],
                    title=snapshot["title"],
                    actor="governed_amazon_listing_fulfillment_refresh",
                )

                if result.get("success"):
                    refreshed += 1
                    if snapshot["amazon_fulfillment_channel"] == "AFN":
                        afn += 1
                    elif snapshot["amazon_fulfillment_channel"] == "MFN":
                        mfn += 1
                else:
                    skipped += 1

            pagination = payload.get("pagination") or {}
            page_token = pagination.get("nextToken") or payload.get("nextToken")

            if not page_token:
                break

            if max_pages is not None and pages >= int(max_pages):
                break

        set_store_runtime_status(store, "idle", last_sync=True)
        db.session.add(SyncLog(
            store_id=store.id,
            status="success",
            items_synced=refreshed,
            message=(
                "governed_amazon_listing_fulfillment_refresh "
                f"refreshed={refreshed} skipped={skipped} "
                f"afn={afn} mfn={mfn} pages={pages}"
            ),
            created_at=datetime.utcnow(),
        ))
        db.session.commit()

        results.append({
            "store_id": store.id,
            "store": store.name,
            "success": True,
            "refreshed": refreshed,
            "skipped": skipped,
            "afn": afn,
            "mfn": mfn,
            "pages": pages,
        })

    return {
        "success": True,
        "governed": True,
        "marketplace": "amazon",
        "source": "ListingsItems.fulfillmentAvailability",
        "results": results,
    }
