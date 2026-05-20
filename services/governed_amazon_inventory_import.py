"""
BT38 GOVERNED AMAZON INVENTORY IMPORT

Clean governed import path.

Rules:
- No old queue workers
- No legacy orchestration
- No restore patching
- Warehouse truth only
- AFN/FBA read-only
- MFN/FBM writable
"""

from datetime import datetime

from app import db
from models import (
    Store,
    MarketplaceListing,
    WarehouseStock,
    AmazonFBAInventory,
    SyncLog,
)

from backend.services.amazon_service import AmazonService


def run_governed_amazon_inventory_import(store_id=None):

    query = db.session.query(Store).filter(
        Store.platform.ilike("%amazon%"),
        Store.is_active == True,  # noqa: E712
    )

    if store_id:
        query = query.filter(Store.id == store_id)

    stores = query.all()

    results = []

    for store in stores:

        service = AmazonService(store)

        inventory = service.get_fba_inventory()

        imported = 0

        for row in inventory:

            sku = (
                row.get("seller_sku")
                or row.get("sku")
                or ""
            ).strip()

            if not sku:
                continue

            qty = int(row.get("available_quantity") or 0)

            channel = (
                row.get("fulfillment_channel")
                or row.get("channel")
                or "AFN"
            ).upper()

            inv = (
                db.session.query(AmazonFBAInventory)
                .filter(AmazonFBAInventory.seller_sku == sku)
                .first()
            )

            if not inv:
                inv = AmazonFBAInventory(
                    seller_sku=sku,
                )
                db.session.add(inv)

            inv.available_quantity = qty
            inv.updated_at = datetime.utcnow()

            listings = (
                db.session.query(MarketplaceListing)
                .filter(
                    MarketplaceListing.external_sku == sku
                )
                .all()
            )

            for listing in listings:

                listing.last_marketplace_qty = qty
                listing.normalized_amazon_fulfillment_channel = channel

                if listing.warehouse_stock_id:

                    ws = db.session.get(
                        WarehouseStock,
                        listing.warehouse_stock_id
                    )

                    if ws:

                        if channel in ("MFN", "FBM", "MERCHANT"):
                            ws.available_quantity = qty

                        ws.last_synced_at = datetime.utcnow()

            imported += 1

        db.session.add(SyncLog(
            store_id=store.id,
            status="success",
            items_synced=imported,
            message=f"governed_amazon_inventory_import imported={imported}",
            created_at=datetime.utcnow(),
        ))

        results.append({
            "store_id": store.id,
            "store": store.name,
            "imported": imported,
        })

    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "results": results,
    }
