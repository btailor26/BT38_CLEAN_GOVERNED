"""
BT38 GOVERNED AMAZON INVENTORY IMPORT

Single governed execution path.
"""

from datetime import datetime

from app import db
from models import (
    Store,
    MarketplaceListing,
    Warehouse,
    WarehouseStock,
    AmazonFBAInventory,
    SyncLog,
)

from backend.adapters.amazon_sp_api_adapter import (
    AmazonSPAPIAdapter,
)


def _find_or_create_warehouse_stock(sku, qty, channel, asin=None, fnsku=None):

    default_warehouse = Warehouse.get_default()

    stock = (
        db.session.query(WarehouseStock)
        .filter(
            WarehouseStock.warehouse_id == default_warehouse.id,
            WarehouseStock.sku == sku,
            WarehouseStock.is_deleted == False,  # noqa: E712
        )
        .first()
    )

    if not stock:
        stock = WarehouseStock(
            warehouse_id=default_warehouse.id,
            sku=sku,
            product_name=f"Amazon SKU {sku}",
            location=(
                "Amazon FBA"
                if channel in ("AFN", "FBA")
                else "Warehouse"
            ),
            barcode=fnsku,
            available_quantity=(
                qty
                if channel in ("MFN", "FBM", "MERCHANT")
                else 0
            ),
            is_active=True,
            is_deleted=False,
        )
        db.session.add(stock)
        db.session.flush()

    if asin and not stock.product_name:
        stock.product_name = f"Amazon ASIN {asin}"

    if fnsku and not stock.barcode:
        stock.barcode = fnsku

    if channel in ("MFN", "FBM", "MERCHANT"):
        stock.available_quantity = qty

    stock.last_sync_at = datetime.utcnow()

    return stock


def _find_or_create_marketplace_listing(store, stock, sku, channel, qty, asin=None, fnsku=None):

    external_listing_id = asin or fnsku or sku

    listing = (
        db.session.query(MarketplaceListing)
        .filter(
            MarketplaceListing.store_id == store.id,
            MarketplaceListing.external_listing_id == external_listing_id,
            MarketplaceListing.external_sku == sku,
        )
        .first()
    )

    if not listing:
        listing = MarketplaceListing(
            store_id=store.id,
            warehouse_stock_id=stock.id,
            external_listing_id=external_listing_id,
            external_sku=sku,
            title=f"Amazon SKU {sku}",
            price=0.0,
            currency="GBP",
            is_active=True,
        )
        db.session.add(listing)

    listing.warehouse_stock_id = stock.id
    listing.asin = asin
    listing.fnsku = fnsku
    listing.amazon_fulfillment_channel = channel
    listing.last_marketplace_qty = qty
    listing.last_synced_at = datetime.utcnow()
    listing.is_active = True

    return listing


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

        adapter = AmazonSPAPIAdapter(store)

        inventory = adapter.get_inventory()

        imported = 0
        linked_listings = 0
        linked_warehouse_rows = 0

        for row in inventory:

            sku = (row.get("seller_sku") or "").strip()

            if not sku:
                continue

            qty = int(row.get("available_quantity") or 0)

            channel = (
                row.get("fulfillment_channel")
                or "AFN"
            ).upper()

            asin = row.get("asin")
            fnsku = row.get("fnsku")

            inv = (
                db.session.query(AmazonFBAInventory)
                .filter(
                    AmazonFBAInventory.seller_sku == sku
                )
                .first()
            )

            if not inv:
                inv = AmazonFBAInventory(
                    seller_sku=sku,
                )
                db.session.add(inv)

            inv.available_quantity = qty
            inv.asin = asin
            inv.fnsku = fnsku
            inv.updated_at = datetime.utcnow()

            stock = _find_or_create_warehouse_stock(
                sku=sku,
                qty=qty,
                channel=channel,
                asin=asin,
                fnsku=fnsku,
            )
            linked_warehouse_rows += 1

            listing = _find_or_create_marketplace_listing(
                store=store,
                stock=stock,
                sku=sku,
                channel=channel,
                qty=qty,
                asin=asin,
                fnsku=fnsku,
            )
            linked_listings += 1

            if listing:
                listing.last_marketplace_qty = qty
                listing.amazon_fulfillment_channel = channel

            imported += 1

        db.session.add(SyncLog(
            store_id=store.id,
            status="success",
            items_synced=imported,
            message=(
                f"governed_amazon_inventory_import "
                f"imported={imported} "
                f"warehouse_rows={linked_warehouse_rows} "
                f"listings={linked_listings}"
            ),
            created_at=datetime.utcnow(),
        ))

        results.append({
            "store_id": store.id,
            "store": store.name,
            "imported": imported,
            "warehouse_rows": linked_warehouse_rows,
            "listings": linked_listings,
        })

    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "results": results,
    }
