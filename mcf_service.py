"""
MCF Service - Multi-Channel Fulfillment Engine

Phase 2: FBA as the Multi-Channel Fulfillment Engine

This service handles:
1. Creating MCF orders via Amazon Fulfillment Outbound API
2. Mapping external SKUs to FBA SKUs
3. Getting MCF shipping estimates with real pricing
4. Tracking MCF order status and shipments
5. Calculating fees for profit analysis

MCF Pricing Rules:
- First unit shipping fee + additional unit fee for each extra item
- Weight-based handling fees
- Per-shipment fees

Critical: FBA orders do NOT deduct warehouse stock - Amazon holds the inventory.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from app import db
from models import (
    MCFOrder, MCFOrderItem, MarketplaceOrder, AmazonFBAInventory,
    MarketplaceListing, Store, WarehouseStock, OrderFees,
    ListingMarginConfig
)
from amazon_rest_api import AmazonRestAPIClient

logger = logging.getLogger(__name__)


class MCFShippingSpeed:
    """MCF Shipping speed options"""
    STANDARD = 'Standard'
    EXPEDITED = 'Expedited'
    PRIORITY = 'Priority'
    
    @classmethod
    def all(cls):
        return [cls.STANDARD, cls.EXPEDITED, cls.PRIORITY]


class MCFFeeCalculator:
    """
    Calculate MCF fees based on Amazon's pricing structure.
    
    MCF Pricing (UK, approximate as of 2024):
    - Standard: £4.49 first unit, +£0.90 per additional unit
    - Expedited: £5.99 first unit, +£1.20 per additional unit
    - Priority: £8.99 first unit, +£1.80 per additional unit
    
    Plus:
    - Weight handling: £0.25-£0.50 per kg over 0.5kg
    - Per-shipment fee: £0.50
    """
    
    MCF_FEES = {
        'Standard': {'first_unit': 4.49, 'additional_unit': 0.90, 'per_shipment': 0.50},
        'Expedited': {'first_unit': 5.99, 'additional_unit': 1.20, 'per_shipment': 0.75},
        'Priority': {'first_unit': 8.99, 'additional_unit': 1.80, 'per_shipment': 1.00},
    }
    
    WEIGHT_HANDLING_PER_KG = 0.35
    WEIGHT_HANDLING_THRESHOLD_KG = 0.5
    
    @classmethod
    def calculate_item_fee(cls, quantity: int, shipping_speed: str = 'Standard', 
                          weight_kg: float = 0.0) -> Dict:
        """
        Calculate MCF fee for an item with given quantity.
        
        Args:
            quantity: Number of units
            shipping_speed: Standard, Expedited, or Priority
            weight_kg: Item weight in kg (for weight handling)
            
        Returns:
            Dict with first_unit_fee, additional_unit_fee, total_fee, weight_handling
        """
        fees = cls.MCF_FEES.get(shipping_speed, cls.MCF_FEES['Standard'])
        
        first_unit_fee = fees['first_unit']
        additional_unit_fee = fees['additional_unit']
        
        if quantity <= 0:
            return {
                'first_unit_fee': 0,
                'additional_unit_fee': 0,
                'total_fee': 0,
                'weight_handling': 0,
                'units': 0
            }
        
        if quantity == 1:
            fulfillment_fee = first_unit_fee
        else:
            fulfillment_fee = first_unit_fee + (additional_unit_fee * (quantity - 1))
        
        weight_handling = 0
        if weight_kg > cls.WEIGHT_HANDLING_THRESHOLD_KG:
            extra_weight = weight_kg - cls.WEIGHT_HANDLING_THRESHOLD_KG
            weight_handling = extra_weight * cls.WEIGHT_HANDLING_PER_KG * quantity
        
        return {
            'first_unit_fee': first_unit_fee,
            'additional_unit_fee': additional_unit_fee,
            'total_fee': fulfillment_fee + weight_handling,
            'weight_handling': weight_handling,
            'units': quantity
        }
    
    @classmethod
    def calculate_order_fee(cls, items: List[Dict], shipping_speed: str = 'Standard') -> Dict:
        """
        Calculate total MCF fees for an order with multiple items.
        
        Args:
            items: List of dicts with 'quantity' and optional 'weight_kg'
            shipping_speed: Shipping speed for all items
            
        Returns:
            Dict with per_item_fees, per_shipment_fee, total_fee
        """
        fees = cls.MCF_FEES.get(shipping_speed, cls.MCF_FEES['Standard'])
        per_shipment_fee = fees['per_shipment']
        
        total_units = sum(item.get('quantity', 1) for item in items)
        total_fulfillment = 0
        total_weight_handling = 0
        
        for item in items:
            item_fee = cls.calculate_item_fee(
                item.get('quantity', 1),
                shipping_speed,
                item.get('weight_kg', 0)
            )
            total_fulfillment += item_fee['total_fee']
            total_weight_handling += item_fee['weight_handling']
        
        return {
            'per_shipment_fee': per_shipment_fee,
            'fulfillment_fee': total_fulfillment,
            'weight_handling': total_weight_handling,
            'total_units': total_units,
            'total_fee': per_shipment_fee + total_fulfillment
        }


class MCFService:
    """
    Multi-Channel Fulfillment Service
    
    Manages the complete MCF workflow:
    1. Find FBA listing for external SKU
    2. Create MCF order with Amazon
    3. Track MCF order status
    4. Calculate fees and profit
    """
    
    def __init__(self):
        self.fee_calculator = MCFFeeCalculator()

    def _resolve_mcf_fba_store(self, mcf_order):
        """Use the Amazon store already resolved by governed MCF."""
        store_id = getattr(mcf_order, "fba_store_id", None)

        if store_id:
            store = db.session.get(Store, int(store_id))
            if (
                store is not None
                and bool(getattr(store, "is_active", False))
                and "amazon" in str(getattr(store, "platform", "") or "").lower()
            ):
                return store

        return (
            Store.query
            .filter(Store.is_active == True)
            .filter(Store.platform.ilike("%amazon%"))
            .order_by(Store.id.asc())
            .first()
        )
    
    def find_fba_listing_for_sku(
        self,
        sku: str,
        store_id: int = None,
    ) -> Optional[AmazonFBAInventory]:
        """
        Resolve FBA through the existing Product Linking group.

        Non-grouped and non-FBA stock is never eligible for MCF.
        """
        warehouse_stock = (
            WarehouseStock.query
            .filter_by(sku=sku)
            .first()
        )

        if (
            warehouse_stock is None
            or not warehouse_stock.master_product_group_id
        ):
            return None

        group_members = (
            WarehouseStock.query
            .filter(
                WarehouseStock.master_product_group_id
                == int(warehouse_stock.master_product_group_id)
            )
            .all()
        )

        stock_ids = [int(row.id) for row in group_members]

        amazon_stores = [
            store
            for store in Store.query.filter(
                Store.is_active == True  # noqa: E712
            ).all()
            if "amazon" in str(store.platform or "").lower()
        ]

        amazon_store_ids = [int(store.id) for store in amazon_stores]

        if store_id:
            amazon_store_ids = [
                value
                for value in amazon_store_ids
                if value == int(store_id)
            ]

        if not amazon_store_ids:
            return None

        listings = (
            MarketplaceListing.query
            .filter(
                MarketplaceListing.store_id.in_(amazon_store_ids),
                MarketplaceListing.warehouse_stock_id.in_(stock_ids),
                MarketplaceListing.is_active == True,  # noqa: E712
            )
            .all()
        )

        seller_skus = {
            str(listing.external_sku or "").strip()
            for listing in listings
            if str(listing.external_sku or "").strip()
        }

        fnskus = {
            str(listing.fnsku or "").strip()
            for listing in listings
            if str(listing.fnsku or "").strip()
        }

        if not seller_skus and not fnskus:
            return None

        query = AmazonFBAInventory.query.filter(
            AmazonFBAInventory.store_id.in_(amazon_store_ids),
            AmazonFBAInventory.is_active == True,  # noqa: E712
            AmazonFBAInventory.is_archived == False,  # noqa: E712
            AmazonFBAInventory.mcf_enabled == True,  # noqa: E712
        )

        if seller_skus and fnskus:
            query = query.filter(
                db.or_(
                    AmazonFBAInventory.seller_sku.in_(seller_skus),
                    AmazonFBAInventory.fnsku.in_(fnskus),
                )
            )
        elif seller_skus:
            query = query.filter(
                AmazonFBAInventory.seller_sku.in_(seller_skus)
            )
        else:
            query = query.filter(
                AmazonFBAInventory.fnsku.in_(fnskus)
            )

        return (
            query
            .order_by(
                AmazonFBAInventory.available_quantity.desc(),
                AmazonFBAInventory.updated_at.desc(),
                AmazonFBAInventory.id.desc(),
            )
            .first()
        )

    def check_fba_availability(
        self,
        sku: str,
        quantity: int,
    ) -> Tuple[bool, str, Optional[AmazonFBAInventory]]:
        fba_inventory = self.find_fba_listing_for_sku(sku)

        if not fba_inventory:
            return (
                False,
                f"No linked FBA inventory found for SKU: {sku}",
                None,
            )

        available_qty = int(
            fba_inventory.available_quantity or 0
        )

        if available_qty < int(quantity or 0):
            return (
                False,
                (
                    "Insufficient FBA inventory: "
                    f"{available_qty} available, {quantity} needed"
                ),
                fba_inventory,
            )

        return (
            True,
            f"FBA inventory available: {available_qty}",
            fba_inventory,
        )


    def get_mcf_estimate(self, items: List[Dict], shipping_speed: str = 'Standard',
                        destination_country: str = 'GB') -> Dict:
        """
        Get MCF shipping estimate with fees.
        
        Args:
            items: List of dicts with 'sku', 'quantity', optional 'weight_kg'
            shipping_speed: Standard, Expedited, or Priority
            destination_country: 2-letter country code
            
        Returns:
            Dict with estimated fees, availability, and delivery dates
        """
        result = {
            'available': True,
            'items': [],
            'shipping_speed': shipping_speed,
            'fees': {},
            'estimated_delivery': None,
            'errors': []
        }
        
        all_available = True
        fee_items = []
        
        for item in items:
            sku = item.get('sku')
            quantity = item.get('quantity', 1)
            weight_kg = item.get('weight_kg', 0.3)
            
            available, message, fba_listing = self.check_fba_availability(sku, quantity)
            
            item_result = {
                'sku': sku,
                'quantity': quantity,
                'available': available,
                'message': message,
                'fba_sku': fba_listing.seller_sku if fba_listing else None,
                'fba_available_qty': fba_listing.available_quantity if fba_listing else 0
            }
            
            if available and fba_listing:
                item_fee = self.fee_calculator.calculate_item_fee(quantity, shipping_speed, weight_kg)
                item_result['fee'] = item_fee
                fee_items.append({'quantity': quantity, 'weight_kg': weight_kg})
            else:
                all_available = False
                result['errors'].append(message)
            
            result['items'].append(item_result)
        
        result['available'] = all_available
        
        if all_available and fee_items:
            result['fees'] = self.fee_calculator.calculate_order_fee(fee_items, shipping_speed)
            
            delivery_days = {'Standard': 5, 'Expedited': 3, 'Priority': 2}.get(shipping_speed, 5)
            if destination_country != 'GB':
                delivery_days += 3
            result['estimated_delivery'] = (datetime.utcnow() + timedelta(days=delivery_days)).isoformat()
        
        return result
    
    def create_mcf_order(self, source_order_id: str, source_channel: str, source_store_id: int,
                        items: List[Dict], shipping_address: Dict,
                        shipping_speed: str = 'Standard',
                        order_total: float = 0.0,
                        platform_fees: float = 0.0) -> Tuple[bool, str, Optional[MCFOrder]]:
        """
        Create an MCF order for external channel fulfillment.
        
        ============================================================
        CRITICAL STOCK PROTECTION RULE:
        MCF orders use FBA inventory ONLY - they NEVER touch warehouse stock.
        
        - DO NOT decrement WarehouseStock.available_quantity
        - DO NOT create StockLedgerEntry for MCF orders
        - DO NOT call warehouse_stock.deduct() or similar methods
        
        FBA inventory is managed by Amazon. When MCF order ships,
        Amazon automatically deducts from their fulfillment center stock.
        Our FBA listing quantities are updated via the normal FBA sync.
        ============================================================
        
        Args:
            source_order_id: Order ID from source channel (eBay, Etsy, etc)
            source_channel: Channel name (eBay, Etsy, TikTok, Website)
            source_store_id: Store ID where order originated
            items: List of dicts with 'sku', 'quantity', 'unit_price', 'product_cost'
            shipping_address: Dict with name, address_line1, city, postcode, country
            shipping_speed: Standard, Expedited, or Priority
            order_total: Total order value charged to customer
            platform_fees: Fees charged by source platform
            
        Returns:
            Tuple of (success, message, mcf_order)
        """
        try:
            fulfillment_order_id = f"MCF-{source_channel[:3].upper()}-{uuid.uuid4().hex[:8].upper()}"
            
            mcf_order = MCFOrder(
                source_order_id=source_order_id,
                source_channel=source_channel,
                source_store_id=source_store_id,
                seller_fulfillment_order_id=fulfillment_order_id,
                displayable_order_id=source_order_id[:50] if source_order_id else fulfillment_order_id,
                destination_name=shipping_address.get('name', ''),
                destination_address_line1=shipping_address.get('address_line1', ''),
                destination_address_line2=shipping_address.get('address_line2', ''),
                destination_city=shipping_address.get('city', ''),
                destination_state=shipping_address.get('state', ''),
                destination_postcode=shipping_address.get('postcode', ''),
                destination_country=shipping_address.get('country', 'GB'),
                destination_phone=shipping_address.get('phone', ''),
                shipping_speed=shipping_speed,
                displayable_comment=f"Order from {source_channel}: {source_order_id}",
                status='pending',
                order_total=order_total,
                platform_fees=platform_fees,
                currency='GBP'
            )
            
            db.session.add(mcf_order)
            db.session.flush()
            
            total_product_cost = 0
            fee_items = []
            
            for item_data in items:
                sku = item_data.get('sku')
                quantity = item_data.get('quantity', 1)
                unit_price = item_data.get('unit_price', 0)
                product_cost = item_data.get('product_cost', 0)
                
                available, message, fba_listing = self.check_fba_availability(sku, quantity)
                
                if not available:
                    db.session.rollback()
                    return False, message, None
                
                item_fee = self.fee_calculator.calculate_item_fee(
                    quantity, shipping_speed, item_data.get('weight_kg', 0.3)
                )
                
                mcf_item = MCFOrderItem(
                    mcf_order_id=mcf_order.id,
                    source_sku=sku,
                    fba_listing_id=None,
                    fba_sku=fba_listing.seller_sku,
                    asin=fba_listing.asin,
                    fnsku=fba_listing.fnsku,
                    quantity=quantity,
                    unit_price=unit_price,
                    product_cost=product_cost,
                    mcf_fulfillment_fee=item_fee['total_fee'],
                    mcf_first_unit_fee=item_fee['first_unit_fee'],
                    mcf_additional_unit_fee=item_fee['additional_unit_fee'],
                    status='pending'
                )
                
                db.session.add(mcf_item)
                total_product_cost += product_cost * quantity
                fee_items.append({'quantity': quantity, 'weight_kg': item_data.get('weight_kg', 0.3)})
            
            order_fees = self.fee_calculator.calculate_order_fee(fee_items, shipping_speed)
            mcf_order.mcf_per_shipment_fee = order_fees['per_shipment_fee']
            mcf_order.mcf_fulfillment_fee = order_fees['fulfillment_fee']
            mcf_order.total_mcf_fee = order_fees['total_fee']
            mcf_order.product_cost = total_product_cost
            
            mcf_order.calculate_totals()
            
            db.session.commit()
            
            logger.info(f"Created MCF order {fulfillment_order_id} for {source_channel} order {source_order_id}")
            
            return True, f"MCF order created: {fulfillment_order_id}", mcf_order
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Error creating MCF order: {str(e)}")
            return False, f"Error creating MCF order: {str(e)}", None
    
    def submit_mcf_to_amazon(self, mcf_order: MCFOrder) -> Tuple[bool, str]:
        """
        Submit MCF order to Amazon Fulfillment Outbound API.
        
        Uses the FBA store's credentials to call the Fulfillment Outbound API.
        """
        try:
            fba_store = self._resolve_mcf_fba_store(mcf_order)
            if not fba_store:
                return False, "No active Amazon FBA store configured"
            
            credentials = fba_store.amazon_credentials
            if not credentials:
                return False, "Amazon FBA credentials not configured"
            
            api_client = AmazonRestAPIClient(credentials, 'A1F83G8C2ARO7P')
            
            items_payload = []
            for item in mcf_order.items.all():
                items_payload.append({
                    'sellerSku': item.fba_sku,
                    'sellerFulfillmentOrderItemId': f"{mcf_order.seller_fulfillment_order_id}-{item.id}",
                    'quantity': item.quantity,
                    'perUnitDeclaredValue': {
                        'currencyCode': 'GBP',
                        'value': str(item.unit_price or 0)
                    }
                })
            
            payload = {
                'sellerFulfillmentOrderId': mcf_order.seller_fulfillment_order_id,
                'displayableOrderId': mcf_order.displayable_order_id,
                'displayableOrderDate': mcf_order.created_at.isoformat() + 'Z',
                'displayableOrderComment': mcf_order.displayable_comment or '',
                'shippingSpeedCategory': mcf_order.shipping_speed.upper(),
                'destinationAddress': {
                    'name': mcf_order.destination_name or 'Customer',
                    'addressLine1': mcf_order.destination_address_line1 or '',
                    'addressLine2': mcf_order.destination_address_line2 or '',
                    'city': mcf_order.destination_city or '',
                    'stateOrRegion': mcf_order.destination_state or '',
                    'postalCode': mcf_order.destination_postcode or '',
                    'countryCode': mcf_order.destination_country or 'GB',
                    'phone': mcf_order.destination_phone or ''
                },
                'items': items_payload
            }
            
            success, data, error = api_client._make_request(
                'POST',
                '/fba/outbound/2020-07-01/fulfillmentOrders',
                json_data=payload
            )
            
            if success:
                mcf_order.status = 'submitted'
                mcf_order.amazon_status = 'RECEIVED'
                mcf_order.amazon_status_updated_at = datetime.utcnow()
                db.session.commit()
                
                logger.info(f"MCF order {mcf_order.seller_fulfillment_order_id} submitted to Amazon")
                return True, "MCF order submitted to Amazon successfully"
            else:
                mcf_order.status = 'failed'
                mcf_order.last_error = error
                mcf_order.retry_count += 1
                db.session.commit()
                
                logger.error(f"Failed to submit MCF order to Amazon: {error}")
                return False, f"Failed to submit to Amazon: {error}"
                
        except Exception as e:
            mcf_order.status = 'failed'
            mcf_order.last_error = str(e)
            mcf_order.retry_count += 1
            db.session.commit()
            
            logger.error(f"Error submitting MCF order to Amazon: {str(e)}")
            return False, f"Error: {str(e)}"
    
    def get_mcf_order_status(self, mcf_order: MCFOrder) -> Tuple[bool, Dict]:
        """
        Get updated status for an MCF order from Amazon.
        """
        try:
            fba_store = self._resolve_mcf_fba_store(mcf_order)
            if not fba_store:
                return False, {'error': 'No active Amazon FBA store'}
            
            credentials = fba_store.amazon_credentials
            if not credentials:
                return False, {'error': 'Amazon FBA credentials not configured'}
            
            api_client = AmazonRestAPIClient(credentials, 'A1F83G8C2ARO7P')
            
            success, data, error = api_client._make_request(
                'GET',
                f'/fba/outbound/2020-07-01/fulfillmentOrders/{mcf_order.seller_fulfillment_order_id}'
            )
            
            if success and data:
                payload = data.get('payload', {})
                fulfillment_order = payload.get('fulfillmentOrder', {})
                
                mcf_order.amazon_status = fulfillment_order.get('fulfillmentOrderStatus')
                mcf_order.amazon_status_updated_at = datetime.utcnow()
                
                if mcf_order.amazon_status in ['COMPLETE', 'COMPLETE_PARTIALLED']:
                    mcf_order.status = 'completed'
                elif mcf_order.amazon_status in ['CANCELLED', 'INVALID']:
                    mcf_order.status = 'cancelled'
                elif mcf_order.amazon_status in ['PLANNING', 'PROCESSING']:
                    mcf_order.status = 'processing'
                
                shipments = payload.get('fulfillmentShipments', [])
                if shipments:
                    first_shipment = shipments[0]
                    mcf_order.carrier = first_shipment.get('carrierCode')
                    mcf_order.tracking_number = first_shipment.get('trackingNumber')
                    
                    ship_date = first_shipment.get('shipDate')
                    if ship_date:
                        mcf_order.ship_date = datetime.fromisoformat(ship_date.replace('Z', '+00:00'))
                    
                    est_arrival = first_shipment.get('estimatedArrivalDate')
                    if est_arrival:
                        mcf_order.estimated_arrival_date = datetime.fromisoformat(est_arrival.replace('Z', '+00:00'))
                
                db.session.commit()
                
                return True, {
                    'status': mcf_order.amazon_status,
                    'carrier': mcf_order.carrier,
                    'tracking_number': mcf_order.tracking_number,
                    'ship_date': mcf_order.ship_date.isoformat() if mcf_order.ship_date else None,
                    'estimated_arrival': mcf_order.estimated_arrival_date.isoformat() if mcf_order.estimated_arrival_date else None
                }
            else:
                return False, {'error': error or 'Failed to get status'}
                
        except Exception as e:
            logger.error(f"Error getting MCF order status: {str(e)}")
            return False, {'error': str(e)}
    
    def cancel_mcf_order(self, mcf_order: MCFOrder) -> Tuple[bool, str]:
        """
        Cancel an MCF order with Amazon.
        """
        try:
            if mcf_order.status in ['completed', 'cancelled']:
                return False, f"Cannot cancel order in status: {mcf_order.status}"
            
            fba_store = self._resolve_mcf_fba_store(mcf_order)
            if not fba_store:
                return False, "No active Amazon FBA store"
            
            credentials = fba_store.amazon_credentials
            if not credentials:
                return False, "Amazon FBA credentials not configured"
            
            api_client = AmazonRestAPIClient(credentials, 'A1F83G8C2ARO7P')
            
            success, data, error = api_client._make_request(
                'PUT',
                f'/fba/outbound/2020-07-01/fulfillmentOrders/{mcf_order.seller_fulfillment_order_id}/cancel'
            )
            
            if success:
                mcf_order.status = 'cancelled'
                mcf_order.amazon_status = 'CANCELLED'
                mcf_order.amazon_status_updated_at = datetime.utcnow()
                db.session.commit()
                
                logger.info(f"MCF order {mcf_order.seller_fulfillment_order_id} cancelled")
                return True, "MCF order cancelled successfully"
            else:
                return False, f"Failed to cancel: {error}"
                
        except Exception as e:
            logger.error(f"Error cancelling MCF order: {str(e)}")
            return False, f"Error: {str(e)}"


class OrderFulfillmentRouter:
    """
    Routes orders to correct fulfillment path: FBA (MCF) or FBM (warehouse).
    
    Decision logic:
    1. Check if SKU has an active FBA listing with MCF enabled
    2. Check FBA inventory availability
    3. Route to MCF if FBA available, otherwise fall back to FBM
    
    CRITICAL STOCK HANDLING DIFFERENCES:
    
    FBA/MCF Path:
    - Uses FBA inventory at Amazon fulfillment centers
    - NO warehouse stock deduction
    - NO StockLedgerEntry created
    - FBA quantities updated via normal Amazon sync
    
    FBM Path:
    - Uses warehouse stock (WarehouseStock model)
    - DEDUCTS warehouse available_quantity
    - CREATES StockLedgerEntry for audit trail
    - Triggers push to connected marketplaces
    
    The caller is responsible for handling FBM stock deduction
    via the marketplace_order_processor or similar service.
    """
    
    def __init__(self):
        self.mcf_service = MCFService()
    
    def determine_fulfillment_type(self, sku: str, quantity: int = 1) -> Tuple[str, str]:
        """
        Determine the best fulfillment type for a SKU.
        
        Returns:
            Tuple of (fulfillment_type, reason)
            fulfillment_type: 'FBA' or 'FBM'
        """
        available, message, fba_listing = self.mcf_service.check_fba_availability(sku, quantity)
        
        if available:
            return 'FBA', f"FBA inventory available ({fba_listing.available_quantity} units)"
        
        warehouse_stock = WarehouseStock.query.filter_by(sku=sku).first()
        if warehouse_stock and warehouse_stock.sellable_quantity >= quantity:
            return 'FBM', f"Warehouse stock available ({warehouse_stock.sellable_quantity} units)"
        
        if warehouse_stock:
            return 'FBM', f"Insufficient stock (warehouse: {warehouse_stock.sellable_quantity}, needed: {quantity})"
        
        return 'FBM', "No inventory source found"
    
    def route_order(self, source_order_id: str, source_channel: str, source_store_id: int,
                   items: List[Dict], shipping_address: Dict,
                   order_total: float = 0.0, platform_fees: float = 0.0) -> Dict:
        """
        Route an order to the appropriate fulfillment path.
        
        For FBA items: Creates MCF order (no warehouse deduction)
        For FBM items: Creates MarketplaceOrder (deducts warehouse stock)
        
        Returns:
            Dict with fulfillment results
        """
        result = {
            'source_order_id': source_order_id,
            'source_channel': source_channel,
            'fba_items': [],
            'fbm_items': [],
            'mcf_order': None,
            'marketplace_orders': [],
            'errors': []
        }
        
        for item in items:
            sku = item.get('sku')
            quantity = item.get('quantity', 1)
            
            fulfillment_type, reason = self.determine_fulfillment_type(sku, quantity)
            item['fulfillment_type'] = fulfillment_type
            item['fulfillment_reason'] = reason
            
            if fulfillment_type == 'FBA':
                result['fba_items'].append(item)
            else:
                result['fbm_items'].append(item)
        
        if result['fba_items']:
            success, message, mcf_order = self.mcf_service.create_mcf_order(
                source_order_id=source_order_id,
                source_channel=source_channel,
                source_store_id=source_store_id,
                items=result['fba_items'],
                shipping_address=shipping_address,
                order_total=sum(i.get('unit_price', 0) * i.get('quantity', 1) for i in result['fba_items']),
                platform_fees=platform_fees * (len(result['fba_items']) / len(items)) if items else 0
            )
            
            if success:
                result['mcf_order'] = mcf_order.to_dict()
            else:
                result['errors'].append(f"MCF creation failed: {message}")
        
        return result


mcf_service = MCFService()
fulfillment_router = OrderFulfillmentRouter()
