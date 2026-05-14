"""
Phase 2 Auto-Sync: Auto-Push Service (DRY-RUN MODE)

This service handles automatic stock push to marketplaces when warehouse stock changes.
Currently in DRY-RUN mode only - logs what would be pushed without making API calls.

Key principles:
- FBA listings are NEVER pushed (read-only)
- Only FBM and eBay listings are eligible for auto-push
- Warehouse is the single source of truth
- Uses existing tables only (SyncJob, SyncLog, MarketplaceListing, WarehouseStock)
"""

import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from extensions import db
from models import (
    WarehouseStock, 
    MarketplaceListing, 
    Store, 
    SyncLog, 
    SyncJob,
    SystemConfig
)

logger = logging.getLogger(__name__)

AUTO_SYNC_CONFIG_KEY = 'auto_sync_enabled'


def is_auto_sync_enabled() -> bool:
    """Check if auto-sync is enabled in system settings (default: OFF)"""
    try:
        config = SystemConfig.query.filter_by(key=AUTO_SYNC_CONFIG_KEY).first()
        if config:
            return config.value.lower() in ('true', '1', 'yes', 'enabled')
        return False
    except Exception as e:
        logger.warning(f"Error checking auto_sync_enabled: {e}")
        return False


def set_auto_sync_enabled(enabled: bool) -> bool:
    """Set auto-sync enabled/disabled"""
    try:
        config = SystemConfig.query.filter_by(key=AUTO_SYNC_CONFIG_KEY).first()
        if config:
            config.value = 'true' if enabled else 'false'
        else:
            config = SystemConfig(key=AUTO_SYNC_CONFIG_KEY, value='true' if enabled else 'false')
            db.session.add(config)
        db.session.commit()
        logger.info(f"Auto-sync {'enabled' if enabled else 'disabled'}")
        return True
    except Exception as e:
        logger.error(f"Error setting auto_sync_enabled: {e}")
        db.session.rollback()
        return False


def is_listing_pushable(listing: MarketplaceListing, store: Store) -> Tuple[bool, str]:
    """
    Check if a listing is eligible for auto-push.
    
    Returns:
        (is_pushable, reason_if_not)
    """
    if not listing:
        return False, "Listing not found"
    
    if not store:
        return False, "Store not found"
    
    if not store.is_active:
        return False, f"Store '{store.name}' is inactive"
    
    if store.platform == 'AmazonFBA':
        return False, "FBA listings are read-only (never pushed)"
    
    fulfillment_channel = getattr(listing, 'amazon_fulfillment_channel', None)
    if fulfillment_channel == 'AFN':
        return False, "AFN/FBA listing - read-only"
    
    push_state = getattr(listing, 'push_state', None)
    if push_state and push_state != 'active':
        return False, f"Push state is '{push_state}' (not active)"
    
    if not listing.warehouse_stock_id:
        return False, "Not linked to warehouse"
    
    track_inventory = getattr(listing, 'track_inventory', True)
    if track_inventory is False:
        return False, "Inventory tracking disabled"
    
    return True, ""


def get_pushable_listings_for_warehouse(warehouse_stock_id: int) -> List[Tuple[MarketplaceListing, Store]]:
    """
    Get all marketplace listings linked to a warehouse stock that are eligible for auto-push.
    Excludes FBA listings.
    
    LINKAGE: Uses warehouse_stock_id ONLY (not master_product_group_id).
    Each listing must be directly linked to the warehouse stock.
    
    Returns:
        List of (listing, store) tuples
    """
    pushable = []
    
    try:
        listings = MarketplaceListing.query.filter_by(
            warehouse_stock_id=warehouse_stock_id
        ).all()
        
        for listing in listings:
            store = db.session.get(Store, listing.store_id) if listing.store_id else None
            is_ok, reason = is_listing_pushable(listing, store)
            
            if is_ok:
                pushable.append((listing, store))
            else:
                logger.debug(f"Listing {listing.id} not pushable: {reason}")
        
        return pushable
        
    except Exception as e:
        logger.error(f"Error getting pushable listings for warehouse {warehouse_stock_id}: {e}")
        return []


def queue_auto_push_for_sku(warehouse_stock_id: int) -> Dict:
    """
    Queue auto-push dry-run jobs for all eligible listings linked to a warehouse stock.
    
    This is the main hook called when warehouse stock changes.
    
    Args:
        warehouse_stock_id: ID of the WarehouseStock record that changed
        
    Returns:
        Dict with job creation results
    """
    from queue_manager import enqueue_sync_job, JOB_AUTO_PUSH_DRY_RUN, PRIORITY_MEDIUM
    
    result = {
        'warehouse_stock_id': warehouse_stock_id,
        'jobs_created': 0,
        'listings_skipped': 0,
        'skipped_reasons': [],
        'auto_sync_enabled': is_auto_sync_enabled()
    }
    
    if not is_auto_sync_enabled():
        logger.debug(f"Auto-sync disabled - not queuing jobs for warehouse {warehouse_stock_id}")
        result['skipped_reasons'].append("Auto-sync is disabled in settings")
        return result
    
    try:
        warehouse_stock = db.session.get(WarehouseStock, warehouse_stock_id)
        if not warehouse_stock:
            result['skipped_reasons'].append(f"WarehouseStock {warehouse_stock_id} not found")
            return result
        
        sku = warehouse_stock.sku
        available_qty = warehouse_stock.available_quantity or 0
        
        listings = MarketplaceListing.query.filter_by(
            warehouse_stock_id=warehouse_stock_id
        ).all()
        
        for listing in listings:
            store = db.session.get(Store, listing.store_id) if listing.store_id else None
            is_ok, reason = is_listing_pushable(listing, store)
            
            if not is_ok:
                result['listings_skipped'] += 1
                result['skipped_reasons'].append(f"SKU {sku} → {store.platform if store else 'unknown'}: {reason}")
                
                if 'FBA' in reason or 'AFN' in reason:
                    _log_fba_skip(warehouse_stock, listing, store, reason)
                continue
            
            try:
                job = enqueue_sync_job(
                    store_id=store.id,
                    job_type=JOB_AUTO_PUSH_DRY_RUN,
                    payload={
                        'warehouse_stock_id': warehouse_stock_id,
                        'sku': sku,
                        'available_quantity': available_qty,
                        'listing_id': listing.id,
                        'listing_sku': getattr(listing, 'external_sku', '') or getattr(listing, 'sku', ''),
                        'platform': store.platform,
                        'store_name': store.name
                    },
                    priority=PRIORITY_MEDIUM
                )
                result['jobs_created'] += 1
                logger.info(f"AUTO_PUSH_DRY_RUN job {job.id} queued: SKU={sku}, qty={available_qty}, store={store.name}")
                
            except Exception as job_err:
                logger.error(f"Failed to enqueue auto-push job for listing {listing.id}: {job_err}")
                result['skipped_reasons'].append(f"Job creation failed: {str(job_err)}")
        
        return result
        
    except Exception as e:
        logger.error(f"Error in queue_auto_push_for_sku({warehouse_stock_id}): {e}", exc_info=True)
        result['skipped_reasons'].append(f"Error: {str(e)}")
        return result


def _log_fba_skip(warehouse_stock: WarehouseStock, listing: MarketplaceListing, store: Store, reason: str):
    """Log when an FBA listing is skipped (for diagnostics)"""
    try:
        log = SyncLog(
            store_id=store.id if store else None,
            status='info',
            message=f"[Auto-Push] FBA SKIPPED: SKU={warehouse_stock.sku} - {reason}",
            items_synced=0
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.debug(f"Failed to log FBA skip: {e}")


def queue_real_push_for_warehouse(warehouse_stock_id: int) -> Dict:
    """
    Queue REAL push jobs for all eligible listings linked to a warehouse stock.
    This is for MANUAL pushes from Product Linking - actually pushes to marketplaces.
    
    CRITICAL INVARIANT: qty_to_push = warehouse_stock.available_quantity
    - Does NOT add sold counts
    - Does NOT calculate deltas
    - Does NOT read marketplace existing qty
    - Pure OVERWRITE operation
    
    CRITICAL FIX: Cancels any stale pending push jobs for the same warehouse_stock_id
    before enqueuing fresh jobs. This prevents old jobs with stale quantities (e.g., 22)
    from executing after the fresh push (e.g., 14) and overwriting it.
    
    Args:
        warehouse_stock_id: ID of the WarehouseStock record
        
    Returns:
        Dict with job creation results including pushed_listings detail
    """
    from queue_manager import enqueue_sync_job, JOB_PUSH_ITEM, PRIORITY_HIGH, cancel_stale_push_jobs_for_warehouse
    
    result = {
        'warehouse_stock_id': warehouse_stock_id,
        'jobs_created': 0,
        'listings_skipped': 0,
        'skipped_reasons': [],
        'pushed_listings': [],
        'stale_jobs_cancelled': 0,
        'real_push': True,
        'auto_sync_enabled': is_auto_sync_enabled(),
        'manual_push_note': 'Manual push executed regardless of auto-sync setting'
    }
    
    try:
        stale_cancelled = cancel_stale_push_jobs_for_warehouse(warehouse_stock_id)
        result['stale_jobs_cancelled'] = stale_cancelled
        if stale_cancelled > 0:
            logger.warning(f"[PRODUCT_LINKING_PUSH] Cancelled {stale_cancelled} stale pending jobs before fresh push")
        
        warehouse_stock = db.session.get(WarehouseStock, warehouse_stock_id)
        if not warehouse_stock:
            result['skipped_reasons'].append(f"WarehouseStock {warehouse_stock_id} not found")
            return result
        
        sku = warehouse_stock.sku
        qty_to_push = warehouse_stock.available_quantity or 0
        
        logger.info(f"[PRODUCT_LINKING_PUSH] ========================================")
        logger.info(f"[PRODUCT_LINKING_PUSH] Warehouse SKU: {sku}")
        logger.info(f"[PRODUCT_LINKING_PUSH] qty_to_push = {qty_to_push} (warehouse.available_quantity)")
        logger.info(f"[PRODUCT_LINKING_PUSH] This is OVERWRITE only - no sold added, no delta calc")
        
        # Find listings linked via warehouse_stock_id (direct linkage ONLY)
        listings = MarketplaceListing.query.filter_by(
            warehouse_stock_id=warehouse_stock_id
        ).all()
        
        logger.info(f"[PRODUCT_LINKING_PUSH] Found {len(listings)} linked listings to push (via warehouse_stock_id)")
        
        for listing in listings:
            store = db.session.get(Store, listing.store_id) if listing.store_id else None
            is_ok, reason = is_listing_pushable(listing, store)
            
            listing_sku = getattr(listing, 'external_sku', '') or sku
            listing_id = listing.external_listing_id or listing.id
            platform = store.platform if store else 'unknown'
            
            if not is_ok:
                result['listings_skipped'] += 1
                result['skipped_reasons'].append(f"SKU {sku} → {platform}: {reason}")
                logger.info(f"[PRODUCT_LINKING_PUSH] SKIPPED: platform={platform} listing_id={listing_id} reason={reason}")
                
                if 'FBA' in reason or 'AFN' in reason:
                    _log_fba_skip(warehouse_stock, listing, store, reason)
                continue
            
            try:
                from services.runtime_gate import is_runtime_action_allowed

                allowed, reason = is_runtime_action_allowed(
                    store=store,
                    action_type="push",
                    manual=True
                )

                if not allowed:
                    result['listings_skipped'] += 1
                    result['skipped_reasons'].append(f"SKU {sku} → {platform}: {reason}")
                    logger.warning(f"[RUNTIME_GATE_BLOCKED] Queue creation blocked for SKU {sku} store={store.name}: {reason}")
                    continue

                job = enqueue_sync_job(
                    store_id=store.id,
                    job_type=JOB_PUSH_ITEM,
                    payload={
                        'warehouse_stock_id': warehouse_stock_id,
                        'sku': sku,
                        'quantity': qty_to_push,
                        'source': 'product_linking_adjust_and_push',
                        'enqueued_at': datetime.utcnow().isoformat()
                    },
                    priority=PRIORITY_HIGH
                )
                result['jobs_created'] += 1
                result['pushed_listings'].append({
                    'platform': platform,
                    'store_name': store.name,
                    'listing_id': listing_id,
                    'listing_sku': listing_sku,
                    'qty_pushed': qty_to_push,
                    'job_id': job.id
                })
                logger.info(f"[PRODUCT_LINKING_PUSH] QUEUED: platform={platform} listing_id={listing_id} listing_sku={listing_sku} qty={qty_to_push} job_id={job.id}")
                
            except Exception as job_err:
                logger.error(f"Failed to enqueue real push job for listing {listing.id}: {job_err}")
                result['skipped_reasons'].append(f"Job creation failed: {str(job_err)}")
        
        logger.info(f"[PRODUCT_LINKING_PUSH] ========================================")
        logger.info(f"[PRODUCT_LINKING_PUSH] SUMMARY: {result['jobs_created']} pushes queued, {result['listings_skipped']} skipped")
        for pl in result['pushed_listings']:
            logger.info(f"[PRODUCT_LINKING_PUSH] → {pl['platform']} | {pl['listing_sku']} | qty={pl['qty_pushed']}")
        logger.info(f"[PRODUCT_LINKING_PUSH] ========================================")
        
        return result
        
    except Exception as e:
        logger.error(f"Error in queue_real_push_for_warehouse({warehouse_stock_id}): {e}", exc_info=True)
        result['skipped_reasons'].append(f"Error: {str(e)}")
        return result


def execute_dry_run_push(job: SyncJob) -> Dict:
    """
    Execute a dry-run auto-push job.
    This does NOT call any marketplace APIs - it only logs what would happen.
    
    Args:
        job: SyncJob with type JOB_AUTO_PUSH_DRY_RUN
        
    Returns:
        Dict with execution results
    """
    payload = job.payload or {}
    warehouse_stock_id = payload.get('warehouse_stock_id')
    sku = payload.get('sku', 'UNKNOWN')
    available_qty = payload.get('available_quantity', 0)
    listing_id = payload.get('listing_id')
    listing_sku = payload.get('listing_sku', sku)
    platform = payload.get('platform', 'unknown')
    store_name = payload.get('store_name', 'unknown')
    
    result = {
        'success': True,
        'dry_run': True,
        'sku': sku,
        'quantity': available_qty,
        'platform': platform,
        'store_name': store_name
    }
    
    try:
        warehouse_stock = db.session.get(WarehouseStock, warehouse_stock_id) if warehouse_stock_id else None
        current_qty = warehouse_stock.available_quantity if warehouse_stock else available_qty
        
        dry_run_message = (
            f"[AUTO_PUSH_DRY_RUN] Would push stock to marketplace:\n"
            f"  SKU: {listing_sku}\n"
            f"  Warehouse SKU: {sku}\n"
            f"  Quantity: {current_qty}\n"
            f"  Platform: {platform}\n"
            f"  Store: {store_name}\n"
            f"  Listing ID: {listing_id}\n"
            f"  *** NO API CALL MADE - DRY RUN ONLY ***"
        )
        
        logger.info(dry_run_message)
        
        log = SyncLog(
            store_id=job.store_id,
            status='completed',
            message=(
                f"[AUTO_PUSH_DRY_RUN] DRY RUN ONLY: would set quantity={current_qty} "
                f"for SKU={listing_sku} on {platform} store={store_name}"
            ),
            items_synced=0
        )
        db.session.add(log)
        db.session.commit()
        
        result['message'] = dry_run_message
        return result
        
    except Exception as e:
        logger.error(f"Error executing dry-run push for job {job.id}: {e}", exc_info=True)
        
        try:
            log = SyncLog(
                store_id=job.store_id,
                status='error',
                message=f"[AUTO_PUSH_DRY_RUN] Error: {str(e)}",
                items_synced=0
            )
            db.session.add(log)
            db.session.commit()
        except:
            pass
        
        result['success'] = False
        result['error'] = str(e)
        return result
