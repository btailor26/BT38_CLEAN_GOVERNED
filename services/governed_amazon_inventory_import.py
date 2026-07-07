"""
BT38 GOVERNED AMAZON FBA INVENTORY IMPORT

Single responsibility:
- Import Amazon FBA/AFN truth into AmazonFBAInventory only.

Rules:
- FBA/AFN is read-only.
- Do not create MarketplaceListing rows.
- Do not create WarehouseStock rows.
- Do not mutate warehouse stock.
- Do not mutate groups.
- Do not delete or archive existing listings.
- Existing listing/warehouse/transfer authority remains separate.
"""

from datetime import datetime

from app import db
from models import Store, MarketplaceListing, AmazonFBAInventory, SyncLog
from services.runtime_status_writer import set_store_runtime_status
from backend.adapters.amazon_sp_api_adapter import AmazonSPAPIAdapter


def _clean(value):
    return str(value or "").strip()


def _normalise_channel(value):
    channel = _clean(value).upper()

    if channel in {"FBA", "AFN", "AMAZON", "AMAZON_FULFILLED"}:
        return "AFN"

    if channel in {"FBM", "MFN", "MERCHANT", "MERCHANT_FULFILLED"}:
        return "MFN"

    return "UNKNOWN"


def _find_existing_listing(store, sku, asin=None, fnsku=None):
    sku = _clean(sku)
    asin = _clean(asin)
    fnsku = _clean(fnsku)

    query = (
        db.session.query(MarketplaceListing)
        .filter(MarketplaceListing.store_id == store.id)
        .filter(MarketplaceListing.is_active == True)  # noqa: E712
    )

    if sku:
        listing = query.filter(MarketplaceListing.external_sku == sku).first()
        if listing:
            return listing

    if fnsku:
        listing = query.filter(
            (MarketplaceListing.external_listing_id == fnsku)
            | (MarketplaceListing.fnsku == fnsku)
        ).first()
        if listing:
            return listing

    if asin:
        listing = query.filter(MarketplaceListing.asin == asin).first()
        if listing:
            return listing

    return None


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
        linked_existing_listings = 0
        unlinked_fba_truth_rows = 0
        afn_rows = 0
        mfn_rows_seen = 0
        unknown_channel_rows = 0

        for row in inventory:
            sku = _clean(row.get("seller_sku"))
            if not sku:
                continue

            asin = _clean(row.get("asin"))
            fnsku = _clean(row.get("fnsku"))
            channel = _normalise_channel(row.get("fulfillment_channel"))

            qty = int(row.get("available_quantity") or 0)
            reserved = int(row.get("reserved_quantity") or 0)
            inbound = int(row.get("inbound_quantity") or 0)

            inv = (
                db.session.query(AmazonFBAInventory)
                .filter(AmazonFBAInventory.seller_sku == sku)
                .first()
            )

            if not inv:
                inv = AmazonFBAInventory(seller_sku=sku)
                db.session.add(inv)

            if hasattr(inv, "store_id"):
                inv.store_id = store.id

            inv.available_quantity = qty
            inv.reserved_quantity = reserved
            inv.inbound_quantity = inbound
            inv.asin = asin or None
            inv.fnsku = fnsku or None
            inv.is_archived = False
            inv.last_synced_at = datetime.utcnow()
            inv.last_sync_status = "success"
            inv.updated_at = datetime.utcnow()

            if channel == "AFN":
                afn_rows += 1
            elif channel == "MFN":
                mfn_rows_seen += 1
            else:
                unknown_channel_rows += 1

            existing_listing = _find_existing_listing(
                store,
                sku=sku,
                asin=asin,
                fnsku=fnsku,
            )

            if existing_listing:
                linked_existing_listings += 1

                # Visibility/cache only. No warehouse mutation.
                if hasattr(existing_listing, "asin") and asin:
                    existing_listing.asin = asin
                if hasattr(existing_listing, "fnsku") and fnsku:
                    existing_listing.fnsku = fnsku
                if hasattr(existing_listing, "last_marketplace_qty"):
                    existing_listing.last_marketplace_qty = qty
                if hasattr(existing_listing, "last_synced_at"):
                    existing_listing.last_synced_at = datetime.utcnow()
                if hasattr(existing_listing, "updated_at"):
                    existing_listing.updated_at = datetime.utcnow()
            else:
                unlinked_fba_truth_rows += 1

            imported += 1

        set_store_runtime_status(store, "idle", last_sync=True)
        db.session.add(SyncLog(
            store_id=store.id,
            status="success",
            items_synced=imported,
            message=(
                f"governed_amazon_inventory_import "
                f"imported={imported} "
                f"fba_truth_rows={imported} "
                f"linked_existing_listings={linked_existing_listings} "
                f"unlinked_fba_truth_rows={unlinked_fba_truth_rows} "
                f"afn_rows={afn_rows} "
                f"mfn_rows_seen={mfn_rows_seen} "
                f"unknown_channel_rows={unknown_channel_rows}"
            ),
            created_at=datetime.utcnow(),
        ))

        results.append({
            "store_id": store.id,
            "store": store.name,
            "imported": imported,
            "fba_truth_rows": imported,
            "linked_existing_listings": linked_existing_listings,
            "unlinked_fba_truth_rows": unlinked_fba_truth_rows,
            "afn_rows": afn_rows,
            "mfn_rows_seen": mfn_rows_seen,
            "unknown_channel_rows": unknown_channel_rows,
            "created_marketplace_listings": 0,
            "created_warehouse_stock": 0,
            "warehouse_mutation": False,
        })

    db.session.commit()

    return {
        "success": True,
        "governed": True,
        "truth_source": "AmazonFBAInventory",
        "warehouse_mutation": False,
        "created_marketplace_listings": 0,
        "created_warehouse_stock": 0,
        "results": results,
    }
