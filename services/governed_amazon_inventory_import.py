"""
BT38 GOVERNED AMAZON FBA INVENTORY IMPORT

Single responsibility:
- Import Amazon FBA/AFN truth into AmazonFBAInventory only.

Rules:
- FBA/AFN is read-only.
- Do not create MarketplaceListing rows.
- Do not create WarehouseStock rows.
- Do not mutate warehouse stock quantities.
- Do not delete or archive existing listings.
- Preserve an already-governed warehouse/group relationship when Amazon catalogue
  identity changes or an active listing replaces an older inactive row.
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


def _identity_query(store, sku, asin=None, fnsku=None):
    """Return Amazon listing rows matching a stable catalogue identity."""
    sku = _clean(sku)
    asin = _clean(asin)
    fnsku = _clean(fnsku)

    base = db.session.query(MarketplaceListing).filter(
        MarketplaceListing.store_id == store.id
    )

    if sku:
        rows = base.filter(MarketplaceListing.external_sku == sku).all()
        if rows:
            return rows

    if fnsku:
        rows = base.filter(
            (MarketplaceListing.external_listing_id == fnsku)
            | (MarketplaceListing.fnsku == fnsku)
        ).all()
        if rows:
            return rows

    if asin:
        return base.filter(MarketplaceListing.asin == asin).all()

    return []


def _find_existing_listing(store, sku, asin=None, fnsku=None):
    rows = _identity_query(store, sku=sku, asin=asin, fnsku=fnsku)
    active = [row for row in rows if bool(getattr(row, "is_active", False))]

    if not active:
        return None

    return sorted(
        active,
        key=lambda row: (
            0 if getattr(row, "warehouse_stock_id", None) else 1,
            0 if getattr(row, "master_product_group_id", None) else 1,
            -(int(getattr(row, "id", 0) or 0)),
        ),
    )[0]


def _find_relationship_source(store, sku, asin=None, fnsku=None, exclude_id=None):
    """Find a saved relationship without reactivating an old catalogue row."""
    candidates = _identity_query(store, sku=sku, asin=asin, fnsku=fnsku)
    linked = [
        row
        for row in candidates
        if int(getattr(row, "id", 0) or 0) != int(exclude_id or 0)
        and (
            getattr(row, "warehouse_stock_id", None)
            or getattr(row, "master_product_group_id", None)
        )
    ]

    if not linked:
        return None

    return sorted(
        linked,
        key=lambda row: (
            0 if bool(getattr(row, "is_active", False)) else 1,
            0 if getattr(row, "warehouse_stock_id", None) else 1,
            0 if getattr(row, "master_product_group_id", None) else 1,
            -(int(getattr(row, "id", 0) or 0)),
        ),
    )[0]


def _preserve_relationship(existing_listing, source_listing, inventory_row):
    """Carry forward relationship identity only; never alter marketplace state."""
    preserved = False

    if existing_listing is not None and source_listing is not None:
        if (
            not getattr(existing_listing, "warehouse_stock_id", None)
            and getattr(source_listing, "warehouse_stock_id", None)
        ):
            existing_listing.warehouse_stock_id = source_listing.warehouse_stock_id
            preserved = True

        if (
            not getattr(existing_listing, "master_product_group_id", None)
            and getattr(source_listing, "master_product_group_id", None)
        ):
            existing_listing.master_product_group_id = source_listing.master_product_group_id
            preserved = True

    relationship_listing = existing_listing or source_listing

    if (
        inventory_row is not None
        and relationship_listing is not None
        and hasattr(inventory_row, "warehouse_stock_id")
        and not getattr(inventory_row, "warehouse_stock_id", None)
        and getattr(relationship_listing, "warehouse_stock_id", None)
    ):
        inventory_row.warehouse_stock_id = relationship_listing.warehouse_stock_id
        preserved = True

    return preserved


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
        preserved_relationships = 0
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

            relationship_source = _find_relationship_source(
                store,
                sku=sku,
                asin=asin,
                fnsku=fnsku,
                exclude_id=getattr(existing_listing, "id", None),
            )

            if _preserve_relationship(existing_listing, relationship_source, inv):
                preserved_relationships += 1

            if existing_listing:
                linked_existing_listings += 1

                # Visibility/cache only. No warehouse quantity mutation.
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
                if not (
                    relationship_source
                    and getattr(relationship_source, "warehouse_stock_id", None)
                ):
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
                f"preserved_relationships={preserved_relationships} "
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
            "preserved_relationships": preserved_relationships,
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
