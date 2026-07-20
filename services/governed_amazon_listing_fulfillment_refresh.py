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

from sp_api.api import ListingsItems
from sp_api.base import Marketplaces

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
