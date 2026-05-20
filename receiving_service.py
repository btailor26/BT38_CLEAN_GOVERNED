"""
Warehouse Receiving Service
Implements the "1 warehouse rule" - controls when stock becomes available for multi-channel sale
"""
import logging
from datetime import datetime
from typing import Dict, List, Tuple
from extensions import db
from models import (
    WarehouseStock, WarehouseReceipt, WarehouseReceiptLine,
    StockLedgerEntry, PurchaseOrder, MarketplaceListing
)
from smart_push_service import SmartPushService

logging.basicConfig(level=logging.INFO)


class ReceivingService:
    """Handles warehouse receiving confirmations and marketplace push triggers"""
    
    def create_receipt(self, po_id: int, received_items: List[Dict]) -> WarehouseReceipt:
        """
        Create a warehouse receipt for incoming stock (Step 1: Physical Receipt)
        
        Stock at this stage is NOT available for sale yet - it's pending confirmation.
        
        Args:
            po_id: Purchase order ID
            received_items: List of dicts with {'sku', 'received_qty', 'damaged_qty', 'notes'}
        
        Returns:
            WarehouseReceipt with status='pending'
        """
        try:
            po = PurchaseOrder.query.get(po_id)
            if not po:
                raise ValueError(f"Purchase Order {po_id} not found")
            
            # Generate receipt number
            receipt_count = WarehouseReceipt.query.count()
            receipt_number = f"WR-{datetime.now().strftime('%Y%m%d')}-{receipt_count + 1:04d}"
            
            # Create receipt
            receipt = WarehouseReceipt(
                receipt_number=receipt_number,
                purchase_order_id=po_id,
                status='pending',
                received_date=datetime.now(),
                notes=f"Created from PO {po.po_number}"
            )
            db.session.add(receipt)
            db.session.flush()  # Get receipt ID
            
            # Create receipt lines and update warehouse stock (pending state)
            for item in received_items:
                sku = item['sku']
                received_qty = item.get('received_qty', 0)
                damaged_qty = item.get('damaged_qty', 0)
                
                # Get or create warehouse stock
                warehouse_stock = WarehouseStock.query.filter_by(sku=sku).first()
                if not warehouse_stock:
                    warehouse_stock = WarehouseStock(
                        sku=sku,
                        available_quantity=0,
                        pending_receipt_qty=0,
                        quarantined_quantity=0
                    )
                    db.session.add(warehouse_stock)
                    db.session.flush()
                
                # Create receipt line
                line = WarehouseReceiptLine(
                    receipt_id=receipt.id,
                    sku=sku,
                    warehouse_stock_id=warehouse_stock.id,
                    received_quantity=received_qty,
                    confirmed_quantity=0,  # Not confirmed yet
                    damaged_quantity=damaged_qty,
                    status='pending',
                    notes=item.get('notes')
                )
                db.session.add(line)
                
                # Move stock to PENDING state (NOT available for sale yet)
                old_pending = warehouse_stock.pending_receipt_qty
                old_quarantined = warehouse_stock.quarantined_quantity
                
                warehouse_stock.pending_receipt_qty += received_qty
                warehouse_stock.quarantined_quantity += damaged_qty
                
                # Create ledger entry for pending receipt
                ledger = StockLedgerEntry(
                    warehouse_stock_id=warehouse_stock.id,
                    transaction_type='receipt_pending',
                    adjustment_type='increase',
                    pending_receipt_qty_before=old_pending,
                    pending_receipt_qty_after=warehouse_stock.pending_receipt_qty,
                    quarantined_quantity_before=old_quarantined,
                    quarantined_quantity_after=warehouse_stock.quarantined_quantity,
                    reason=f"Physical receipt - pending confirmation: {receipt_number}",
                    reference_id=receipt_number,
                    reference_type='warehouse_receipt',
                    created_by='system',
                    source_system='warehouse'
                )
                db.session.add(ledger)
                
                logging.info(f"📦 Received (pending): {sku} - {received_qty} units (NOT available for sale yet)")
            
            db.session.commit()
            logging.info(f"✅ Created warehouse receipt: {receipt_number} (status: pending)")
            return receipt
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"❌ Failed to create receipt: {str(e)}")
            raise
    
    def confirm_receipt(self, receipt_id: int, confirmed_by: str = 'system') -> Dict:
        """
        Confirm warehouse receipt - makes stock available for sale (Step 2: Confirmation)
        
        This is the critical gate that controls when stock becomes available for marketplace push.
        Only CONFIRMED stock is pushed to marketplaces.
        
        Args:
            receipt_id: Warehouse receipt ID
            confirmed_by: Who is confirming the receipt
        
        Returns:
            Dict with push results
        """
        try:
            receipt = WarehouseReceipt.query.get(receipt_id)
            if not receipt:
                raise ValueError(f"Receipt {receipt_id} not found")
            
            if receipt.status == 'confirmed':
                logging.warning(f"Receipt {receipt.receipt_number} is already confirmed")
                return {'status': 'already_confirmed', 'pushes': []}
            
            # Update receipt status
            receipt.status = 'confirmed'
            receipt.confirmed_date = datetime.now()
            receipt.confirmed_by = confirmed_by
            
            skus_to_push = []
            
            # Process each line item
            for line in receipt.line_items:
                if line.status == 'pending':
                    warehouse_stock = line.warehouse_stock
                    if not warehouse_stock:
                        logging.warning(f"No warehouse stock found for SKU {line.sku}")
                        continue
                    
                    # Move from PENDING to AVAILABLE (confirms stock is ready for sale)
                    confirmed_qty = line.received_quantity - line.damaged_quantity
                    
                    old_available = warehouse_stock.available_quantity
                    old_pending = warehouse_stock.pending_receipt_qty
                    
                    warehouse_stock.available_quantity += confirmed_qty
                    warehouse_stock.pending_receipt_qty -= line.received_quantity
                    
                    # Ensure pending doesn't go negative
                    if warehouse_stock.pending_receipt_qty < 0:
                        warehouse_stock.pending_receipt_qty = 0
                    
                    # Update line status
                    line.status = 'confirmed'
                    line.confirmed_quantity = confirmed_qty
                    
                    # Create ledger entry for confirmation
                    ledger = StockLedgerEntry(
                        warehouse_stock_id=warehouse_stock.id,
                        transaction_type='receipt_confirmed',
                        adjustment_type='increase',
                        available_quantity_before=old_available,
                        available_quantity_after=warehouse_stock.available_quantity,
                        pending_receipt_qty_before=old_pending,
                        pending_receipt_qty_after=warehouse_stock.pending_receipt_qty,
                        reason=f"Receipt confirmed - now available for sale: {receipt.receipt_number}",
                        reference_id=receipt.receipt_number,
                        reference_type='warehouse_receipt',
                        created_by=confirmed_by,
                        source_system='warehouse'
                    )
                    db.session.add(ledger)
                    
                    logging.info(f"✅ Confirmed: {line.sku} - {confirmed_qty} units NOW AVAILABLE FOR SALE")
                    skus_to_push.append(line.sku)
            
            # Legacy warehouse push orchestration is retired.
            # Commit warehouse confirmation changes only.
            db.session.commit()
            logging.info(
                "Legacy warehouse push orchestration retired for receipt %s; "
                "use governed propagation path.",
                receipt.receipt_number,
            )
            
            # Build compatible push_results structure for UI without enqueueing or marketplace execution.
            push_results = []
            for sku in skus_to_push:
                push_results.append({
                    'sku': sku,
                    'success': False,
                    'message': 'Legacy warehouse push orchestration retired. Use governed propagation path.',
                    'execution_blocked': True,
                    'governed': True,
                    'retired': True,
                    'successful': 0,
                    'failed': 0
                })
            
            # Add summary if multiple SKUs
            if len(skus_to_push) > 1:
                push_results.append({
                    'summary': True,
                    'sku': 'SUMMARY',
                    'success': False,
                    'total_skus': len(skus_to_push),
                    'jobs_enqueued': 0,
                    'successful': 0,
                    'failed': 0,
                    'message': 'Legacy warehouse push orchestration retired. Use governed propagation path.',
                    'execution_blocked': True,
                    'governed': True,
                    'retired': True
                })
            
            return {
                'status': 'confirmed',
                'receipt_number': receipt.receipt_number,
                'confirmed_date': receipt.confirmed_date,
                'pushes': push_results
            }
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"❌ Failed to confirm receipt: {str(e)}")
            raise
    
    def reject_receipt(self, receipt_id: int, reason: str = None) -> None:
        """
        Reject warehouse receipt - returns pending stock to quarantine
        
        Args:
            receipt_id: Warehouse receipt ID
            reason: Rejection reason
        """
        try:
            receipt = WarehouseReceipt.query.get(receipt_id)
            if not receipt:
                raise ValueError(f"Receipt {receipt_id} not found")
            
            receipt.status = 'rejected'
            rejection_note = f"REJECTED: {reason or 'No reason provided'}"
            receipt.notes = f"{receipt.notes}\n{rejection_note}" if receipt.notes else rejection_note
            
            # Move pending stock to quarantine
            for line in receipt.line_items:
                if line.status == 'pending':
                    warehouse_stock = line.warehouse_stock
                    if warehouse_stock:
                        old_pending = warehouse_stock.pending_receipt_qty
                        old_quarantined = warehouse_stock.quarantined_quantity
                        
                        warehouse_stock.pending_receipt_qty -= line.received_quantity
                        warehouse_stock.quarantined_quantity += line.received_quantity
                        
                        if warehouse_stock.pending_receipt_qty < 0:
                            warehouse_stock.pending_receipt_qty = 0
                        
                        line.status = 'rejected'
                        
                        # Create ledger entry
                        ledger = StockLedgerEntry(
                            warehouse_stock_id=warehouse_stock.id,
                            transaction_type='receipt_rejected',
                            adjustment_type='set',
                            pending_receipt_qty_before=old_pending,
                            pending_receipt_qty_after=warehouse_stock.pending_receipt_qty,
                            quarantined_quantity_before=old_quarantined,
                            quarantined_quantity_after=warehouse_stock.quarantined_quantity,
                            reason=f"Receipt rejected: {reason}",
                            reference_id=receipt.receipt_number,
                            reference_type='warehouse_receipt',
                            created_by='system',
                            source_system='warehouse'
                        )
                        db.session.add(ledger)
                        
                        logging.info(f"⚠️ Rejected: {line.sku} - {line.received_quantity} units moved to quarantine")
            
            db.session.commit()
            logging.info(f"✅ Rejected warehouse receipt: {receipt.receipt_number}")
            
        except Exception as e:
            db.session.rollback()
            logging.error(f"❌ Failed to reject receipt: {str(e)}")
            raise
