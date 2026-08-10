"""Governed order -> warehouse stock mutation bridge."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from app import db
from models import MarketplaceListing, WarehouseStock, StockLedgerEntry

SALE_TYPES = {"order", "processed", "fulfilled", "shipped"}
RETURN_TYPES = {"refund", "return", "returned", "cancelled", "canceled"}

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "": return default
        return int(float(value))
    except Exception: return default

def _text(value: Any) -> str: return str(value or "").strip()

def _line_idempotency_key(line: Any) -> str:
    store_id = _text(getattr(line, "store_id", None))
    order_id = _text(getattr(line, "external_order_id", None)) or _text(getattr(line, "marketplace_order_id", None)) or _text(getattr(line, "order_number", None))
    sku = (_text(getattr(line, "sku", None)) or _text(getattr(line, "external_sku", None)) or _text(getattr(line, "seller_sku", None))).upper()
    if store_id and order_id and sku: return f"order_stock:v2:{store_id}:{order_id}:{sku}"
    existing = _text(getattr(line, "stock_mutation_key", None))
    if existing: return existing
    explicit = _text(getattr(line, "idempotency_key", None))
    if explicit: return f"order_stock:{explicit}"
    platform = _text(getattr(line, "platform", None) or getattr(line, "marketplace", None)).lower()
    return f"order_stock:fallback:{platform}:{_text(getattr(line, 'id', None))}:{sku}:{_text(getattr(line, 'quantity', None))}"

def _already_mutated(line: Any, key: str) -> bool:
    if not key: return False
    if db.session.query(StockLedgerEntry.id).filter(StockLedgerEntry.reference_id == key).first() is not None: return True
    store_id = _text(getattr(line, "store_id", None))
    order_id = _text(getattr(line, "external_order_id", None)) or _text(getattr(line, "marketplace_order_id", None)) or _text(getattr(line, "order_number", None))
    sku = _text(getattr(line, "sku", None)) or _text(getattr(line, "external_sku", None)) or _text(getattr(line, "seller_sku", None))
    if not (store_id and order_id and sku): return False
    return db.session.query(StockLedgerEntry.id).filter(StockLedgerEntry.reference_id.startswith(f"order_stock:{store_id}:{order_id}:"), StockLedgerEntry.reference_id.endswith(f":{sku}")).first() is not None

def _line_sku(line: Any) -> str: return _text(getattr(line, "sku", None)) or _text(getattr(line, "external_sku", None)) or _text(getattr(line, "seller_sku", None))
def _line_platform(line: Any) -> str: return _text(getattr(line, "platform", None) or getattr(line, "marketplace", None)).lower()
def _line_quantity(line: Any) -> int:
    for attr in ("quantity", "qty", "quantity_sold", "qty_sold"):
        qty = _safe_int(getattr(line, attr, None), 0)
        if qty: return abs(qty)
    return 1

def _line_type(line: Any) -> str: return _text(getattr(line, "transaction_type", None) or getattr(line, "type", None) or getattr(line, "status", None)).lower()
def is_sale(line: Any) -> bool:
    value = _line_type(line)
    if value == "pending" and _text(getattr(line, "fulfillment_type", None)).upper() == "FBM": return True
    if not value: return True
    return any(token in value for token in SALE_TYPES)
def _is_return(line: Any) -> bool: return any(token in _line_type(line) for token in RETURN_TYPES)

def _find_listing_for_line(line: Any):
    sku = _line_sku(line)
    if not sku: return None
    query = MarketplaceListing.query.filter(MarketplaceListing.is_active == True, MarketplaceListing.external_sku == sku)
    if _line_platform(line): query = query.join(MarketplaceListing.store).filter(MarketplaceListing.store.has())
    return query.order_by(MarketplaceListing.warehouse_stock_id.is_(None), MarketplaceListing.updated_at.desc(), MarketplaceListing.id.desc()).first()

def mutate_warehouse_stock_from_order_line(line: Any, source: str = "governed_order_bridge") -> dict[str, Any]:
    key = _line_idempotency_key(line)
    if _already_mutated(line, key): return {"success": True, "skipped": True, "reason": "already_mutated", "reference_id": key}
    listing = _find_listing_for_line(line)
    if not listing or not listing.warehouse_stock_id: return {"success": False, "skipped": True, "reason": "no_linked_marketplace_listing", "sku": _line_sku(line), "reference_id": key}
    stock = db.session.query(WarehouseStock).filter(WarehouseStock.id == listing.warehouse_stock_id).with_for_update().first()
    if not stock: return {"success": False, "skipped": True, "reason": "warehouse_stock_missing", "warehouse_stock_id": listing.warehouse_stock_id, "reference_id": key}
    if _already_mutated(line, key): return {"success": True, "skipped": True, "reason": "already_mutated_after_lock", "warehouse_stock_id": stock.id, "reference_id": key}
    qty = _line_quantity(line)
    before_available, before_reserved, before_allocated = _safe_int(stock.available_quantity), _safe_int(stock.reserved_quantity), _safe_int(stock.allocated_quantity)
    if _is_return(line): after_available, transaction_type, adjustment_type = before_available + qty, "return", "increase"
    elif is_sale(line): after_available, transaction_type, adjustment_type = max(0, before_available - qty), "sale", "decrease"
    else: return {"success": False, "skipped": True, "reason": "unsupported_order_line_type", "line_type": _line_type(line), "reference_id": key}
    stock.available_quantity = after_available; stock.updated_at = datetime.utcnow()
    if hasattr(line, "status"): line.status = "processed"
    if hasattr(line, "processed_at"): line.processed_at = datetime.utcnow()
    if hasattr(line, "error_message"): line.error_message = None
    if hasattr(line, "updated_at"): line.updated_at = datetime.utcnow()
    linked = MarketplaceListing.query.filter(MarketplaceListing.warehouse_stock_id == stock.id).all()
    for linked_listing in linked:
        if hasattr(linked_listing, "push_state"): linked_listing.push_state = "pending_group_reconcile"
        if hasattr(linked_listing, "last_sync_status"): linked_listing.last_sync_status = "pending_group_reconcile"
        if hasattr(linked_listing, "updated_at"): linked_listing.updated_at = datetime.utcnow()
    db.session.add(StockLedgerEntry(warehouse_stock_id=stock.id, transaction_type=transaction_type, adjustment_type=adjustment_type, available_quantity_before=before_available, available_quantity_after=after_available, reserved_quantity_before=before_reserved, reserved_quantity_after=before_reserved, allocated_quantity_before=before_allocated, allocated_quantity_after=before_allocated, on_order_quantity_before=0, on_order_quantity_after=0, pending_receipt_qty_before=0, pending_receipt_qty_after=0, quarantined_quantity_before=0, quarantined_quantity_after=0, reference_type="marketplace_order", reference_id=key, reason=f"{source}: marketplace order updated grouped warehouse stock", source_system="marketplace", update_source=source))
    group_id = getattr(stock, "master_product_group_id", None); is_group_controlled = bool(getattr(stock, "is_group_controlled", False)); should_reconcile_group = bool(group_id and is_group_controlled)
    db.session.commit()
    return {"success": True, "skipped": False, "sku": stock.sku, "warehouse_stock_id": stock.id, "group_id": group_id, "is_group_controlled": is_group_controlled, "quantity": qty, "available_before": before_available, "available_after": after_available, "affected_listings": len(linked), "reference_id": key, "group_reconcile_triggered": should_reconcile_group, "group_reconcile_result": {"success": True, "skipped": True, "reason": "warehouse_stock_mutation_does_not_push_directly", "group_id": group_id, "source": "warehouse_stock_mutation"}}

def _attempt_immediate_mcf_handoff(line: Any) -> dict[str, Any]:
    row_id = _safe_int(getattr(line, "id", None), 0)
    if row_id <= 0: return {"success": False, "skipped": True, "reason": "marketplace_order_row_id_missing"}
    try:
        store = getattr(line, "store", None)
        platform = _text(getattr(store, "platform", None)).lower()
        needs_delivery = not all(_text(getattr(line, field, None)) for field in ("ship_to_name", "ship_to_address", "ship_to_city", "ship_to_postcode", "ship_to_country"))
        hydration = None
        if "ebay" in platform and needs_delivery:
            from services.governed_exact_ebay_order_hydration import hydrate_exact_ebay_order
            hydration = hydrate_exact_ebay_order(store=store, marketplace_order_id=getattr(line, "marketplace_order_id", None), source="webhook_ebay_exact_order_hydration")
            if not hydration.get("success"):
                return {"success": False, "skipped": False, "reason": "automatic_mcf_exact_order_hydration_failed", "hydration": hydration, "marketplace_order_row_id": row_id}
            db.session.refresh(line)
        from governed_mcf_routes import run_governed_mcf_submission
        result = run_governed_mcf_submission(row_id, auto_release=True, form_data={}, actor_user=None)
        if hydration is not None and isinstance(result, dict): result["exact_order_hydration"] = hydration
        return result
    except Exception as exc:
        return {"success": False, "skipped": False, "reason": "automatic_mcf_handoff_failed", "error": str(exc), "marketplace_order_row_id": row_id}

def process_exact_marketplace_order_line(line: Any, source: str = "governed_exact_order") -> dict[str, Any]:
    if line is None: return {"success": False, "skipped": True, "reason": "marketplace_order_missing"}
    if getattr(line, "processed_at", None): return {"success": True, "skipped": True, "reason": "already_processed", "order_id": getattr(line, "marketplace_order_id", None)}
    fulfillment = _text(getattr(line, "fulfillment_type", None)).upper()
    if fulfillment in {"FBA", "AFN"}:
        line.status = "processed"; line.processed_at = datetime.utcnow()
        if hasattr(line, "updated_at"): line.updated_at = datetime.utcnow()
        db.session.commit()
        return {"success": True, "skipped": False, "processed": True, "stock_mutated": False, "inventory_authority": "AmazonFBAInventory", "fulfillment_type": fulfillment, "order_id": getattr(line, "marketplace_order_id", None), "warehouse_stock_id": getattr(line, "warehouse_stock_id", None)}
    should_attempt_mcf = bool(is_sale(line) and not _is_return(line))
    result = mutate_warehouse_stock_from_order_line(line, source=source)
    if should_attempt_mcf and result.get("success") and not result.get("skipped"): result["mcf_handoff"] = _attempt_immediate_mcf_handoff(line)
    return result

def mutate_recent_marketplace_order_lines(limit: int = 100, source: str = "governed_order_bridge") -> dict[str, Any]:
    from models import MarketplaceOrder
    candidates = MarketplaceOrder.query.filter(MarketplaceOrder.status == "pending").filter(MarketplaceOrder.fulfillment_type == "FBM").filter(MarketplaceOrder.warehouse_stock_id.isnot(None)).order_by(MarketplaceOrder.id.desc()).limit(limit).all()
    results=[]; mutated=0; skipped=0
    for line in candidates:
        result=mutate_warehouse_stock_from_order_line(line, source=source); results.append(result)
        if result.get("success") and not result.get("skipped"): mutated += 1
        else: skipped += 1
    return {"success": True, "governed": True, "source": source, "authority": "MarketplaceOrder", "checked": len(candidates), "mutated": mutated, "skipped": skipped, "results": results[:50]}

def replay_failed_grouped_marketplace_orders(limit: int = 100, source: str = "governed_failed_order_replay") -> dict[str, Any]:
    from models import MarketplaceOrder
    rows = MarketplaceOrder.query.filter(MarketplaceOrder.status == "failed").order_by(MarketplaceOrder.id.desc()).limit(limit).all()
    checked=0; replayed=0; skipped=0; results=[]
    for order in rows:
        checked += 1; key=_line_idempotency_key(order)
        if _already_mutated(order,key): skipped+=1; results.append({"order_id":getattr(order,"marketplace_order_id",None),"sku":getattr(order,"sku",None),"skipped":True,"reason":"already_mutated"}); continue
        listing=_find_listing_for_line(order)
        if not listing or not listing.warehouse_stock_id: skipped+=1; results.append({"order_id":getattr(order,"marketplace_order_id",None),"sku":getattr(order,"sku",None),"skipped":True,"reason":"still_not_linked_to_warehouse"}); continue
        stock=db.session.get(WarehouseStock,listing.warehouse_stock_id)
        if not stock: skipped+=1; results.append({"order_id":getattr(order,"marketplace_order_id",None),"sku":getattr(order,"sku",None),"skipped":True,"reason":"warehouse_stock_missing"}); continue
        qty=_line_quantity(order)
        if int(stock.sellable_quantity or 0)<qty: skipped+=1; results.append({"order_id":getattr(order,"marketplace_order_id",None),"sku":getattr(order,"sku",None),"warehouse_stock_id":stock.id,"available":int(stock.sellable_quantity or 0),"required":qty,"skipped":True,"reason":"insufficient_current_stock"}); continue
        result=mutate_warehouse_stock_from_order_line(order,source=source)
        if result.get("success") and not result.get("skipped"):
            order.status="stock_applied_pending_reconcile"; order.error_message=None
            if hasattr(order,"updated_at"): order.updated_at=datetime.utcnow()
            db.session.commit(); replayed+=1
        else: skipped+=1
        results.append({"order_id":getattr(order,"marketplace_order_id",None),"sku":getattr(order,"sku",None),"result":result})
    return {"success":True,"governed":True,"source":source,"checked":checked,"replayed":replayed,"skipped":skipped,"results":results[:50]}
