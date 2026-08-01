"""
Governed order -> warehouse stock mutation bridge.

Rule:
- Marketplace sale/order events must update the linked warehouse/group stock.
- SKU name does not decide authority.
- Linked MarketplaceListing.warehouse_stock_id decides the warehouse stock row.
- If grouped, the shared warehouse/group stock is mutated once.
- This service does NOT push directly to marketplaces.
- Push/reconcile remains controlled by governed fuse-box paths.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app import db
from models import (
    AmazonFBAInventory,
    MarketplaceListing,
    WarehouseStock,
    StockLedgerEntry,
)


SALE_TYPES = {"order", "processed", "fulfilled", "shipped"}
RETURN_TYPES = {"refund", "return", "returned", "cancelled", "canceled"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _line_idempotency_key(line: Any) -> str:
    existing = _text(getattr(line, "stock_mutation_key", None))
    if existing:
        return existing

    explicit = _text(getattr(line, "idempotency_key", None))
    if explicit:
        return f"order_stock:{explicit}"

    platform = _text(getattr(line, "platform", None))
    order_id = (
        _text(getattr(line, "external_order_id", None))
        or _text(getattr(line, "marketplace_order_id", None))
        or _text(getattr(line, "order_number", None))
        or _text(getattr(line, "id", None))
    )
    sku = _text(getattr(line, "sku", None))
    qty = _text(getattr(line, "quantity", None))
    return f"order_stock:{platform}:{order_id}:{sku}:{qty}"


def _already_mutated(key: str) -> bool:
    if not key:
        return False

    return (
        db.session.query(StockLedgerEntry.id)
        .filter(StockLedgerEntry.reference_id == key)
        .first()
        is not None
    )


def _line_sku(line: Any) -> str:
    return (
        _text(getattr(line, "sku", None))
        or _text(getattr(line, "external_sku", None))
        or _text(getattr(line, "seller_sku", None))
    )


def _line_platform(line: Any) -> str:
    return _text(getattr(line, "platform", None) or getattr(line, "marketplace", None)).lower()


def _line_quantity(line: Any) -> int:
    for attr in ("quantity", "qty", "quantity_sold", "qty_sold"):
        qty = _safe_int(getattr(line, attr, None), 0)
        if qty:
            return abs(qty)
    return 1


def _line_type(line: Any) -> str:
    return _text(
        getattr(line, "transaction_type", None)
        or getattr(line, "type", None)
        or getattr(line, "status", None)
    ).lower()


def is_sale(line: Any) -> bool:
    value = _line_type(line)

    # Imported FBM marketplace orders arrive as "pending".
    # Once linked to warehouse stock they should reduce stock.
    if value == "pending":
        fulfillment = _text(getattr(line, "fulfillment_type", None)).upper()
        if fulfillment == "FBM":
            return True

    if not value:
        return True

    return any(token in value for token in SALE_TYPES)


def _is_return(line: Any) -> bool:
    value = _line_type(line)
    return any(token in value for token in RETURN_TYPES)


def _find_listing_for_line(line: Any):
    sku = _line_sku(line)
    if not sku:
        return None

    query = MarketplaceListing.query.filter(
        MarketplaceListing.is_active == True,  # noqa: E712
        MarketplaceListing.external_sku == sku,
    )

    platform = _line_platform(line)
    if platform:
        query = query.join(MarketplaceListing.store).filter(
            MarketplaceListing.store.has()
        )

    return query.order_by(
        MarketplaceListing.warehouse_stock_id.is_(None),
        MarketplaceListing.updated_at.desc(),
        MarketplaceListing.id.desc(),
    ).first()


def mutate_warehouse_stock_from_order_line(line: Any, source: str = "governed_order_bridge") -> dict[str, Any]:
    """
    Mutates warehouse stock from one marketplace order/sale line.

    Sale:
      available_quantity decreases by quantity.

    Return/refund:
      available_quantity increases by quantity.

    Idempotency:
      Uses StockLedgerEntry.reference_id so the same order line is not applied twice.
    """

    key = _line_idempotency_key(line)

    if _already_mutated(key):
        return {
            "success": True,
            "skipped": True,
            "reason": "already_mutated",
            "reference_id": key,
        }

    listing = _find_listing_for_line(line)

    if not listing or not listing.warehouse_stock_id:
        return {
            "success": False,
            "skipped": True,
            "reason": "no_linked_marketplace_listing",
            "sku": _line_sku(line),
            "reference_id": key,
        }

    stock = db.session.get(WarehouseStock, listing.warehouse_stock_id)

    if not stock:
        return {
            "success": False,
            "skipped": True,
            "reason": "warehouse_stock_missing",
            "warehouse_stock_id": listing.warehouse_stock_id,
            "reference_id": key,
        }

    qty = _line_quantity(line)

    before_available = _safe_int(stock.available_quantity)
    before_reserved = _safe_int(stock.reserved_quantity)
    before_allocated = _safe_int(stock.allocated_quantity)

    if _is_return(line):
        after_available = before_available + qty
        transaction_type = "return"
        adjustment_type = "increase"
    elif is_sale(line):
        after_available = max(0, before_available - qty)
        transaction_type = "sale"
        adjustment_type = "decrease"
    else:
        return {
            "success": False,
            "skipped": True,
            "reason": "unsupported_order_line_type",
            "line_type": _line_type(line),
            "reference_id": key,
        }

    stock.available_quantity = after_available
    stock.updated_at = datetime.utcnow()

    # Mark marketplace order line as processed after the stock ledger mutation.
    if hasattr(line, "status"):
        line.status = "processed"
    if hasattr(line, "processed_at"):
        line.processed_at = datetime.utcnow()
    if hasattr(line, "error_message"):
        line.error_message = None
    if hasattr(line, "updated_at"):
        line.updated_at = datetime.utcnow()

    # Mark linked listings dirty for governed reconcile/push, but do not push here.
    linked = (
        MarketplaceListing.query
        .filter(MarketplaceListing.warehouse_stock_id == stock.id)
        .all()
    )

    for linked_listing in linked:
        if hasattr(linked_listing, "push_state"):
            linked_listing.push_state = "pending_group_reconcile"
        if hasattr(linked_listing, "last_sync_status"):
            linked_listing.last_sync_status = "pending_group_reconcile"
        if hasattr(linked_listing, "updated_at"):
            linked_listing.updated_at = datetime.utcnow()

    ledger = StockLedgerEntry(
        warehouse_stock_id=stock.id,
        transaction_type=transaction_type,
        adjustment_type=adjustment_type,
        available_quantity_before=before_available,
        available_quantity_after=after_available,
        reserved_quantity_before=before_reserved,
        reserved_quantity_after=before_reserved,
        allocated_quantity_before=before_allocated,
        allocated_quantity_after=before_allocated,
        on_order_quantity_before=0,
        on_order_quantity_after=0,
        pending_receipt_qty_before=0,
        pending_receipt_qty_after=0,
        quarantined_quantity_before=0,
        quarantined_quantity_after=0,
        reference_type="marketplace_order",
        reference_id=key,
        reason=f"{source}: marketplace order updated grouped warehouse stock",
        source_system="marketplace",
        update_source=source,
    )

    db.session.add(ledger)

    group_id = getattr(stock, "master_product_group_id", None)
    is_group_controlled = bool(getattr(stock, "is_group_controlled", False))
    should_reconcile_group = bool(group_id and is_group_controlled)

    db.session.commit()

    group_reconcile_result = {
        "success": True,
        "skipped": True,
        "reason": "warehouse_stock_mutation_does_not_push_directly",
        "group_id": group_id,
        "source": "warehouse_stock_mutation",
    }

    return {
        "success": True,
        "skipped": False,
        "sku": stock.sku,
        "warehouse_stock_id": stock.id,
        "group_id": group_id,
        "is_group_controlled": is_group_controlled,
        "quantity": qty,
        "available_before": before_available,
        "available_after": after_available,
        "affected_listings": len(linked),
        "reference_id": key,
        "group_reconcile_triggered": should_reconcile_group,
        "group_reconcile_result": group_reconcile_result,
    }


def process_exact_marketplace_order_line(
    line: Any,
    source: str = "governed_exact_order",
) -> dict[str, Any]:
    """
    Single exact-row order processing entry point.

    FBM/eBay:
      mutate the linked WarehouseStock row once.

    Amazon FBA/AFN:
      retain Amazon inventory as read-only authority and mark the exact
      MarketplaceOrder row processed without decrementing WarehouseStock.

    This function never searches MarketplaceOrder for pending rows.
    """

    if line is None:
        return {
            "success": False,
            "skipped": True,
            "reason": "marketplace_order_missing",
        }

    if getattr(line, "processed_at", None):
        return {
            "success": True,
            "skipped": True,
            "reason": "already_processed",
            "order_id": getattr(line, "marketplace_order_id", None),
        }

    fulfillment = _text(
        getattr(line, "fulfillment_type", None)
    ).upper()

    if fulfillment in {"FBA", "AFN"}:
        # Amazon remains the inventory authority for FBA.
        #
        # Align only the exact FBA authority row associated with this order.
        # The active AFN/FBA listing already contains Amazon's imported
        # marketplace quantity. Copy that value into AmazonFBAInventory so:
        #
        #   FBA Read Only
        #   Master Stock
        #   Product Linking
        #
        # all read the same persisted FBA authority.
        #
        # WarehouseStock is never decremented for FBA/AFN.
        stock_id = getattr(line, "warehouse_stock_id", None)
        seller_sku = _text(getattr(line, "sku", None))

        listing_query = MarketplaceListing.query.filter(
            MarketplaceListing.is_active == True,  # noqa: E712
        )

        if stock_id:
            listing_query = listing_query.filter(
                MarketplaceListing.warehouse_stock_id == stock_id,
            )
        elif seller_sku:
            listing_query = listing_query.filter(
                MarketplaceListing.external_sku == seller_sku,
            )
        else:
            listing_query = listing_query.filter(
                MarketplaceListing.id == -1,
            )

        listing = (
            listing_query
            .order_by(MarketplaceListing.id.desc())
            .first()
        )

        fba_row = None
        authoritative_quantity = None
        fba_quantity_changed = False
        fba_quantity_before = None
        fba_quantity_after = None

        if listing is not None:
            channel = _text(
                getattr(
                    listing,
                    "normalized_amazon_fulfillment_channel",
                    None,
                )
                or getattr(
                    listing,
                    "amazon_fulfillment_channel",
                    None,
                )
            ).upper()

            if (
                bool(getattr(listing, "is_fba", False))
                or channel in {"FBA", "AFN"}
            ):
                raw_quantity = getattr(
                    listing,
                    "effective_quantity",
                    None,
                )

                if raw_quantity is not None:
                    authoritative_quantity = _safe_int(raw_quantity)

                fba_query = AmazonFBAInventory.query

                if stock_id:
                    fba_query = fba_query.filter(
                        AmazonFBAInventory.warehouse_stock_id
                        == stock_id,
                    )
                elif seller_sku:
                    fba_query = fba_query.filter(
                        AmazonFBAInventory.seller_sku
                        == seller_sku,
                    )
                else:
                    fba_query = fba_query.filter(
                        AmazonFBAInventory.id == -1,
                    )

                fba_row = (
                    fba_query
                    .filter(
                        AmazonFBAInventory.is_archived == False,  # noqa: E712
                    )
                    .order_by(AmazonFBAInventory.id.desc())
                    .first()
                )

        if fba_row is not None and authoritative_quantity is not None:
            fba_quantity_before = _safe_int(
                getattr(fba_row, "available_quantity", 0)
            )
            fba_quantity_after = authoritative_quantity

            if fba_quantity_before != fba_quantity_after:
                fba_row.available_quantity = fba_quantity_after
                fba_quantity_changed = True

                if hasattr(fba_row, "updated_at"):
                    fba_row.updated_at = datetime.utcnow()

                if hasattr(fba_row, "last_sync_status"):
                    fba_row.last_sync_status = "success"

        line.status = "processed"
        line.processed_at = datetime.utcnow()

        if hasattr(line, "updated_at"):
            line.updated_at = datetime.utcnow()

        db.session.commit()

        return {
            "success": True,
            "skipped": False,
            "processed": True,
            "stock_mutated": False,
            "inventory_authority": "AmazonFBAInventory",
            "fulfillment_type": fulfillment,
            "order_id": getattr(line, "marketplace_order_id", None),
            "warehouse_stock_id": stock_id,
            "fba_inventory_id": (
                getattr(fba_row, "id", None)
                if fba_row is not None
                else None
            ),
            "fba_quantity_changed": fba_quantity_changed,
            "fba_quantity_before": fba_quantity_before,
            "fba_quantity_after": fba_quantity_after,
        }

    # FBM and eBay use the existing idempotent warehouse mutation.
    return mutate_warehouse_stock_from_order_line(
        line,
        source=source,
    )


def mutate_recent_marketplace_order_lines(limit: int = 100, source: str = "governed_order_bridge") -> dict[str, Any]:
    """
    Governed marketplace order bridge.

    MarketplaceOrder is the marketplace authority.

    Webhook/order import paths create MarketplaceOrder rows.
    This bridge mutates warehouse stock from MarketplaceOrder only.
    It does not mix CanonicalOrderLine or SalesOrderItem into marketplace stock authority.
    It does not push directly; group/listing reconcile remains governed.
    """

    from models import MarketplaceOrder

    candidates = (
        MarketplaceOrder.query
        .filter(MarketplaceOrder.status == "pending")
        .filter(MarketplaceOrder.fulfillment_type == "FBM")
        .filter(MarketplaceOrder.warehouse_stock_id.isnot(None))
        .order_by(MarketplaceOrder.id.desc())
        .limit(limit)
        .all()
    )

    results = []
    mutated = 0
    skipped = 0

    for line in candidates:
        result = mutate_warehouse_stock_from_order_line(line, source=source)
        results.append(result)

        if result.get("success") and not result.get("skipped"):
            mutated += 1
        else:
            skipped += 1

    return {
        "success": True,
        "governed": True,
        "source": source,
        "authority": "MarketplaceOrder",
        "checked": len(candidates),
        "mutated": mutated,
        "skipped": skipped,
        "results": results[:50],
    }


def replay_failed_grouped_marketplace_orders(limit: int = 100, source: str = "governed_failed_order_replay") -> dict[str, Any]:
    """
    Controlled replay for old failed marketplace orders after grouping/linking is corrected.

    Safe rules:
    - MarketplaceOrder only
    - status must be failed
    - SKU must now link to MarketplaceListing.warehouse_stock_id
    - warehouse stock must exist
    - order must not already have a StockLedgerEntry
    - sale mutates stock once
    - order status becomes stock_applied_pending_reconcile
    - no marketplace push happens here
    """

    from models import MarketplaceOrder

    rows = (
        MarketplaceOrder.query
        .filter(MarketplaceOrder.status == "failed")
        .order_by(MarketplaceOrder.id.desc())
        .limit(limit)
        .all()
    )

    checked = 0
    replayed = 0
    skipped = 0
    results = []

    for order in rows:
        checked += 1

        key = _line_idempotency_key(order)

        if _already_mutated(key):
            skipped += 1
            results.append({
                "order_id": getattr(order, "marketplace_order_id", None),
                "sku": getattr(order, "sku", None),
                "skipped": True,
                "reason": "already_mutated",
            })
            continue

        listing = _find_listing_for_line(order)

        if not listing or not listing.warehouse_stock_id:
            skipped += 1
            results.append({
                "order_id": getattr(order, "marketplace_order_id", None),
                "sku": getattr(order, "sku", None),
                "skipped": True,
                "reason": "still_not_linked_to_warehouse",
            })
            continue

        stock = db.session.get(WarehouseStock, listing.warehouse_stock_id)

        if not stock:
            skipped += 1
            results.append({
                "order_id": getattr(order, "marketplace_order_id", None),
                "sku": getattr(order, "sku", None),
                "skipped": True,
                "reason": "warehouse_stock_missing",
            })
            continue

        qty = _line_quantity(order)

        if int(stock.sellable_quantity or 0) < qty:
            skipped += 1
            results.append({
                "order_id": getattr(order, "marketplace_order_id", None),
                "sku": getattr(order, "sku", None),
                "warehouse_stock_id": stock.id,
                "available": int(stock.sellable_quantity or 0),
                "required": qty,
                "skipped": True,
                "reason": "insufficient_current_stock",
            })
            continue

        result = mutate_warehouse_stock_from_order_line(
            order,
            source=source,
        )

        if result.get("success") and not result.get("skipped"):
            order.status = "stock_applied_pending_reconcile"
            order.error_message = None
            if hasattr(order, "updated_at"):
                order.updated_at = datetime.utcnow()
            db.session.commit()
            replayed += 1
        else:
            skipped += 1

        results.append({
            "order_id": getattr(order, "marketplace_order_id", None),
            "sku": getattr(order, "sku", None),
            "result": result,
        })

    return {
        "success": True,
        "governed": True,
        "source": source,
        "checked": checked,
        "replayed": replayed,
        "skipped": skipped,
        "results": results[:50],
    }

