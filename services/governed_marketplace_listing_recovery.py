"""Marketplace listing recovery dispatcher.

This module routes missing-listing notifications only.

It does not:
- construct MarketplaceListing rows;
- mutate WarehouseStock directly;
- create a second importer;
- push marketplace quantity;
- run recurring scans.

Each marketplace continues through its existing importer and writer.
"""

from __future__ import annotations

from typing import Any


def recover_governed_marketplace_listing(
    *,
    marketplace: str,
    store_id: int | None,
    event_type: str,
    seller_sku: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    marketplace = str(marketplace or "").strip().lower()

    if marketplace == "amazon":
        from services.governed_amazon_listing_fulfillment_refresh import (
            recover_governed_amazon_listing_from_notification,
        )

        return recover_governed_amazon_listing_from_notification(
            store_id=store_id,
            event_type=event_type,
            seller_sku=seller_sku,
        )

    if marketplace == "ebay":
        from services.governed_ebay_inventory_import import (
            recover_governed_ebay_listing_from_notification,
        )

        return recover_governed_ebay_listing_from_notification(
            store_id=store_id,
            event_type=event_type,
            payload=payload,
        )

    return {
        "success": False,
        "governed": True,
        "applicable": False,
        "reason": "unsupported_marketplace_listing_recovery",
        "marketplace": marketplace,
        "event_type": str(event_type or ""),
    }
