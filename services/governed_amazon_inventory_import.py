"""
BT38 GOVERNED AMAZON FBA INVENTORY UPDATE

Normal-operation contract:
- FBA/AFN is read-only.
- A webhook/event updates only the affected FBA inventory identity.
- No normal runtime call may fetch or scan the complete Amazon inventory.
- Full FBA hydration is allowed only when the caller explicitly passes
  full_refresh=True for initial connection or operator recovery.
- Do not create MarketplaceListing or WarehouseStock rows.
- Do not mutate warehouse stock quantities.
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
    """Query only rows matching the changed Amazon catalogue identity."""
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
    """Carry forward relationship identity without changing warehouse quantity."""
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


def _normalise_event_row(payload):
    """Accept adapter rows or Amazon notification-style field names."""
    payload = dict(payload or {})
    details = payload.get("inventoryDetails") or payload.get("inventory_details") or {}

    def _qty(*keys):
        for key in keys:
            value = payload.get(key)
            if value is not None:
                return int(value or 0)
            value = details.get(key)
            if value is not None:
                if isinstance(value, dict):
                    return int(value.get("totalReservedQuantity") or 0)
                return int(value or 0)
        return 0

    return {
        "seller_sku": (
            payload.get("seller_sku")
            or payload.get("sellerSku")
            or payload.get("sku")
        ),
        "asin": payload.get("asin"),
        "fnsku": payload.get("fnsku") or payload.get("fnSku"),
        "fulfillment_channel": (
            payload.get("fulfillment_channel")
            or payload.get("fulfillmentChannel")
            or "AFN"
        ),
        "available_quantity": _qty(
            "available_quantity",
            "fulfillableQuantity",
            "totalQuantity",
        ),
        "reserved_quantity": _qty(
            "reserved_quantity",
            "reservedQuantity",
        ),
        "inbound_quantity": _qty("inbound_quantity"),
    }


def _apply_inventory_row(store, raw_row):
    """Update exactly one FBA inventory identity and its linked listing cache."""
    row = _normalise_event_row(raw_row)
    sku = _clean(row.get("seller_sku"))
    if not sku:
        return {"updated": False, "reason": "seller_sku_required"}

    asin = _clean(row.get("asin"))
    fnsku = _clean(row.get("fnsku"))
    channel = _normalise_channel(row.get("fulfillment_channel"))
    qty = int(row.get("available_quantity") or 0)
    reserved = int(row.get("reserved_quantity") or 0)
    inbound = int(row.get("inbound_quantity") or 0)
    now = datetime.utcnow()

    inv_query = db.session.query(AmazonFBAInventory).filter(
        AmazonFBAInventory.seller_sku == sku
    )
    if hasattr(AmazonFBAInventory, "store_id"):
        inv_query = inv_query.filter(AmazonFBAInventory.store_id == store.id)
    inv = inv_query.first()

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
    inv.last_synced_at = now
    inv.last_sync_status = "success"
    inv.updated_at = now

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
    relationship_preserved = _preserve_relationship(
        existing_listing,
        relationship_source,
        inv,
    )

    if existing_listing:
        if hasattr(existing_listing, "asin") and asin:
            existing_listing.asin = asin
        if hasattr(existing_listing, "fnsku") and fnsku:
            existing_listing.fnsku = fnsku
        if hasattr(existing_listing, "last_marketplace_qty"):
            existing_listing.last_marketplace_qty = qty
        if hasattr(existing_listing, "last_synced_at"):
            existing_listing.last_synced_at = now
        if hasattr(existing_listing, "updated_at"):
            existing_listing.updated_at = now

    return {
        "updated": True,
        "seller_sku": sku,
        "asin": asin or None,
        "fnsku": fnsku or None,
        "channel": channel,
        "available_quantity": qty,
        "linked_existing_listing": bool(existing_listing),
        "relationship_preserved": bool(relationship_preserved),
        "warehouse_mutation": False,
    }


def apply_governed_amazon_fba_event(store_id, payload, source="amazon_fba_event"):
    """Apply one Amazon FBA change without fetching marketplace inventory."""
    store = (
        db.session.query(Store)
        .filter(
            Store.id == int(store_id),
            Store.platform.ilike("%amazon%"),
            Store.is_active == True,  # noqa: E712
        )
        .first()
    )
    if not store:
        return {"success": False, "reason": "amazon_store_not_found"}

    result = _apply_inventory_row(store, payload)
    if not result.get("updated"):
        db.session.rollback()
        return {
            "success": False,
            "governed": True,
            "source": source,
            "full_scan_started": False,
            **result,
        }

    db.session.commit()
    return {
        "success": True,
        "governed": True,
        "source": source,
        "targeted": True,
        "rows_received": 1,
        "rows_updated": 1,
        "full_scan_started": False,
        "warehouse_mutation": False,
        "result": result,
    }


def run_governed_amazon_inventory_import(
    store_id=None,
    *,
    inventory_rows=None,
    full_refresh=False,
    source="targeted_inventory_update",
):
    """Apply supplied changes; full marketplace scan requires explicit approval."""
    if inventory_rows is None and not full_refresh:
        return {
            "success": False,
            "governed": True,
            "source": source,
            "reason": "targeted_inventory_rows_required",
            "targeted": True,
            "full_scan_started": False,
            "results": [],
        }

    query = db.session.query(Store).filter(
        Store.platform.ilike("%amazon%"),
        Store.is_active == True,  # noqa: E712
    )
    if store_id:
        query = query.filter(Store.id == store_id)
    stores = query.all()
    results = []

    for store in stores:
        rows = list(inventory_rows or [])
        if full_refresh:
            rows = AmazonSPAPIAdapter(store).get_inventory()

        updated_rows = []
        for row in rows:
            result = _apply_inventory_row(store, row)
            if result.get("updated"):
                updated_rows.append(result)

        if full_refresh:
            set_store_runtime_status(store, "idle", last_sync=True)
            db.session.add(SyncLog(
                store_id=store.id,
                status="success",
                items_synced=len(updated_rows),
                message=(
                    "governed_amazon_fba_explicit_full_refresh "
                    f"updated={len(updated_rows)} source={source}"
                ),
                created_at=datetime.utcnow(),
            ))

        results.append({
            "store_id": store.id,
            "store": store.name,
            "targeted": not full_refresh,
            "full_refresh": bool(full_refresh),
            "rows_received": len(rows),
            "rows_updated": len(updated_rows),
            "updated_skus": [item["seller_sku"] for item in updated_rows],
            "warehouse_mutation": False,
        })

    db.session.commit()
    return {
        "success": True,
        "governed": True,
        "source": source,
        "truth_source": "AmazonFBAInventory",
        "targeted": not full_refresh,
        "full_scan_started": bool(full_refresh),
        "warehouse_mutation": False,
        "created_marketplace_listings": 0,
        "created_warehouse_stock": 0,
        "results": results,
    }
