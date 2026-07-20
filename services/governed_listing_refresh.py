"""One clear governed listing refresh path.

This is the read/import side of the governed marketplace flow.
It creates or updates the MarketplaceListing row required before any governed
FBM push can be attempted.

It does not call Amazon directly, does not push quantity, does not start workers,
does not start schedulers, and does not use retired sync routes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app import db
from models import MarketplaceListing, Store, Warehouse, WarehouseStock

ALLOWED_MARKETPLACES = {"amazon"}
ALLOWED_FULFILLMENT_CHANNELS = {"MFN", "FBM", "AFN", "FBA"}
TRANSFER_PENDING_REVIEW = "Transfer Pending Review"


def normalize_fulfillment_channel(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_sku(value: Any) -> str:
    return str(value or "").strip()


def _warehouse_location_for_fulfillment(fulfillment: str) -> str:
    if fulfillment in {"AFN", "FBA"}:
        return "Amazon FBA"
    if fulfillment in {"MFN", "FBM"}:
        return "Amazon FBM"
    return "Warehouse"


def _find_or_create_warehouse_stock_for_listing(*, sku: str, title: str | None, fulfillment: str) -> WarehouseStock:
    """Ensure every governed imported listing has a WarehouseStock truth row.

    The warehouse page renders from WarehouseStock first. If a governed import
    creates a MarketplaceListing without a warehouse_stock_id, that listing is
    hidden from /warehouse and quantity actions fail. This helper keeps import
    visibility aligned without changing UI or marketplace execution.
    """
    default_warehouse = Warehouse.get_default()

    stock = db.session.query(WarehouseStock).filter(
        WarehouseStock.warehouse_id == default_warehouse.id,
        WarehouseStock.sku == sku,
    ).first()

    if stock is None:
        stock = WarehouseStock(
            warehouse_id=default_warehouse.id,
            sku=sku,
            available_quantity=0,
            reserved_quantity=0,
            allocated_quantity=0,
            on_order_quantity=0,
            product_name=title or sku,
            location=_warehouse_location_for_fulfillment(fulfillment),
            unit_cost=0.0,
            is_active=True,
            is_deleted=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.session.add(stock)
        db.session.flush()
        return stock

    changed = False
    if not getattr(stock, "product_name", None) and title:
        stock.product_name = title
        changed = True
    if not getattr(stock, "location", None):
        stock.location = _warehouse_location_for_fulfillment(fulfillment)
        changed = True
    if getattr(stock, "is_deleted", False):
        stock.is_deleted = False
        changed = True
    if getattr(stock, "is_active", True) is not True:
        stock.is_active = True
        changed = True
    if changed:
        stock.updated_at = datetime.utcnow()

    return stock


def refresh_governed_listing_from_snapshot(
    *,
    store_id: Any,
    sku: str,
    external_listing_id: str,
    amazon_fulfillment_channel: str,
    title: str | None = None,
    price: float | int | None = None,
    currency: str = "GBP",
    warehouse_stock_id: Any | None = None,
    transfer_status: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Create or update one governed MarketplaceListing from a verified snapshot.

    The caller must provide a verified marketplace snapshot. This helper only
    writes the listing state into BT38 so governed execution can validate the
    listing/store pair later.
    """
    sku = normalize_sku(sku)
    fulfillment = normalize_fulfillment_channel(amazon_fulfillment_channel)
    external_listing_id = str(external_listing_id or "").strip()

    if not sku:
        return _blocked("missing sku")
    if not external_listing_id:
        return _blocked("missing external_listing_id")
    if fulfillment not in ALLOWED_FULFILLMENT_CHANNELS:
        return _blocked("unknown fulfillment channel")

    store = db.session.get(Store, store_id)
    if store is None:
        return _blocked("missing store")
    if "amazon" not in str(getattr(store, "platform", "") or "").lower():
        return _blocked("store is not Amazon")
    if getattr(store, "is_active", False) is not True:
        return _blocked("store is not active")

    listing = MarketplaceListing.query.filter_by(
        store_id=store.id,
        external_listing_id=external_listing_id,
        external_sku=sku,
    ).first()

    warehouse_stock = None

    if warehouse_stock_id is not None:
        warehouse_stock = db.session.get(WarehouseStock, warehouse_stock_id)
        if warehouse_stock is None:
            return _blocked("missing warehouse stock")

    elif listing is not None and getattr(listing, "warehouse_stock_id", None):
        # Existing grouped/linked listing already has warehouse authority.
        # Preserve that authority even when marketplace SKU and warehouse SKU differ.
        warehouse_stock = db.session.get(WarehouseStock, listing.warehouse_stock_id)
        if warehouse_stock is None:
            return _blocked("missing linked warehouse stock")

        target_location = _warehouse_location_for_fulfillment(fulfillment)
        if getattr(warehouse_stock, "location", None) != target_location:
            warehouse_stock.location = target_location
            warehouse_stock.updated_at = datetime.utcnow()

    else:
        warehouse_stock = _find_or_create_warehouse_stock_for_listing(
            sku=sku,
            title=title,
            fulfillment=fulfillment,
        )

    # Legacy Amazon identifier migration guard:
    # Older rows used external_listing_id = SKU.
    # Newer snapshots may use ASIN as external_listing_id.
    # If the same active Amazon SKU already points to the same warehouse row,
    # reuse that legacy row instead of creating a duplicate MarketplaceListing.
    if listing is None and warehouse_stock is not None:
        legacy_listing = (
            MarketplaceListing.query
            .filter_by(
                store_id=store.id,
                external_sku=sku,
                warehouse_stock_id=warehouse_stock.id,
                is_active=True,
            )
            .filter(MarketplaceListing.external_listing_id == sku)
            .order_by(MarketplaceListing.id.asc())
            .first()
        )
        if legacy_listing is not None:
            listing = legacy_listing

    created = False
    if listing is None:
        listing = MarketplaceListing(
            store_id=store.id,
            external_listing_id=external_listing_id,
            external_sku=sku,
            title=title or sku,
            price=float(price or 0),
            currency=currency or "GBP",
            amazon_fulfillment_channel=fulfillment,
            warehouse_stock_id=warehouse_stock.id,
            is_active=True,
            push_state="needs_review" if transfer_status == TRANSFER_PENDING_REVIEW else "active",
            last_push_status="pending",
        )
        created = True
        db.session.add(listing)

    # Preserve stable operational SKU identity.
    # Never overwrite an existing working SKU with a blank incoming value.
    incoming_sku = normalize_sku(sku)
    existing_sku = normalize_sku(getattr(listing, "external_sku", None))
    if incoming_sku:
        listing.external_sku = incoming_sku
    elif not existing_sku:
        listing.external_sku = ""
    listing.amazon_fulfillment_channel = fulfillment
    listing.title = title or listing.title or sku
    listing.price = float(price if price is not None else listing.price or 0)
    listing.currency = currency or listing.currency or "GBP"
    listing.is_active = True
    listing.last_synced_at = datetime.utcnow()
    listing.updated_at = datetime.utcnow()

    # Marketplace refresh may establish an initial warehouse relationship,
    # but it must never replace a relationship saved through Product Linking.
    if (
        warehouse_stock is not None
        and listing.warehouse_stock_id is None
    ):
        listing.warehouse_stock_id = warehouse_stock.id

    normalized_live_channel = str(
        getattr(listing, "normalized_amazon_fulfillment_channel", "") or ""
    ).strip().upper()

    converted_fbm_listing = (
        normalized_live_channel in {"MFN", "FBM", "MERCHANT"}
        and bool(getattr(listing, "warehouse_stock_id", None))
    )

    if transfer_status == TRANSFER_PENDING_REVIEW:
        listing.push_state = "needs_review"

    elif fulfillment in {"AFN", "FBA"} and not converted_fbm_listing:
        listing.push_state = "blocked"

    elif (
        fulfillment in {"MFN", "FBM"}
        or converted_fbm_listing
    ) and listing.push_state in {None, "blocked", "needs_review"}:
        listing.push_state = "active"

    db.session.commit()

    return {
        "success": True,
        "ok": True,
        "created": created,
        "listing_id": listing.id,
        "store_id": store.id,
        "sku": sku,
        "external_listing_id": external_listing_id,
        "amazon_fulfillment_channel": listing.amazon_fulfillment_channel,
        "push_state": listing.push_state,
        "warehouse_stock_id": listing.warehouse_stock_id,
        "actor": actor,
    }


def _blocked(reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "ok": False,
        "execution_blocked": True,
        "reason": reason,
    }
