"""
BT38 GOVERNED AMAZON FBA INVENTORY UPDATE

Normal-operation contract:
- FBA/AFN is read-only.
- A webhook/event updates only the affected FBA inventory identity.
- No normal runtime call may fetch or scan the complete Amazon inventory.
- Full FBA hydration is allowed only when the caller explicitly passes
  full_refresh=True for initial connection or operator recovery.
- Do not create MarketplaceListing or WarehouseStock rows.
- Do not mutate warehouse stock quantities or Product Linking relationships.
"""

from datetime import datetime

from app import db
from models import AmazonFBAInventory, MarketplaceListing, Store, SyncLog
from backend.adapters.amazon_sp_api_adapter import AmazonSPAPIAdapter
from services.runtime_status_writer import set_store_runtime_status


def _clean(value):
    return str(value or "").strip()


def _normalise_channel(value):
    channel = _clean(value).upper()
    if channel in {"FBA", "AFN", "AMAZON", "AMAZON_FULFILLED"}:
        return "AFN"
    if channel in {"FBM", "MFN", "MERCHANT", "MERCHANT_FULFILLED"}:
        return "MFN"
    return "UNKNOWN"


def _normalise_event_row(payload):
    """Accept adapter rows or Amazon notification-style field names."""
    payload = dict(payload or {})
    details = payload.get("inventoryDetails") or payload.get("inventory_details") or {}

    def _qty(*keys):
        for key in keys:
            value = payload.get(key)
            if value is None:
                value = details.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                value = value.get("totalReservedQuantity") or 0
            return int(value or 0)
        return 0

    return {
        "seller_sku": payload.get("seller_sku") or payload.get("sellerSku") or payload.get("sku"),
        "asin": payload.get("asin"),
        "fnsku": payload.get("fnsku") or payload.get("fnSku"),
        "fulfillment_channel": (
            payload.get("fulfillment_channel")
            or payload.get("fulfillmentChannel")
            or "AFN"
        ),
        "available_quantity": _qty("available_quantity", "fulfillableQuantity", "totalQuantity"),
        "reserved_quantity": _qty("reserved_quantity", "reservedQuantity"),
        "inbound_quantity": _qty("inbound_quantity"),
    }


def _find_existing_listing(store, sku, asin=None, fnsku=None):
    """Resolve only the affected listing identity; never scan unrelated listings."""
    base = db.session.query(MarketplaceListing).filter(
        MarketplaceListing.store_id == store.id,
        MarketplaceListing.is_active == True,  # noqa: E712
    )

    if sku:
        listing = base.filter(MarketplaceListing.external_sku == sku).order_by(
            MarketplaceListing.id.desc()
        ).first()
        if listing:
            return listing

    if fnsku:
        listing = base.filter(
            (MarketplaceListing.external_listing_id == fnsku)
            | (MarketplaceListing.fnsku == fnsku)
        ).order_by(MarketplaceListing.id.desc()).first()
        if listing:
            return listing

    if asin:
        return base.filter(MarketplaceListing.asin == asin).order_by(
            MarketplaceListing.id.desc()
        ).first()

    return None


def _same_value(obj, field, expected):
    if not hasattr(obj, field):
        return True
    current = getattr(obj, field, None)
    if expected is None:
        return current in (None, "")
    return current == expected


def _inventory_unchanged(inv, *, store_id, asin, fnsku, qty, reserved, inbound):
    if inv is None:
        return False
    checks = [
        _same_value(inv, "store_id", store_id),
        _same_value(inv, "available_quantity", qty),
        _same_value(inv, "reserved_quantity", reserved),
        _same_value(inv, "inbound_quantity", inbound),
        _same_value(inv, "asin", asin or None),
        _same_value(inv, "fnsku", fnsku or None),
        _same_value(inv, "is_archived", False),
        _same_value(inv, "last_sync_status", "success"),
    ]
    return all(checks)


def _listing_cache_unchanged(listing, *, asin, fnsku, qty):
    if listing is None:
        return True
    checks = [
        _same_value(listing, "asin", asin or getattr(listing, "asin", None)),
        _same_value(listing, "fnsku", fnsku or getattr(listing, "fnsku", None)),
        _same_value(listing, "last_marketplace_qty", qty),
    ]
    return all(checks)


def _apply_inventory_row(store, raw_row):
    """Update one FBA identity; unchanged events perform no writes."""
    row = _normalise_event_row(raw_row)
    sku = _clean(row.get("seller_sku"))
    if not sku:
        return {"updated": False, "changed": False, "reason": "seller_sku_required"}

    asin = _clean(row.get("asin"))
    fnsku = _clean(row.get("fnsku"))
    channel = _normalise_channel(row.get("fulfillment_channel"))
    qty = int(row.get("available_quantity") or 0)
    reserved = int(row.get("reserved_quantity") or 0)
    inbound = int(row.get("inbound_quantity") or 0)

    inv_query = db.session.query(AmazonFBAInventory).filter(
        AmazonFBAInventory.seller_sku == sku
    )
    if hasattr(AmazonFBAInventory, "store_id"):
        inv_query = inv_query.filter(AmazonFBAInventory.store_id == store.id)
    inv = inv_query.first()

    listing = _find_existing_listing(store, sku=sku, asin=asin, fnsku=fnsku)

    if _inventory_unchanged(
        inv,
        store_id=store.id,
        asin=asin,
        fnsku=fnsku,
        qty=qty,
        reserved=reserved,
        inbound=inbound,
    ) and _listing_cache_unchanged(listing, asin=asin, fnsku=fnsku, qty=qty):
        return {
            "updated": False,
            "changed": False,
            "reason": "unchanged",
            "seller_sku": sku,
            "asin": asin or None,
            "fnsku": fnsku or None,
            "channel": channel,
            "available_quantity": qty,
            "linked_existing_listing": bool(listing),
            "warehouse_stock_id": getattr(listing, "warehouse_stock_id", None) if listing else None,
            "group_id": getattr(listing, "master_product_group_id", None) if listing else None,
            "warehouse_mutation": False,
            "relationship_mutation": False,
        }

    now = datetime.utcnow()
    if inv is None:
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

    if listing:
        if hasattr(listing, "asin") and asin and getattr(listing, "asin", None) != asin:
            listing.asin = asin
        if hasattr(listing, "fnsku") and fnsku and getattr(listing, "fnsku", None) != fnsku:
            listing.fnsku = fnsku
        if hasattr(listing, "last_marketplace_qty") and getattr(listing, "last_marketplace_qty", None) != qty:
            listing.last_marketplace_qty = qty
        if hasattr(listing, "last_synced_at"):
            listing.last_synced_at = now
        if hasattr(listing, "updated_at"):
            listing.updated_at = now

    return {
        "updated": True,
        "changed": True,
        "seller_sku": sku,
        "asin": asin or None,
        "fnsku": fnsku or None,
        "channel": channel,
        "available_quantity": qty,
        "linked_existing_listing": bool(listing),
        "listing_id": getattr(listing, "id", None) if listing else None,
        "warehouse_stock_id": getattr(listing, "warehouse_stock_id", None) if listing else None,
        "group_id": getattr(listing, "master_product_group_id", None) if listing else None,
        "warehouse_mutation": False,
        "relationship_mutation": False,
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
    if result.get("reason") == "seller_sku_required":
        db.session.rollback()
        return {
            "success": False,
            "governed": True,
            "source": source,
            "full_scan_started": False,
            **result,
        }

    if not result.get("updated"):
        db.session.rollback()
        return {
            "success": True,
            "governed": True,
            "source": source,
            "targeted": True,
            "rows_received": 1,
            "rows_updated": 0,
            "unchanged": True,
            "full_scan_started": False,
            "warehouse_mutation": False,
            "relationship_mutation": False,
            "result": result,
        }

    db.session.commit()
    return {
        "success": True,
        "governed": True,
        "source": source,
        "targeted": True,
        "rows_received": 1,
        "rows_updated": 1,
        "unchanged": False,
        "full_scan_started": False,
        "warehouse_mutation": False,
        "relationship_mutation": False,
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
    propagation_targets = {}
    propagation_results = []

    for store in stores:
        rows = list(inventory_rows or [])
        if full_refresh:
            rows = AmazonSPAPIAdapter(store).get_inventory()

        changed_rows = []
        unchanged_rows = []
        for row in rows:
            result = _apply_inventory_row(store, row)
            if result.get("updated"):
                changed_rows.append(result)

                group_id = result.get("group_id")
                warehouse_stock_id = result.get(
                    "warehouse_stock_id"
                )

                if group_id and warehouse_stock_id:
                    propagation_targets[int(group_id)] = int(
                        warehouse_stock_id
                    )

            elif result.get("reason") == "unchanged":
                unchanged_rows.append(result)

        if full_refresh:
            set_store_runtime_status(store, "idle", last_sync=True)
            db.session.add(SyncLog(
                store_id=store.id,
                status="success",
                items_synced=len(changed_rows),
                message=(
                    "governed_amazon_fba_explicit_full_refresh "
                    f"updated={len(changed_rows)} unchanged={len(unchanged_rows)} source={source}"
                ),
                created_at=datetime.utcnow(),
            ))

        results.append({
            "store_id": store.id,
            "store": store.name,
            "targeted": not full_refresh,
            "full_refresh": bool(full_refresh),
            "rows_received": len(rows),
            "rows_updated": len(changed_rows),
            "rows_unchanged": len(unchanged_rows),
            "updated_skus": [item["seller_sku"] for item in changed_rows],
            "warehouse_mutation": False,
            "relationship_mutation": False,
            "group_propagation_requested": sorted({
                int(item["group_id"])
                for item in changed_rows
                if item.get("group_id")
                and item.get("warehouse_stock_id")
            }),
        })

    if any(item["rows_updated"] for item in results) or full_refresh:
        # Commit refreshed AmazonFBAInventory truth before the shared
        # propagation path reads it.
        db.session.commit()

        if propagation_targets:
            from flask import current_app, has_request_context
            from governed_group_propagation_routes import (
                run_governed_group_propagation,
            )

            for group_id in sorted(propagation_targets):
                warehouse_stock_id = propagation_targets[group_id]

                payload = {
                    "warehouse_stock_id": warehouse_stock_id,
                    "source": (
                        "amazon_fba_inventory_refresh_shared_handoff"
                    ),
                    "dry_run": False,
                }

                if has_request_context():
                    response = run_governed_group_propagation(
                        group_id,
                        payload=payload,
                    )
                else:
                    with current_app.test_request_context(
                        headers={
                            "X-Actor": (
                                "amazon-fba-inventory-refresh"
                            ),
                        },
                    ):
                        response = run_governed_group_propagation(
                            group_id,
                            payload=payload,
                        )

                status_code = 200
                response_object = response

                if isinstance(response, tuple):
                    response_object = response[0]

                    if len(response) > 1:
                        status_code = int(response[1] or 200)

                if hasattr(response_object, "get_json"):
                    response_body = (
                        response_object.get_json(silent=True)
                        or {}
                    )
                elif isinstance(response_object, dict):
                    response_body = dict(response_object)
                else:
                    response_body = {
                        "success": False,
                        "reason": (
                            "unsupported_group_propagation_response"
                        ),
                    }

                propagation_results.append({
                    "group_id": group_id,
                    "warehouse_stock_id": warehouse_stock_id,
                    "http_status": status_code,
                    "result": response_body,
                })
    else:
        db.session.rollback()

    return {
        "success": True,
        "governed": True,
        "source": source,
        "truth_source": "AmazonFBAInventory",
        "targeted": not full_refresh,
        "full_scan_started": bool(full_refresh),
        "warehouse_mutation": any(
            item.get("http_status", 500) < 400
            and bool(item.get("result", {}).get("success"))
            for item in propagation_results
        ),
        "relationship_mutation": False,
        "created_marketplace_listings": 0,
        "created_warehouse_stock": 0,
        "group_propagation": propagation_results,
        "results": results,
    }
