import time
import logging
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict
from app import app, db
from models import Store, SyncLog, WarehouseStock, MarketplaceListing, StockLedgerEntry, ProductGroup, GroupExternalRef, PushSettings, SkuExternalRef, InventoryItem, FeedStatus
from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from amazon_service import AmazonAPIService
from ebay_service import eBayAPIService

def migrate_ebay_sku_mappings():
    """
    One-time migration to populate sku_external_refs with existing eBay ItemID mappings
    """
    try:
        logging.info("Starting eBay SKU mapping migration...")
        
        # Check if migration has already been run
        existing_count = db.session.query(SkuExternalRef).filter(SkuExternalRef.platform == 'eBay').count()
        if existing_count > 0:
            logging.info(f"Migration already run - found {existing_count} existing eBay SKU mappings")
            return
        
        # Get all numeric eBay ItemIDs from group_external_refs
        from sqlalchemy import text
        numeric_refs = db.session.execute(text("""
            SELECT ger.external_id as ebay_item_id, ii.sku 
            FROM group_external_refs ger 
            JOIN product_groups pg ON ger.group_id = pg.id 
            JOIN inventory_items ii ON ii.group_id = pg.id 
            WHERE ger.platform = 'eBay' 
            AND ger.external_id ~ '^[0-9]+$'
        """)).fetchall()
        
        migrated_count = 0
        for row in numeric_refs:
            ebay_item_id, sku = row
            
            # Create SKU external reference mapping
            sku_ref = SkuExternalRef(
                sku=sku,
                platform='eBay',
                external_item_id=ebay_item_id
            )
            
            try:
                db.session.add(sku_ref)
                db.session.commit()
                migrated_count += 1
            except IntegrityError:
                # Skip duplicates
                db.session.rollback()
                continue
        
        logging.info(f"Successfully migrated {migrated_count} eBay SKU mappings")
        
    except Exception as e:
        logging.error(f"Error in eBay SKU migration: {str(e)}")
        db.session.rollback()

def check_auto_resume_stores():
    """Check for stores that should auto-resume after cooldown period (e.g., rate limit reset)"""
    try:
        now = datetime.utcnow()
        
        # Find stores with auto_resume_at in the past
        stores_to_resume = db.session.query(Store).filter(
            Store.auto_resume_at.isnot(None),
            Store.auto_resume_at <= now
        ).all()
        
        for store in stores_to_resume:
            logging.info(f"Auto-resuming store {store.name} (was paused: {store.pause_reason})")
            
            # Re-enable auto_push
            store.auto_push_enabled = True
            store.sync_status = 'idle'
            store.auto_resume_at = None
            pause_reason = store.pause_reason
            store.pause_reason = None
            
            db.session.commit()
            
            # Log the resume event
            sync_log = SyncLog(
                store_id=store.id,
                status='success',
                message=f'Store auto-resumed after cooldown ({pause_reason})',
                items_synced=0,
                timestamp=now
            )
            db.session.add(sync_log)
            db.session.commit()
            
            logging.info(f"✅ Store {store.name} auto-resumed and ready for sync")
            
    except Exception as e:
        logging.error(f"Error in auto-resume check: {str(e)}")
        db.session.rollback()

def check_amazon_feed_status():
    """Check status of pending Amazon feeds and update marketplace listings"""
    try:
        # Get all pending feeds (not yet completed)
        pending_feeds = db.session.query(FeedStatus).filter(
            FeedStatus.processing_status.in_(['IN_QUEUE', 'IN_PROGRESS'])
        ).all()
        
        if not pending_feeds:
            return
        
        logging.info(f"Checking status of {len(pending_feeds)} pending Amazon feed(s)")
        
        for feed in pending_feeds:
            try:
                # Get the store
                store = db.session.query(Store).filter_by(id=feed.store_id).first()
                if not store:
                    continue
                
                # Initialize Amazon service to get credentials and marketplace
                amazon_service = AmazonAPIService()
                
                # Parse credentials (support both store-level and system-wide)
                credentials = None
                if store.api_key:
                    try:
                        creds = json.loads(store.api_key)
                        credentials = {
                            'refresh_token': creds.get('refresh_token'),
                            'lwa_app_id': creds.get('lwa_app_id'),
                            'lwa_client_secret': creds.get('lwa_client_secret')
                        }
                    except:
                        pass
                
                # Fallback to system config if no store credentials
                if not credentials or not all(credentials.values()):
                    from models import SystemConfig
                    try:
                        config = db.session.query(SystemConfig).filter_by(key='amazon_credentials').first()
                        if config:
                            system_creds = json.loads(config.value)
                            credentials = {
                                'refresh_token': system_creds.get('refresh_token'),
                                'lwa_app_id': system_creds.get('lwa_app_id'),
                                'lwa_client_secret': system_creds.get('lwa_client_secret')
                            }
                    except:
                        pass
                
                if not credentials or not all(credentials.values()):
                    logging.warning(f"No valid credentials for feed {feed.feed_id} - skipping")
                    continue
                
                # Import Feeds client
                try:
                    from sp_api.api import Feeds
                    from sp_api.base import Marketplaces
                    
                    # Use UK marketplace (matching feed submission in amazon_service.py)
                    marketplace = Marketplaces.UK
                    
                    feeds_client = Feeds(
                        marketplace=marketplace,
                        credentials=credentials
                    )
                    
                    # Get feed status
                    feed_response = feeds_client.get_feed(feedId=feed.feed_id)
                    
                    if feed_response.payload:
                        # Log full payload for debugging FATAL feeds
                        logging.debug(f"Feed {feed.feed_id} full payload: {feed_response.payload}")
                        
                        status = feed_response.payload.get('processingStatus')
                        feed.processing_status = status
                        feed.last_checked_at = datetime.utcnow()
                        
                        # Update processing times
                        if status == 'IN_PROGRESS' and not feed.processing_started_at:
                            feed.processing_started_at = datetime.utcnow()
                        
                        # Handle completed feeds
                        if status in ['DONE', 'FATAL', 'CANCELLED']:
                            feed.processing_ended_at = datetime.utcnow()
                            result_doc_id = feed_response.payload.get('resultFeedDocumentId')
                            feed.result_feed_document_id = result_doc_id
                            
                            # Download result document for both DONE and FATAL to get details
                            error_details = None
                            if result_doc_id:
                                try:
                                    # Get result document URL
                                    doc_response = feeds_client.get_feed_document(feedDocumentId=result_doc_id)
                                    if doc_response.payload and 'url' in doc_response.payload:
                                        import requests
                                        result_url = doc_response.payload['url']
                                        result_content = requests.get(result_url, timeout=10).text
                                        
                                        # Parse result content to extract errors
                                        try:
                                            # Try JSON first
                                            result_data = json.loads(result_content)
                                            errors = []
                                            if isinstance(result_data, dict):
                                                if 'errors' in result_data:
                                                    for error in result_data.get('errors', []):
                                                        errors.append(f"{error.get('code', 'ERROR')}: {error.get('message', 'Unknown error')}")
                                                if 'messages' in result_data:
                                                    for msg in result_data.get('messages', []):
                                                        if msg.get('resultCode') != 'Success':
                                                            errors.append(f"{msg.get('resultCode', 'ERROR')}: {msg.get('resultDescription', 'Unknown error')}")
                                            error_details = '; '.join(errors) if errors else result_content[:500]
                                        except json.JSONDecodeError:
                                            # Fallback to raw content
                                            error_details = result_content[:500]
                                        
                                        feed.result_summary = result_content[:1000]
                                except Exception as e:
                                    logging.error(f"Error downloading feed result for {feed.feed_id}: {str(e)}")
                                    error_details = f"Could not download result: {str(e)}"
                            
                            if status == 'DONE':
                                # Check if result contains errors even with DONE status
                                actual_success = True
                                if error_details and ('error' in error_details.lower() or 'failed' in error_details.lower()):
                                    actual_success = False
                                
                                feed.success = actual_success
                                
                                # Update marketplace listing
                                listing = db.session.query(MarketplaceListing).join(
                                    WarehouseStock
                                ).filter(
                                    MarketplaceListing.store_id == store.id,
                                    WarehouseStock.sku == feed.sku
                                ).first()
                                
                                if listing:
                                    if actual_success:
                                        listing.last_push_at = datetime.utcnow()
                                        listing.last_push_status = 'success'
                                        listing.last_push_error = None
                                        listing.push_attempts = 0
                                        logging.info(f"✅ Amazon feed {feed.feed_id} completed successfully for SKU {feed.sku}")
                                    else:
                                        listing.last_push_status = 'error'
                                        listing.last_push_error = error_details or "Feed completed but contained errors"
                                        feed.error_message = error_details or "Feed completed but contained processing errors"
                                        logging.error(f"❌ Amazon feed {feed.feed_id} completed with errors for SKU {feed.sku}: {error_details}")
                            
                            elif status == 'FATAL':
                                feed.success = False
                                feed.error_message = error_details or "Feed processing failed with fatal error"
                                logging.error(f"❌ Amazon feed {feed.feed_id} failed for SKU {feed.sku}: {feed.error_message}")
                            
                            elif status == 'CANCELLED':
                                feed.success = False
                                feed.error_message = "Feed was cancelled"
                                logging.warning(f"⚠️  Amazon feed {feed.feed_id} was cancelled for SKU {feed.sku}")
                        
                        db.session.commit()
                        
                except ImportError:
                    logging.warning("Amazon SP-API not available - skipping feed status check")
                    break
                    
            except Exception as e:
                logging.error(f"Error checking feed {feed.feed_id}: {str(e)}")
                continue
    
    except Exception as e:
        logging.error(f"Error in check_amazon_feed_status: {str(e)}")
        db.session.rollback()

def start_sync_service():
    """Background service that runs sync operations"""
    logging.info("Starting sync service...")
    
    # Run migration on startup (one-time) within app context
    with app.app_context():
        migrate_ebay_sku_mappings()
    
    # Track last reorder check time (run every 10 minutes)
    last_reorder_check = None
    
    while True:
        try:
            with app.app_context():
                # CRITICAL: Detect and recover stuck stores (deadlock prevention)
                detect_and_recover_stuck_stores()
                
                # Check for stores that should auto-resume (e.g., after rate limit cooldown)
                check_auto_resume_stores()
                
                # Check Amazon feed status (ZOHO-style feed tracking)
                check_amazon_feed_status()
                
                # Check reorder points and send notifications (every 10 minutes)
                now = datetime.utcnow()
                if last_reorder_check is None or (now - last_reorder_check).total_seconds() >= 600:  # 10 minutes
                    try:
                        from reorder_service import reorder_monitor
                        logging.info("Running reorder point check...")
                        result = reorder_monitor.check_reorder_points()
                        logging.info(f"Reorder check complete: {result}")
                        last_reorder_check = now
                    except Exception as reorder_error:
                        logging.error(f"Error in reorder check: {str(reorder_error)}")
                
                # Get all active stores that need syncing
                stores = db.session.query(Store).filter(Store.is_active == True).all()
                logging.debug(f"Retrieved {len(stores)} active store(s) from database")
                for s in stores:
                    logging.debug(f"  - Store ID {s.id}: {s.name} ({s.platform})")
                
                for store in stores:
                    # Debug: Log evaluation for each store
                    needs_sync = should_sync(store)
                    logging.debug(f"Store {store.name} ({store.platform}): sync_status={store.sync_status}, should_sync={needs_sync}, last_sync={store.last_sync}")
                    
                    # Skip if already syncing to prevent blocking other stores
                    if store.sync_status == 'syncing':
                        logging.debug(f"Skipping {store.name} - already syncing (prevents blocking other stores)")
                        continue
                    
                    if store.sync_status in ['pending', 'error'] or needs_sync:
                        sync_store(store)
                    else:
                        logging.debug(f"Skipping {store.name} - not yet time to sync")
                
        except Exception as e:
            logging.error(f"Error in sync service: {str(e)}")
        
        # Wait 30 seconds before next sync cycle
        time.sleep(30)

def detect_and_recover_stuck_stores():
    """
    Detect stores stuck in 'syncing' status for too long and reset them.
    Prevents permanent deadlock from crashes or exceptions.
    """
    try:
        # Find stores stuck in syncing for more than 10 minutes (reasonable timeout)
        stuck_timeout = datetime.utcnow() - timedelta(minutes=10)
        
        # Check ALL stores in syncing status first
        all_syncing = db.session.query(Store).filter(Store.sync_status == 'syncing').all()
        logging.debug(f"Deadlock check: Found {len(all_syncing)} store(s) in 'syncing' status")
        
        stuck_stores = db.session.query(Store).filter(
            Store.sync_status == 'syncing',
            Store.last_sync < stuck_timeout
        ).all()
        
        logging.debug(f"Deadlock check: {len(stuck_stores)} store(s) stuck for >10 minutes")
        
        for store in stuck_stores:
            time_stuck = datetime.utcnow() - store.last_sync if store.last_sync else None
            logging.warning(f"DEADLOCK RECOVERY: Store {store.name} stuck in 'syncing' for {time_stuck} - resetting to 'pending' for retry")
            
            store.sync_status = 'pending'
            
            # Log the recovery
            recovery_log = SyncLog()
            recovery_log.store_id = store.id
            recovery_log.status = 'recovered'
            recovery_log.message = f'Deadlock recovery: Store was stuck in syncing status for {time_stuck}, reset to pending for automatic retry'
            db.session.add(recovery_log)
            
        if stuck_stores:
            db.session.commit()
            logging.info(f"Recovered {len(stuck_stores)} stuck store(s)")
            
    except Exception as e:
        logging.error(f"Error in stuck store detection: {str(e)}")
        import traceback
        logging.error(traceback.format_exc())
        db.session.rollback()

def immediate_sync_store(store_id):
    """
    Immediately sync a specific store - called when store is first connected
    Returns (success, message) tuple
    """
    try:
        with app.app_context():
            store = db.session.query(Store).filter(Store.id == store_id).first()
            if not store:
                return False, "Store not found"
            
            logging.info(f"Starting immediate sync for store: {store.name}")
            
            # Perform the sync operation  
            sync_store(store)
            
            # Check if sync was successful
            if store.sync_status == 'active':
                return True, f"Successfully synced store '{store.name}'"
            else:
                return False, f"Sync failed for store '{store.name}'"
                
    except Exception as e:
        logging.error(f"Error in immediate sync for store {store_id}: {str(e)}")
        return False, f"Immediate sync error: {str(e)}"

# Old import functions removed - now using outbound push system

def should_sync(store):
    """Determine if a store should be synced based on last sync time and push settings"""
    if not store.last_sync:
        return True
    
    # Use store-specific push frequency (default 5 minutes if not set)
    frequency_seconds = (store.push_frequency_minutes or 5) * 60
    
    time_diff = datetime.utcnow() - store.last_sync
    return time_diff.total_seconds() > frequency_seconds

def should_push_to_store(store):
    """Determine if inventory should be pushed to this store based on push settings"""
    if not store.is_active or not store.auto_push_enabled:
        return False
    
    # Check if store has exceeded failure threshold
    if (store.auto_disable_on_failures and 
        store.current_failure_count >= store.failure_threshold):
        logging.warning(f"Store {store.name} auto-disabled due to failures")
        return False
    
    return True

def get_stores_by_push_priority():
    """Get active stores ordered by push priority (highest first)"""
    return db.session.query(Store).filter(
        Store.is_active == True,
        Store.auto_push_enabled == True
    ).order_by(desc(Store.push_priority)).all()

def increment_store_failure_count(store):
    """Increment failure count and auto-disable if threshold reached"""
    store.current_failure_count = (store.current_failure_count or 0) + 1
    
    if (store.auto_disable_on_failures and 
        store.current_failure_count >= store.failure_threshold):
        store.auto_push_enabled = False
        logging.warning(f"Auto-disabled push for store {store.name} after {store.current_failure_count} failures")
    
    db.session.commit()

def reset_store_failure_count(store):
    """Reset failure count after successful operation"""
    if store.current_failure_count > 0:
        store.current_failure_count = 0
        db.session.commit()

def create_warehouse_stock_from_import(items_data: List[Dict], store: Store) -> int:
    """
    Create or update warehouse stock entries from imported marketplace inventory data
    Creates: WarehouseStock, InventoryItem (dashboard), and MarketplaceListing (with ASIN/FNSKU for Amazon)
    Returns count of items processed
    """
    processed_count = 0
    
    for item_data in items_data:
        try:
            sku = item_data.get('sku', '').strip()
            if not sku:
                logging.warning(f"Skipping item without SKU: {item_data}")
                continue
            
            # Check if warehouse stock already exists
            existing_stock = WarehouseStock.query.filter_by(sku=sku).first()
            
            # Extract reserved quantity - handle both dict (Amazon) and int (eBay) formats
            reserved_qty = item_data.get('reserved_quantity', 0)
            if isinstance(reserved_qty, dict):
                # Amazon format: extract totalReservedQuantity from dict
                reserved_qty = reserved_qty.get('totalReservedQuantity', 0)
            
            if existing_stock:
                # WAREHOUSE IS AUTHORITATIVE - Only bootstrap when warehouse is 0
                marketplace_qty = item_data.get('quantity', 0)
                
                # DIAGNOSTIC LOGGING to identify why quantities are 0
                if sku in ['EB-KV-GL-30g', 'EB-CLW-BS-LCU_Ma_M', 'EB-FL-CR-50g-X3', 'EB-FL-CR-25g-X3']:
                    logging.warning(f"🔍 DIAGNOSTIC {sku}: warehouse={existing_stock.available_quantity}, marketplace={marketplace_qty}, item_data={item_data}")
                
                if existing_stock.available_quantity == 0 and marketplace_qty > 0:
                    # Bootstrap: warehouse is empty, populate from marketplace
                    existing_stock.available_quantity = marketplace_qty
                    logging.info(f"✅ Bootstrap: Updated warehouse stock for SKU {sku} from 0 to {marketplace_qty} (from {store.platform})")
                elif existing_stock.available_quantity > 0:
                    # Warehouse has stock - keep it authoritative, don't overwrite
                    logging.debug(f"Warehouse stock exists for SKU {sku} - kept warehouse quantity {existing_stock.available_quantity} (warehouse is authoritative)")
                
                # Always update marketplace-specific metadata
                existing_stock.reserved_quantity = reserved_qty  # Marketplace reserved quantity
                existing_stock.last_sync_at = datetime.utcnow()  # Track sync time
                
                # CRITICAL FIX: Auto-sync price from marketplace (eBay, Amazon, etc.)
                marketplace_price = item_data.get('price', 0.0)
                if marketplace_price > 0:
                    existing_stock.unit_cost = marketplace_price
                    logging.info(f"✅ Updated price for SKU {sku}: £{marketplace_price:.2f} (from {store.platform})")
                
                # Update location if we have fulfillment type (Amazon FBA/FBM tracking)
                fulfillment_type = item_data.get('fulfillment_type', '')
                if fulfillment_type in ['FBA', 'FBM']:
                    existing_stock.location = f"Amazon {fulfillment_type}"
            else:
                # Create new warehouse stock entry
                # Set location based on fulfillment type for Amazon (FBA vs FBM tracking)
                fulfillment_type = item_data.get('fulfillment_type', '')
                if fulfillment_type in ['FBA', 'FBM']:
                    warehouse_location = f"Amazon {fulfillment_type}"
                else:
                    warehouse_location = f"From {store.name}"
                
                new_stock = WarehouseStock(
                    sku=sku,
                    available_quantity=item_data.get('quantity', 0),
                    reserved_quantity=reserved_qty,
                    unit_cost=item_data.get('price', 0.0),
                    location=warehouse_location,
                    is_active=True,
                    track_inventory=True,
                    last_sync_at=datetime.utcnow()
                )
                db.session.add(new_stock)
                logging.info(f"Created new warehouse stock for SKU {sku}: {new_stock.available_quantity} units (Location: {warehouse_location})")
            
            # Create/update InventoryItem for dashboard visibility
            existing_item = InventoryItem.query.filter_by(sku=sku).first()
            
            # Get warehouse quantity (authoritative source)
            warehouse_qty = existing_stock.available_quantity if existing_stock else item_data.get('quantity', 0)
            
            if existing_item:
                # Update existing inventory item - sync quantity from WAREHOUSE, not marketplace
                existing_item.name = item_data.get('name', existing_item.name)  # Update name
                existing_item.quantity = warehouse_qty  # Sync from warehouse (authoritative)
                existing_item.price = item_data.get('price', existing_item.price)
                existing_item.updated_at = datetime.utcnow()
                logging.info(f"Updated InventoryItem for SKU {sku} - synced quantity from warehouse: {warehouse_qty}")
            else:
                # Create new inventory item for dashboard display - use warehouse quantity
                new_item = InventoryItem(
                    sku=sku,
                    name=item_data.get('name', sku),
                    quantity=warehouse_qty,  # Use warehouse quantity (authoritative)
                    price=item_data.get('price', 0.0),
                    description=item_data.get('description', f'Imported from {store.platform}')
                )
                db.session.add(new_item)
                logging.info(f"Created new InventoryItem for SKU {sku} with warehouse quantity: {warehouse_qty}")
            
            # Save SKU external reference mapping (for eBay with external_sku)
            external_sku = item_data.get('external_sku')
            external_item_id = item_data.get('external_item_id')
            
            if external_sku and external_sku != sku:
                # We have an external SKU that's different from internal SKU - save the mapping
                from sqlalchemy import text
                mapping_query = text("""
                    INSERT INTO sku_external_refs (sku, platform, external_item_id, external_sku, created_at, updated_at)
                    VALUES (:sku, :platform, :external_item_id, :external_sku, NOW(), NOW())
                    ON CONFLICT (sku, platform) DO UPDATE 
                    SET external_item_id = :external_item_id,
                        external_sku = :external_sku,
                        updated_at = NOW()
                """)
                db.session.execute(mapping_query, {
                    'sku': sku,
                    'platform': store.platform,
                    'external_item_id': external_item_id or '',
                    'external_sku': external_sku
                })
                logging.debug(f"Saved SKU mapping: {sku} → {external_sku} ({store.platform})")
            
            # Create marketplace listing connection.
            # eBay variation-safe identity:
            # - Primary: store + ItemID + external_sku
            # - Fallback: store + ItemID when no external_sku exists
            listing_id_for_match = item_data.get('listing_id', sku)
            external_sku_for_match = item_data.get('external_sku') or sku

            existing_listing = MarketplaceListing.query.filter_by(
                store_id=store.id,
                external_listing_id=listing_id_for_match,
                external_sku=external_sku_for_match
            ).first()

            if not existing_listing and not item_data.get('external_sku'):
                existing_listing = MarketplaceListing.query.filter_by(
                    store_id=store.id,
                    external_listing_id=listing_id_for_match
                ).first()
            
            # CRITICAL FIX: Capture API price to ensure it's always saved when valid
            new_price = item_data.get('price')
            
            if existing_listing:
                # Update existing listing with fresh data (especially title from inventory API)
                # CRITICAL: Also update warehouse_stock_id to link to warehouse stock
                warehouse_stock_id = existing_stock.id if existing_stock else None
                if not warehouse_stock_id:
                    # Need to flush to get the ID for new stock
                    db.session.flush()
                    warehouse_stock_id = new_stock.id
                
                existing_listing.warehouse_stock_id = warehouse_stock_id
                existing_listing.title = item_data.get('name', sku)
                existing_listing.description = item_data.get('description', existing_listing.description)
                existing_listing.external_sku = item_data.get('external_sku', existing_listing.external_sku)  # Update eBay SKU for variations
                
                # CRITICAL FIX: Always update price when API returns valid price (>= £0.99)
                if new_price and new_price >= 0.99:
                    existing_listing.price = new_price
                    logging.debug(f"Updated price for {sku} to £{new_price:.2f} from API")
                
                existing_listing.asin = item_data.get('asin', existing_listing.asin)
                existing_listing.fnsku = item_data.get('fnsku', existing_listing.fnsku)
                existing_listing.item_specifics = item_data.get('item_specifics')  # Update item specifics from import
                
                # CRITICAL FIX: Don't set last_push_quantity/last_push_at during import
                # Import is not a push operation - only actual push operations should update these fields
                # This allows needs_push property to correctly detect quantity changes
                
                # CRITICAL AUTO-RECOVERY: If listing was blocked for missing item specifics, reactivate if specifics now exist
                if existing_listing.push_state == 'blocked' and 'item specific' in (existing_listing.last_push_error or '').lower():
                    if item_data.get('item_specifics'):  # Item specifics are now present
                        existing_listing.push_state = 'active'
                        existing_listing.consecutive_failures = 0
                        existing_listing.last_push_error = None
                        logging.info(f"✅ REACTIVATED listing {sku}: Item specifics now present, cleared blocked state")
                
                logging.info(f"Updated marketplace listing for SKU {sku} on {store.name}")
            else:
                # Determine warehouse_stock_id properly
                warehouse_stock_id = existing_stock.id if existing_stock else None
                if not warehouse_stock_id:
                    # Need to flush to get the ID for new stock
                    db.session.flush()
                    warehouse_stock_id = new_stock.id
                
                # CRITICAL FIX: Don't set last_push_quantity/last_push_at/last_push_status during import
                # Import is not a push operation - let needs_push detect that push is needed
                marketplace_listing = MarketplaceListing(
                    warehouse_stock_id=warehouse_stock_id,
                    store_id=store.id,
                    external_listing_id=item_data.get('listing_id', sku),
                    external_sku=item_data.get('external_sku', sku),  # CRITICAL: Use eBay SKU for variations
                    asin=item_data.get('asin', ''),  # Amazon ASIN
                    fnsku=item_data.get('fnsku', ''),  # Amazon FNSKU
                    title=item_data.get('name', sku),
                    description=item_data.get('description', ''),
                    price=new_price if new_price and new_price >= 0.99 else 0.0,  # CRITICAL FIX: Use API price when valid
                    item_specifics=item_data.get('item_specifics'),  # Store item specifics from import
                    listing_type='single',  # Default classification
                    push_state='active',
                    is_active=True,
                    sync_quantity=True
                )
                db.session.add(marketplace_listing)
                logging.info(f"Created marketplace listing for SKU {sku} on {store.name}")
            
            # BIDIRECTIONAL SYNC: Update warehouse from marketplace if reverse sync is enabled
            try:
                from reverse_sync_coordinator import ReverseSyncCoordinator
                
                # Get the marketplace listing (either existing or newly created)
                current_listing = existing_listing if existing_listing else marketplace_listing
                
                # CRITICAL FIX: Explicitly load the WarehouseStock relationship
                # The warehouse_stock_id is set above, but the relationship won't auto-load
                # We need to explicitly assign the WarehouseStock object for reverse sync to work
                resolved_stock = existing_stock if existing_stock else new_stock
                current_listing.warehouse_stock = resolved_stock
                
                # Get current marketplace quantity
                marketplace_qty = item_data.get('quantity', 0)
                
                # Initialize and call reverse sync coordinator
                reverse_sync = ReverseSyncCoordinator()
                warehouse_updated = reverse_sync.sync_quantity_from_marketplace(
                    marketplace_listing=current_listing,
                    current_marketplace_qty=marketplace_qty,
                    store=store
                )
                
                if warehouse_updated:
                    logging.info(f"✅ Reverse sync: Updated warehouse stock for {sku} from marketplace {store.name}")
                
            except Exception as reverse_sync_error:
                logging.error(f"Error in reverse sync for {sku}: {str(reverse_sync_error)}")
                # Continue processing - reverse sync failure shouldn't block import
            
            # Commit this item immediately to avoid transaction rollback issues
            db.session.commit()
            processed_count += 1
            
        except Exception as e:
            logging.error(f"Error processing imported item {item_data}: {str(e)}")
            # Rollback to recover from the error and continue
            db.session.rollback()
            continue
    
    logging.info(f"Successfully processed {processed_count} warehouse stock entries from import")
    return processed_count

def sync_store(store):
    print("REAL SYNC EXECUTING:", store.name)

    """Push warehouse quantities to marketplace (outbound sync - warehouse authoritative)"""
    logging.info(f"Starting outbound push for store: {store.name}")
    
    # Store original status to restore if needed
    original_status = store.sync_status
    
    try:
        # First, attempt to establish connection if not already connected
        if not attempt_store_connection(store):
            logging.warning(f"Skipping push for {store.name} - connection failed")
            # Connection failed - leave status as is (likely 'error' from attempt_store_connection)
            return
        
        # Update store status to syncing
        store.sync_status = 'syncing'
        db.session.commit()
        
        # Create sync log entry - will be updated to completed or failed
        sync_log = SyncLog()
        sync_log.store_id = store.id
        sync_log.status = 'started'
        sync_log.message = f'Starting inventory import and push for {store.name}'
        sync_log.created_at = datetime.utcnow()
        db.session.add(sync_log)
        db.session.commit()
        
        # Track the sync_log ID to update it in case of errors
        sync_log_id = sync_log.id

        # STEP 1: Import inventory from marketplace into warehouse (inbound)
        imported_count = 0
        import_errors = 0
        
        logging.info(f"PLATFORM_DEBUG: Store '{store.name}' has platform='{store.platform}', lower='{store.platform.lower()}', checking against 'amazon' and 'ebay'")
        
        if store.platform.lower() == 'amazon':
            logging.info(f"Importing Amazon inventory for store: {store.name}")
            try:
                from amazon_service import AmazonAPIService
                region = 'US'  # default
                try:
                    creds = json.loads(store.api_key)
                    region = creds.get('region', 'US')
                except:
                    pass
                    
                amazon_service = AmazonAPIService(marketplace_region=region)
                success, items_data, message = amazon_service.import_inventory_from_amazon(store)
                
                if success and items_data:
                    imported_count = create_warehouse_stock_from_import(items_data, store)
                    logging.info(f"Imported {imported_count} Amazon items into warehouse")
                else:
                    import_errors += 1
                    logging.warning(f"Amazon import failed: {message}")
                    
            except Exception as e:
                import_errors += 1
                logging.error(f"Error importing Amazon inventory: {str(e)}")
        
        elif store.platform.lower() == 'ebay':
            logging.info(f"EBAY_IMPORT_START: Entering eBay import block for store {store.name}")
            logging.info(f"Importing eBay inventory for store: {store.name}")
            try:
                from ebay_service import eBayAPIService
                ebay_service = eBayAPIService()
                success, items_data, message = ebay_service.import_inventory_from_ebay(store)
                
                if success and items_data:
                    imported_count = create_warehouse_stock_from_import(items_data, store)
                    logging.info(f"Imported {imported_count} eBay items into warehouse")
                else:
                    import_errors += 1
                    logging.warning(f"EBAY_IMPORT_FAILED: eBay import failed: {message}")
                    import_errors += 1
                    failed_count += 1
                    
            except Exception as e:
                import_errors += 1
                logging.error(f"EBAY_IMPORT_EXCEPTION: Error importing eBay inventory: {str(e)}")
        
        # STEP 2: Outbound sync (warehouse to marketplace) - platform-specific
        pushed_count = 0
        failed_count = 0
        
        if store.platform.lower() == 'amazon':
            # Use Amazon-specific push service
            logging.info(f"Starting outbound push for store: {store.name}")
            from amazon_service import AmazonAPIService
            
            region = 'US'  # default
            try:
                creds = json.loads(store.api_key)
                region = creds.get('region', 'US')
            except:
                pass
                
            amazon_service = AmazonAPIService(marketplace_region=region)
            
            # Get all active marketplace listings that need push
            listings = MarketplaceListing.query.filter_by(
                store_id=store.id,
                is_active=True,
                sync_quantity=True
            ).all()
            
            logging.info(f"Found {len(listings)} Amazon listings to check for push")
            
            for listing in listings:
                # Amazon doesn't use the eBay push_state system, check quantity changes directly
                if listing.warehouse_stock:
                    # Push if quantity changed or never pushed before
                    warehouse_qty = listing.warehouse_stock.available_quantity
                    should_push = (listing.last_push_quantity != warehouse_qty) or (listing.last_push_at is None)
                    
                    if should_push:
                        # CRITICAL FIX: Get InventoryItem for the SKU (sync_inventory_to_amazon expects InventoryItem, not WarehouseStock)
                        inventory_item = InventoryItem.query.filter_by(sku=listing.warehouse_stock.sku).first()
                        if inventory_item:
                            success, message = amazon_service.sync_inventory_to_amazon(store, inventory_item)
                            if success:
                                pushed_count += 1
                                # Update last_push tracking
                                listing.last_push_quantity = warehouse_qty
                                listing.last_push_at = datetime.utcnow()
                            else:
                                failed_count += 1
                                logging.error(f"Failed to push {listing.external_sku}: {message}")
                        else:
                            failed_count += 1
                            logging.error(f"Failed to push {listing.external_sku}: No InventoryItem found for SKU {listing.warehouse_stock.sku}")
            
            logging.info(f"Amazon push completed for {store.name}: {pushed_count} successful, {failed_count} failed")
            
        elif store.platform.lower() == 'ebay':
            # Use eBay-specific smart push service
            logging.info(f"Starting outbound push for store: {store.name}")
            from smart_push_service import smart_push_service
            
            # Smart push only processes pushable listings (no broken/blocked ones)
            push_results = smart_push_service.push_to_store(store)
            
            pushed_count = push_results['successful']
            failed_count = push_results['failed']
            
            logging.info(f"Smart push completed for {store.name}: {pushed_count} successful, {failed_count} failed, {push_results['skipped_blocked']} skipped")
        
        # Update store sync status
        # Truthful sync result:
        # A sync is only active if there were no failures/import errors.
        # If an API import failed, do NOT mark the store as active/completed.
        if failed_count == 0 and import_errors == 0:
            store.sync_status = 'active'
            store.last_sync = datetime.utcnow()
            reset_store_failure_count(store)
        else:
            store.sync_status = 'partial' if (imported_count > 0 or pushed_count > 0) else 'error'
            increment_store_failure_count(store)
        
        # Update sync log
        sync_log.status = 'completed'
        sync_log.message = f'Imported {imported_count} items, pushed {pushed_count} items, {failed_count} failures, {import_errors} import errors'
        sync_log.items_synced = pushed_count
        
        db.session.commit()
        
        logging.info(f"Sync completed for store: {store.name} - imported {imported_count} items, pushed {pushed_count} items, {failed_count} failures")
        
    except Exception as e:
        logging.error(f"Error pushing to store {store.name}: {str(e)}")
        
        try:
            # Update store status to error
            store.sync_status = 'error'
            increment_store_failure_count(store)
            
            # CRITICAL FIX: Update the original sync_log instead of creating a new one
            # This prevents stuck 'started' logs
            if 'sync_log_id' in locals():
                existing_log = db.session.query(SyncLog).filter_by(id=sync_log_id).first()
                if existing_log:
                    existing_log.status = 'failed'
                    existing_log.message = f'Push failed: {str(e)}'
                    logging.info(f"Updated sync_log {sync_log_id} to failed status")
                else:
                    # Fallback: create error log if we can't find the original
                    error_log = SyncLog()
                    error_log.store_id = store.id
                    error_log.status = 'failed'
                    error_log.message = f'Push failed: {str(e)}'
                    error_log.created_at = datetime.utcnow()
                    db.session.add(error_log)
            else:
                # No sync_log_id tracked, create new error log
                error_log = SyncLog()
                error_log.store_id = store.id
                error_log.status = 'failed'
                error_log.message = f'Push failed: {str(e)}'
                error_log.created_at = datetime.utcnow()
                db.session.add(error_log)
            
            db.session.commit()
        except Exception as commit_error:
            logging.error(f"Error logging sync failure for {store.name}: {str(commit_error)}")
            db.session.rollback()
    
    finally:
        # CRITICAL: Always reset sync_status to prevent permanent deadlock
        # This guarantees status is never stuck at 'syncing', even on crashes/errors
        try:
            if store.sync_status == 'syncing':
                logging.warning(f"Store {store.name} still in 'syncing' status - resetting to 'error' (likely crashed)")
                store.sync_status = 'error'
                store.last_sync = datetime.utcnow()
                db.session.commit()
        except Exception as finally_error:
            logging.error(f"Error in finally block for {store.name}: {str(finally_error)}")
            # Last resort - try to rollback to prevent DB lock
            try:
                db.session.rollback()
            except:
                pass

def attempt_store_connection(store):
    """
    Attempt to establish connection to store if not already connected
    Returns True if connected successfully or already connected
    """
    # Skip connection attempt if already successful
    if store.sync_status == 'active' and store.last_sync:
        time_diff = datetime.utcnow() - store.last_sync
        # If last successful sync was less than 1 hour ago, assume still connected
        if time_diff.total_seconds() < 3600:
            return True
    
    # Only attempt auto-connection for Amazon and eBay stores with credentials
    if store.platform.lower() not in ['amazon', 'ebay'] or not store.api_key:
        return True  # Skip connection test for other platforms
    
    try:
        service = None
        if store.platform.lower() == 'amazon':
            # Determine region from credentials if available
            region = 'US'  # default
            try:
                creds = json.loads(store.api_key)
                region = creds.get('region', 'US')
            except:
                pass
            service = AmazonAPIService(marketplace_region=region)
        elif store.platform.lower() == 'ebay':
            service = eBayAPIService()
        
        if service:
            # Validate credentials first
            is_valid, validation_msg = service.validate_credentials_format(store.api_key)
            if not is_valid:
                logging.error(f"Invalid credentials for {store.name}: {validation_msg}")
                store.sync_status = 'error'
                db.session.commit()
                return False
            
            # Attempt authentication
            if service.authenticate_store(store):
                logging.info(f"Successfully connected to {store.platform} for store: {store.name}")
                store.sync_status = 'active'
                store.last_sync = datetime.utcnow()
                db.session.commit()
                return True
            else:
                logging.error(f"Authentication failed for {store.name}")
                store.sync_status = 'error'
                db.session.commit()
                return False
        
        return False
        
    except Exception as e:
        logging.error(f"Connection attempt failed for {store.name}: {str(e)}")
        store.sync_status = 'error'
        db.session.commit()
        return False

def sync_item_to_store(store, item):
    """Sync an item to external store using real API calls
    
    For grouped items, uses the group's shared quantity (MAX across all variants)
    to ensure all marketplaces receive the same stock level.
    
    Returns:
        tuple: (success: bool, message: str)
    """
    from models import ProductGroup, WarehouseStock
    
    # For grouped items, override quantity with group's shared quantity
    original_quantity = item.quantity
    if item.group_id:
        try:
            # Get the group and calculate shared quantity (MAX warehouse stock)
            group = db.session.get(ProductGroup, item.group_id)
            if group and group.items:
                warehouse_quantities = []
                for group_item in group.items:
                    warehouse_stock = WarehouseStock.query.filter_by(sku=group_item.sku).first()
                    if warehouse_stock:
                        warehouse_quantities.append(warehouse_stock.available_quantity)
                
                if warehouse_quantities:
                    shared_quantity = max(warehouse_quantities)
                    item.quantity = shared_quantity
                    logging.info(f"Grouped item {item.sku}: Using shared quantity {shared_quantity} (group: {group.name})")
        except Exception as e:
            logging.error(f"Error calculating group quantity for {item.sku}: {e}")
    
    try:
        if store.platform.lower() == 'amazon':
            # Use real Amazon API integration
            region = 'US'  # default
            try:
                creds = json.loads(store.api_key)
                region = creds.get('region', 'US')
            except:
                pass
            amazon_service = AmazonAPIService(marketplace_region=region)
            success, message = amazon_service.sync_inventory_to_amazon(store, item)
            logging.info(f"Amazon sync result for {item.sku}: {message}")
            return success, message
            
        elif store.platform.lower() == 'ebay':
            # Use real eBay API integration
            ebay_service = eBayAPIService()
            success, message = ebay_service.sync_inventory_to_ebay(store, item)
            logging.info(f"eBay sync result for {item.sku}: {message}")
            return success, message
            
        else:
            # Other platforms still simulated
            import random 
            success = random.random() > 0.15
            message = 'Successfully synced' if success else 'Sync failed'
            logging.info(f"{store.platform} sync for {item.sku}: {message}")
            return success, message
    finally:
        # Restore original quantity to avoid side effects
        item.quantity = original_quantity


def automatic_push_to_stores(item, operation="update"):
    """
    Automatically push inventory changes to connected stores with auto_push enabled.
    Runs in background and handles errors gracefully.
    
    Args:
        item: InventoryItem instance to sync
        operation: Type of operation ("create", "update", "delete")
    
    Returns:
        tuple: (overall_success, results_dict)
    """
    from app import db
    
    try:
        # CRITICAL FIX: Get warehouse stock for this SKU, then find stores with listings AND auto_push enabled
        # Don't push Amazon SKUs to eBay stores or vice versa!
        warehouse_stock = db.session.query(WarehouseStock).filter_by(sku=item.sku).first()
        if not warehouse_stock:
            logging.info(f"No warehouse stock found for item {item.sku} - skipping automatic push")
            return True, {"message": "No warehouse stock found"}
        
        eligible_stores = db.session.query(Store).join(
            MarketplaceListing, Store.id == MarketplaceListing.store_id
        ).filter(
            Store.is_active == True,
            Store.auto_push_enabled == True,
            Store.api_key.isnot(None),
            Store.api_key != '',
            MarketplaceListing.warehouse_stock_id == warehouse_stock.id
        ).distinct().all()
        
        if not eligible_stores:
            logging.info(f"No eligible stores for automatic push of item {item.sku} (no marketplace listings found)")
            return True, {"message": "No stores configured for automatic push"}
        
        results = []
        overall_success = True
        successful_pushes = 0
        
        logging.info(f"Starting automatic push for item {item.sku} to {len(eligible_stores)} stores")
        
        for store in eligible_stores:
            try:
                # Skip stores without proper authentication status
                if store.sync_status == 'error':
                    logging.warning(f"Skipping store {store.name} - authentication error")
                    results.append({
                        'store': store.name,
                        'platform': store.platform,
                        'success': False,
                        'message': f'Store has authentication error',
                        'skipped': True
                    })
                    continue
                
                from services.runtime_gate import is_runtime_action_allowed

                allowed, reason = is_runtime_action_allowed(
                    store=store,
                    action_type="push",
                    manual=False
                )

                if not allowed:
                    logging.warning(f"[RUNTIME_GATE_BLOCKED] Auto push blocked for item {item.sku} to store {store.id} ({store.name}): {reason}")
                    results.append({
                        'store': store.name,
                        'platform': store.platform,
                        'success': False,
                        'message': reason,
                        'skipped': True
                    })
                    continue

                # Attempt to push to this store
                start_time = datetime.utcnow()
                success, message = sync_item_to_store(store, item)
                end_time = datetime.utcnow()
                duration = (end_time - start_time).total_seconds()
                
                result = {
                    'store': store.name,
                    'platform': store.platform,
                    'success': success,
                    'message': message,
                    'duration_seconds': round(duration, 2),
                    'skipped': False
                }
                
                if success:
                    successful_pushes += 1
                    logging.info(f"✅ Automatic push to {store.name} ({store.platform}) succeeded for {item.sku}")
                else:
                    overall_success = False
                    logging.warning(f"❌ Automatic push to {store.name} ({store.platform}) failed for {item.sku}: {message}")
                
                results.append(result)
                
            except Exception as store_error:
                logging.error(f"Error in automatic push to store {store.name}: {str(store_error)}")
                results.append({
                    'store': store.name,
                    'platform': store.platform,
                    'success': False,
                    'message': f'Error: {str(store_error)}',
                    'skipped': False
                })
                overall_success = False
        
        summary = {
            'total_stores': len(eligible_stores),
            'successful_pushes': successful_pushes,
            'failed_pushes': len(eligible_stores) - successful_pushes,
            'operation': operation,
            'item_sku': item.sku,
            'item_name': item.name,
            'results': results
        }
        
        if successful_pushes > 0:
            logging.info(f"✅ Automatic push completed: {successful_pushes}/{len(eligible_stores)} stores updated for {item.sku}")
        else:
            logging.warning(f"⚠️ No successful automatic pushes for {item.sku}")
        
        return overall_success, summary
        
    except Exception as e:
        logging.error(f"Error in automatic_push_to_stores for item {item.sku if item else 'unknown'}: {str(e)}")
        return False, {
            'error': str(e),
            'item_sku': item.sku if item else 'unknown',
            'total_stores': 0,
            'successful_pushes': 0,
            'failed_pushes': 0
        }


def trigger_automatic_push(item, operation="update", run_async=True):
    """
    DEPRECATED: This function is deprecated as of Nov 9, 2025.
    Use the queue-based push system instead via routes.prepare_warehouse_push() and routes.enqueue_push_jobs().
    
    This function used background threads which caused issues:
    - Jobs could get rolled back with failed transactions
    - No priority handling for manual vs automatic pushes
    - Multiple divergent push mechanisms caused inconsistency
    
    New approach:
    1. Call prepare_warehouse_push(item, operation) BEFORE commit
    2. Commit the transaction
    3. Call enqueue_push_jobs(item.id, stores) AFTER commit
    
    Args:
        item: InventoryItem instance
        operation: Type of operation ("create", "update", "delete")
        run_async: Whether to run the push in a background thread (default: True)
    
    Returns:
        If run_async=True: Returns immediately, push happens in background
        If run_async=False: Returns (success, results) tuple
    """
    import warnings
    warnings.warn(
        "trigger_automatic_push is deprecated. Use queue-based push via prepare_warehouse_push() and enqueue_push_jobs() instead.",
        DeprecationWarning,
        stacklevel=2
    )
    logging.warning(f"⚠️ DEPRECATED: trigger_automatic_push called for {item.sku}. This function is deprecated - use queue-based push system instead.")
    
    if run_async:
        # Run in background thread to avoid blocking the main request
        import threading
        
        def background_push():
            try:
                with app.app_context():
                    automatic_push_to_stores(item, operation)
            except Exception as e:
                logging.error(f"Background automatic push failed for {item.sku}: {str(e)}")
        
        push_thread = threading.Thread(
            target=background_push,
            daemon=True,
            name=f"AutoPush-{item.sku}-{operation}"
        )
        push_thread.start()
        logging.info(f"Started background automatic push for {item.sku}")
        return True, {"message": "Automatic push started in background"}
    else:
        # Run synchronously
        return automatic_push_to_stores(item, operation)


def auto_detect_product_group(item_data, platform, store_id):
    """
    Auto-detect product group and variant attributes for an item during sync
    Returns (group_id, variant_attributes) tuple
    """
    try:
        product_name = item_data.get('name', '').strip()
        sku = item_data.get('sku', '').strip()
        
        if not product_name:
            return None, None
            
        # Extract potential variant attributes from product name and SKU
        variant_attributes = extract_variant_attributes(product_name, sku)
        
        # Generate base name for grouping (remove variant-specific terms)
        base_name = generate_base_product_name(product_name)
        
        if not base_name:
            return None, variant_attributes
        
        # Look for existing group with similar base name
        existing_group = find_existing_group(base_name, platform, store_id)
        
        if existing_group:
            # Add external reference if not exists
            create_external_reference(existing_group.id, platform, sku, item_data)
            logging.info(f"Assigned item {sku} to existing group: {existing_group.name}")
            return existing_group.id, variant_attributes
        
        # Create new group if significant variations detected
        if variant_attributes:
            new_group = create_product_group(base_name, platform, store_id, sku, item_data)
            if new_group:
                logging.info(f"Created new group '{new_group.name}' for item {sku}")
                return new_group.id, variant_attributes
        
        return None, variant_attributes
        
    except Exception as e:
        logging.error(f"Error in auto_detect_product_group for {item_data.get('sku', 'unknown')}: {str(e)}")
        return None, None


def extract_variant_attributes(product_name, sku):
    """Extract variant attributes like color, size, material from product name and SKU"""
    attributes = {}
    
    # Convert to lowercase for pattern matching
    name_lower = product_name.lower()
    sku_lower = sku.lower()
    
    # Color patterns
    color_patterns = [
        r'\b(black|white|red|blue|green|yellow|orange|purple|pink|brown|grey|gray|silver|gold)\b',
        r'\b(navy|beige|ivory|cream|maroon|olive|teal|cyan|magenta|lime|indigo|violet)\b',
        r'\b(dark|light|bright|pale)\s+(black|white|red|blue|green|yellow|orange|purple|pink|brown|grey|gray)\b'
    ]
    
    for pattern in color_patterns:
        match = re.search(pattern, name_lower)
        if match:
            attributes['color'] = match.group().title()
            break
    
    # Size patterns
    size_patterns = [
        r'\b(xs|s|m|l|xl|xxl|xxxl)\b',
        r'\b(x-small|small|medium|large|x-large|xx-large|xxx-large)\b',
        r'\b(\d+(?:\.\d+)?)\s*(cm|mm|inch|in|ft|meter|m)\b',
        r'\b(size\s*)?(\d+(?:\.\d+)?)\b',
        r'\b(one size|os|universal)\b'
    ]
    
    for pattern in size_patterns:
        match = re.search(pattern, name_lower)
        if match:
            if 'size' in pattern or any(unit in pattern for unit in ['cm', 'mm', 'inch', 'in', 'ft', 'meter']):
                attributes['size'] = match.group().upper()
            else:
                attributes['size'] = match.group().upper()
            break
    
    # Material patterns
    material_patterns = [
        r'\b(cotton|polyester|wool|silk|leather|plastic|metal|wood|glass|ceramic|rubber)\b',
        r'\b(stainless steel|aluminum|titanium|carbon fiber|bamboo|linen|denim|velvet|suede)\b'
    ]
    
    for pattern in material_patterns:
        match = re.search(pattern, name_lower)
        if match:
            attributes['material'] = match.group().title()
            break
    
    # Style patterns
    style_patterns = [
        r'\b(classic|modern|vintage|retro|contemporary|traditional|minimalist|rustic)\b',
        r'\b(casual|formal|sporty|elegant|chic|trendy|bohemian|industrial)\b'
    ]
    
    for pattern in style_patterns:
        match = re.search(pattern, name_lower)
        if match:
            attributes['style'] = match.group().title()
            break
    
    return attributes if attributes else None


def generate_base_product_name(product_name):
    """Generate base product name by removing variant-specific terms"""
    if not product_name:
        return None
    
    # Remove common variant indicators
    base_name = product_name
    
    # Remove size indicators
    base_name = re.sub(r'\b(xs|s|m|l|xl|xxl|xxxl)\b', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\b(x-small|small|medium|large|x-large|xx-large|xxx-large)\b', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\b(\d+(?:\.\d+)?)\s*(cm|mm|inch|in|ft|meter|m)\b', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\b(size\s*)?(\d+(?:\.\d+)?)\b', '', base_name, flags=re.IGNORECASE)
    
    # Remove color indicators
    base_name = re.sub(r'\b(black|white|red|blue|green|yellow|orange|purple|pink|brown|grey|gray|silver|gold)\b', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\b(navy|beige|ivory|cream|maroon|olive|teal|cyan|magenta|lime|indigo|violet)\b', '', base_name, flags=re.IGNORECASE)
    base_name = re.sub(r'\b(dark|light|bright|pale)\s+(black|white|red|blue|green|yellow|orange|purple|pink|brown|grey|gray)\b', '', base_name, flags=re.IGNORECASE)
    
    # Remove material indicators if they appear to be variants
    base_name = re.sub(r'\b(cotton|polyester|leather|plastic|metal)\b', '', base_name, flags=re.IGNORECASE)
    
    # Clean up extra spaces and punctuation
    base_name = re.sub(r'\s+', ' ', base_name).strip()
    base_name = re.sub(r'^[-\s,]+|[-\s,]+$', '', base_name)
    
    # Return None if the name is too short after cleaning
    if len(base_name) < 3:
        return None
    
    return base_name


def find_existing_group(base_name, platform, store_id):
    """Find existing product group that matches the base name"""
    if not base_name:
        return None
    
    try:
        # First try exact match on group name
        exact_match = db.session.query(ProductGroup).filter(
            ProductGroup.name.ilike(f"%{base_name}%")
        ).first()
        
        if exact_match:
            return exact_match
        
        # Try fuzzy matching with existing groups
        all_groups = db.session.query(ProductGroup).all()
        for group in all_groups:
            # Calculate similarity score between base_name and group name
            if calculate_name_similarity(base_name, group.name) > 0.8:
                return group
        
        return None
        
    except Exception as e:
        logging.error(f"Error finding existing group: {str(e)}")
        return None


def calculate_name_similarity(name1, name2):
    """Calculate similarity score between two product names (0.0 to 1.0)"""
    if not name1 or not name2:
        return 0.0
    
    # Simple word-based similarity
    words1 = set(name1.lower().split())
    words2 = set(name2.lower().split())
    
    if not words1 or not words2:
        return 0.0
    
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    
    return len(intersection) / len(union) if union else 0.0


def create_product_group(base_name, platform, store_id, sku, item_data):
    """Create a new product group for detected variations"""
    try:
        # Generate unique group key
        group_key = generate_group_key(base_name)
        
        # Create new product group
        new_group = ProductGroup()
        new_group.name = base_name
        new_group.description = f"Auto-generated group for {base_name} variations from {platform}"
        new_group.group_key = group_key
        
        db.session.add(new_group)
        db.session.flush()  # Get the ID without committing
        
        # Create external reference in a safe way
        try:
            create_external_reference(new_group.id, platform, sku, item_data)
        except Exception as ref_error:
            logging.warning(f"Failed to create external reference: {str(ref_error)}")
            # Continue without external reference
        
        return new_group
        
    except Exception as e:
        logging.error(f"Error creating product group: {str(e)}")
        # Don't rollback here since we're in a savepoint
        return None


def generate_group_key(base_name):
    """Generate a unique group key from base name"""
    if not base_name:
        return f"GROUP_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    
    # Convert to uppercase, remove special chars, limit length
    key = re.sub(r'[^A-Z0-9]', '_', base_name.upper())
    key = re.sub(r'_+', '_', key).strip('_')
    
    if len(key) > 20:
        key = key[:20]
    
    # Ensure uniqueness by checking database with proper error handling
    base_key = key
    counter = 1
    
    try:
        while db.session.query(ProductGroup).filter(ProductGroup.group_key == key).first():
            key = f"{base_key}_{counter}"
            counter += 1
            if counter > 100:  # Prevent infinite loop
                key = f"{base_key}_{datetime.utcnow().strftime('%H%M%S')}"
                break
    except Exception as e:
        logging.warning(f"Error checking group key uniqueness: {str(e)}")
        # Fallback to timestamp-based key
        key = f"{base_key}_{datetime.utcnow().strftime('%H%M%S')}"
    
    return key


def create_external_reference(group_id, platform, sku, item_data):
    """Create or update external reference linking group to platform-specific identifier using upsert"""
    try:
        from sqlalchemy import text
        
        current_time = datetime.utcnow()
        item_data_json = json.dumps(item_data) if item_data else None
        
        # Use PostgreSQL upsert (INSERT ... ON CONFLICT DO UPDATE) to handle duplicates atomically
        upsert_sql = text("""
            INSERT INTO group_external_refs (group_id, platform, external_id, external_type, external_data, created_at, updated_at)
            VALUES (:group_id, :platform, :external_id, :external_type, :external_data, :created_at, :updated_at)
            ON CONFLICT (platform, external_id) 
            DO UPDATE SET 
                group_id = EXCLUDED.group_id,
                external_type = EXCLUDED.external_type,
                external_data = EXCLUDED.external_data,
                updated_at = EXCLUDED.updated_at
            RETURNING id, 
                CASE 
                    WHEN xmax = 0 THEN 'inserted'
                    ELSE 'updated'
                END as operation
        """)
        
        # Use a nested savepoint to isolate this operation
        savepoint = db.session.begin_nested()
        try:
            result = db.session.execute(upsert_sql, {
                'group_id': group_id,
                'platform': platform,
                'external_id': sku,
                'external_type': 'listing',
                'external_data': item_data_json,
                'created_at': current_time,
                'updated_at': current_time
            }).fetchone()
            
            savepoint.commit()
            
            # Log the appropriate message based on operation
            operation = result[1] if result else 'unknown'
            if operation == 'inserted':
                logging.info(f"Created external reference for group {group_id}, platform {platform}, SKU {sku}")
            else:
                logging.info(f"Updated external reference for group {group_id}, platform {platform}, SKU {sku}")
                
        except Exception as savepoint_error:
            savepoint.rollback()
            # Re-raise the original error for logging
            raise savepoint_error
        
    except IntegrityError as ie:
        # This should be very rare now with upsert, but handle gracefully just in case
        logging.warning(f"IntegrityError creating external reference for group {group_id}, platform {platform}, SKU {sku}: {str(ie)}")
        # Don't re-raise to avoid breaking the sync process
    except Exception as e:
        logging.error(f"Error creating external reference for group {group_id}, platform {platform}, SKU {sku}: {str(e)}")
        # Don't re-raise to avoid breaking the sync process

def update_store_item_tracking(store, current_sync_skus):
    """
    Update the tracking table with SKUs seen in current sync for this store.
    Returns updated count.
    """
    try:
        from models import StoreItemSync
        from sqlalchemy import text
        
        current_time = datetime.utcnow()
        updated_count = 0
        
        # Upsert each SKU that was seen in current sync
        for sku in current_sync_skus:
            if not sku:  # Skip empty SKUs
                continue
                
            upsert_sql = text("""
                INSERT INTO store_item_syncs (store_id, sku, last_seen_at, created_at)
                VALUES (:store_id, :sku, :last_seen_at, :created_at)
                ON CONFLICT (store_id, sku) 
                DO UPDATE SET 
                    last_seen_at = EXCLUDED.last_seen_at
                RETURNING id, 
                    CASE 
                        WHEN xmax = 0 THEN 'inserted'
                        ELSE 'updated'
                    END as operation
            """)
            
            savepoint = db.session.begin_nested()
            try:
                db.session.execute(upsert_sql, {
                    'store_id': store.id,
                    'sku': sku,
                    'last_seen_at': current_time,
                    'created_at': current_time
                })
                savepoint.commit()
                updated_count += 1
            except Exception as savepoint_error:
                savepoint.rollback()
                logging.warning(f"Failed to update tracking for {store.name}:{sku}: {str(savepoint_error)}")
        
        db.session.commit()
        logging.debug(f"Updated tracking for {updated_count} SKUs for store {store.name}")
        return updated_count
        
    except Exception as e:
        logging.error(f"Error updating store item tracking for {store.name}: {str(e)}")
        db.session.rollback()
        return 0

# Old missing items detection removed - warehouse is now authoritative source

def push_quantity_to_ebay(store, listing, quantity):
    """Push quantity update to eBay listing"""
    try:
        ebay_service = eBayAPIService()
        
        # Initialize with store credentials
        credentials = json.loads(store.api_key)
        ebay_service.auth_token = credentials.get('user_token') or credentials.get('access_token')
        ebay_service.dev_id = credentials.get('dev_id')
        ebay_service.app_id = credentials.get('app_id')
        ebay_service.cert_id = credentials.get('cert_id')
        # BT38 PRODUCTION FIX
        # Never silently default production into sandbox
        ebay_service.use_sandbox = credentials.get('sandbox', False)
        
        # Update the listing quantity on eBay
        success = ebay_service.update_listing_quantity(listing.external_listing_id, quantity)
        
        if success:
            return True, None
        else:
            return False, "Failed to update eBay listing quantity"
            
    except Exception as e:
        error_msg = f"eBay push error: {str(e)}"
        logging.error(error_msg)
        return False, error_msg

def push_quantity_to_amazon(store, listing, quantity):
    """Push quantity update to Amazon listing"""
    try:
        # Extract region from credentials
        credentials = json.loads(store.api_key)
        region = credentials.get('region', 'US')
        
        amazon_service = AmazonAPIService(marketplace_region=region)
        
        # Initialize with store credentials
        amazon_service.refresh_token = credentials.get('refresh_token')
        amazon_service.client_id = credentials.get('client_id')
        amazon_service.client_secret = credentials.get('client_secret')
        
        # Update the listing quantity on Amazon
        success = amazon_service.update_listing_quantity(listing.external_sku or listing.external_listing_id, quantity)
        
        if success:
            return True, None
        else:
            return False, "Failed to update Amazon listing quantity"
            
    except Exception as e:
        error_msg = f"Amazon push error: {str(e)}"
        logging.error(error_msg)
        return False, error_msg
