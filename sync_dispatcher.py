"""
Sync Dispatcher - Worker thread that processes sync jobs from the queue
Enables concurrent background sync and manual pushes without deadlock
"""

import logging
import threading
import time
from datetime import datetime
from typing import Dict
from flask import current_app
from extensions import db
from models import Store, InventoryItem, WarehouseStock, SyncJob
from queue_manager import (
    get_next_pending_job,
    mark_job_running,
    mark_job_complete,
    mark_job_failed,
    reset_stuck_jobs,
    JOB_FULL_SYNC,
    JOB_PUSH_ITEM,
    JOB_IMPORT_LISTINGS,
    JOB_ORDER_IMPORT,
    JOB_AUTO_PUSH_DRY_RUN
)

# Store app reference for worker threads
_app_instance = None

def set_app_instance(app):
    """Set the Flask app instance for use in worker threads"""
    global _app_instance
    _app_instance = app

class SyncDispatcher:
    """Dispatcher that processes sync jobs for all stores"""
    
    def __init__(self):
        self.running = False
        self.worker_threads = {}  # store_id -> thread
        self.shutdown_event = threading.Event()
        
    def start(self):
        """Start the dispatcher"""
        global _app_instance
        
        # Defensive check: ensure app instance is set before starting
        if _app_instance is None:
            raise RuntimeError(
                "Flask app instance not set! "
                "Call set_app_instance(app) before start_dispatcher()"
            )
        
        if self.running:
            logging.warning("Dispatcher already running")
            return
        
        self.running = True
        self.shutdown_event.clear()
        
        # Watchdog: Reset stuck jobs from previous crashes/restarts
        # Note: Uses conservative 15-minute timeout to avoid failing legitimate long-running jobs
        with _app_instance.app_context():
            stores_with_stuck_jobs = reset_stuck_jobs(timeout_minutes=15)
            if stores_with_stuck_jobs:  # dict is truthy if non-empty
                total_reset = sum(stores_with_stuck_jobs.values())
                logging.warning(f"🔧 Startup cleanup: Reset {total_reset} stuck jobs from previous session")
        
        # Start a single dispatcher thread that monitors all stores
        dispatcher_thread = threading.Thread(target=self._dispatcher_loop, daemon=True, name="SyncDispatcher")
        dispatcher_thread.start()
        
        logging.info("Sync dispatcher started")
    
    def stop(self):
        """Stop the dispatcher gracefully"""
        if not self.running:
            return
        
        logging.info("Stopping sync dispatcher...")
        self.running = False
        self.shutdown_event.set()
        
        # Wait for worker threads to finish
        for store_id, thread in self.worker_threads.items():
            if thread.is_alive():
                logging.info(f"Waiting for worker thread for store {store_id} to finish...")
                thread.join(timeout=10)
        
        logging.info("Sync dispatcher stopped")
    
    def _dispatcher_loop(self):
        """Main dispatcher loop - monitors all stores and starts workers as needed"""
        logging.info("Dispatcher loop started")
        
        watchdog_counter = 0  # Run watchdog every 60 seconds
        
        while self.running:
            try:
                # Get all active stores
                with _app_instance.app_context():
                    # Periodic watchdog: Reset stuck jobs every 30 iterations (60 seconds)
                    # Uses 30-minute timeout to handle both hung workers and avoid false positives
                    # This timeout is conservative enough to allow legitimate long-running jobs
                    watchdog_counter += 1
                    if watchdog_counter >= 30:
                        # Watchdog for SyncJob (work queue)
                        stores_with_stuck_jobs = reset_stuck_jobs(timeout_minutes=30)
                        if stores_with_stuck_jobs:
                            total_reset = sum(stores_with_stuck_jobs.values())
                            logging.warning(f"🔧 Watchdog: Reset {total_reset} stuck jobs across {len(stores_with_stuck_jobs)} stores")
                            
                            # Clean up worker threads for stores that had stuck jobs
                            # This allows the dispatcher to spawn new workers for those stores
                            # even if the old worker thread is still alive but hung
                            for store_id in stores_with_stuck_jobs.keys():
                                if store_id in self.worker_threads:
                                    del self.worker_threads[store_id]
                                    logging.warning(f"🔧 Removed worker thread for store {store_id} to allow queue recovery")
                        
                        # Watchdog for SyncLog (legacy logs stuck in 'started' status)
                        from queue_manager import reset_stuck_sync_logs
                        stuck_logs_count = reset_stuck_sync_logs(timeout_minutes=30)
                        if stuck_logs_count > 0:
                            logging.warning(f"🔧 SyncLog Watchdog: Marked {stuck_logs_count} stuck sync logs as failed")
                        
                        watchdog_counter = 0
                    
                    stores = Store.query.filter_by(is_active=True).all()
                    
                    for store in stores:
                        # Check if worker thread for this store is already running
                        if store.id in self.worker_threads:
                            if self.worker_threads[store.id].is_alive():
                                continue  # Worker already processing this store
                            else:
                                # Thread finished, remove it
                                del self.worker_threads[store.id]
                        
                        # Check if there are pending jobs for this store
                        pending_job = get_next_pending_job(store.id)
                        if pending_job:
                            # Start a new worker thread for this store
                            worker_thread = threading.Thread(
                                target=self._process_store_queue,
                                args=(store.id,),
                                daemon=True,
                                name=f"StoreWorker-{store.id}"
                            )
                            worker_thread.start()
                            self.worker_threads[store.id] = worker_thread
                            logging.info(f"Started worker thread for store {store.id} ({store.name})")
                
                # Sleep for 2 seconds before next check
                if self.shutdown_event.wait(timeout=2.0):
                    break
                    
            except Exception as e:
                logging.error(f"Error in dispatcher loop: {str(e)}", exc_info=True)
                time.sleep(5)  # Back off on error
        
        logging.info("Dispatcher loop ended")
    
    def _process_store_queue(self, store_id: int):
        """Process all pending jobs for a specific store sequentially"""
        logging.info(f"Worker started for store {store_id}")
        
        try:
            with _app_instance.app_context():
                store = Store.query.get(store_id)
                if not store:
                    logging.error(f"Store {store_id} not found")
                    return
                
                # Update store status for observability
                store.sync_status = 'syncing'
                db.session.commit()
                
                # Process jobs one by one
                while self.running:
                    job = get_next_pending_job(store_id)
                    if not job:
                        break  # No more jobs
                    
                    # Mark job as running
                    mark_job_running(job.id)
                    
                    try:
                        # Execute the job based on type
                        self._execute_job(job, store)
                        mark_job_complete(job.id)
                        logging.info(f"Job {job.id} ({job.job_type}) completed successfully")
                        
                    except Exception as job_error:
                        error_msg = str(job_error)
                        logging.error(f"Job {job.id} failed: {error_msg}", exc_info=True)
                        
                        # Determine if we should retry based on error type
                        retry_minutes = None
                        if "rate limit" in error_msg.lower() or "quota" in error_msg.lower():
                            retry_minutes = 60  # Retry rate limit errors after 1 hour
                        elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                            retry_minutes = 5  # Retry connection errors after 5 minutes
                        
                        mark_job_failed(job.id, error_msg, retry_minutes)
                
                # Truthful dispatcher result:
                # Only mark store active if no jobs remain failed/running for this store.
                failed_or_running = SyncJob.query.filter(
                    SyncJob.store_id == store_id,
                    SyncJob.status.in_(["failed", "running"])
                ).first()

                if failed_or_running:
                    store.sync_status = 'error'
                else:
                    store.sync_status = 'active'
                    store.last_sync = datetime.utcnow()

                db.session.commit()
                
        except Exception as e:
            logging.error(f"Error in store worker for store {store_id}: {str(e)}", exc_info=True)
            try:
                with _app_instance.app_context():
                    store = Store.query.get(store_id)
                    if store:
                        store.sync_status = 'error'
                        db.session.commit()
                        logging.info(f"Marked store {store_id} as 'error' after worker failure")
            except Exception as recovery_error:
                logging.error(f"Failed to update store status after error: {recovery_error}")
        finally:
            logging.info(f"Worker finished for store {store_id}")
    
    def _execute_job(self, job, store):
        """Execute a specific job"""
        job_type = job.job_type
        payload = job.payload or {}
        
        fresh_job = db.session.query(SyncJob).filter_by(id=job.id).first()
        if fresh_job and fresh_job.status == 'cancelled':
            logging.warning(f"[STALE_JOB_SKIP] Job {job.id} was cancelled before execution (qty={payload.get('quantity', 'unknown')})")
            return
        
        # Single execution control: store_mode is the only push gate.
        # Instance/flag/environment push blockers were removed so that
        # push and full-sync behavior is controlled in one place only.

        # [STAGE9A] SAFE MODE GUARD: Block pushes for stores not yet Go Live
        if getattr(store, 'store_mode', 'live') != 'live' and job_type in [JOB_PUSH_ITEM, JOB_FULL_SYNC]:
            block_reason = (
                f"store_mode={store.store_mode}, platform={store.platform}, "
                f"job_type={job_type}, store_id={store.id}, "
                f"sku={payload.get('sku', 'N/A')}"
            )
            logging.warning(f"[SAFE_MODE_BLOCKED] Job {job.id} blocked - {block_reason}")
            job.status = 'skipped'
            job.error_message = f'[SAFE_MODE_BLOCKED] {block_reason}'
            job.completed_at = datetime.utcnow()
            db.session.commit()
            return
        
        # Block push operations for Amazon FBA stores (read-only inventory)
        if store.platform == 'AmazonFBA' and job_type == JOB_PUSH_ITEM:
            logging.warning(f"Skipping push job {job.id} - Amazon FBA inventory is read-only (Amazon-controlled)")
            return  # Silent skip - FBA doesn't accept pushes
        
        if job_type == JOB_FULL_SYNC:
            # Full store sync (import + push)
            # For FBA stores, this will only import, not push
            self._execute_full_sync(store)
            
        elif job_type == JOB_PUSH_ITEM:
            # Push a specific item - can use item_id OR warehouse_stock_id + sku
            item_id = payload.get('item_id')
            warehouse_stock_id = payload.get('warehouse_stock_id')
            sku = payload.get('sku')
            
            if item_id:
                self._execute_push_item(store, item_id, job_payload=payload)
            elif warehouse_stock_id and sku:
                # Warehouse-only item (no linked InventoryItem)
                self._execute_push_warehouse_item(store, warehouse_stock_id, sku, job_payload=payload)
            else:
                raise ValueError("JOB_PUSH_ITEM requires 'item_id' OR ('warehouse_stock_id' and 'sku') in payload")
            
        elif job_type == JOB_IMPORT_LISTINGS:
            # Import listings from marketplace
            self._execute_import_listings(store)
        
        elif job_type == JOB_ORDER_IMPORT:
            # Phase 1 Auto-Sync: Import orders from marketplace
            hours_back = payload.get('hours_back', 24)
            self._execute_order_import(store, hours_back)
        
        elif job_type == JOB_AUTO_PUSH_DRY_RUN:
            # Phase 2 Auto-Sync: Dry-run push (no real API calls)
            self._execute_auto_push_dry_run(job, store)
            
        else:
            raise ValueError(f"Unknown job type: {job_type}")
    
    def _execute_full_sync(self, store):
        """Execute a full sync for a store"""
        from sync_service import sync_store
        logging.info(f"Executing full sync for store {store.id} ({store.name})")
        sync_store(store)
    
    def _execute_push_item(self, store, item_id, job_payload=None):
        """Execute push for a specific item (InventoryItem-based)
        
        CRITICAL GUARDRAILS:
        - Validates job payload quantity matches current warehouse quantity
        - Blocks stale pushes that would overwrite correct data
        - [GROUP_BLOCK] Blocks individual pushes when is_group_controlled=true
        """
        from sync_service import sync_item_to_store
        from models import Warehouse
        
        item = InventoryItem.query.get(item_id)
        if not item:
            raise ValueError(f"Item {item_id} not found")
        
        job_payload = job_payload or {}
        job_source = job_payload.get('source', 'unknown')
        
        # [GROUP_BLOCK] Check if this SKU is group-controlled
        # Group-controlled SKUs can ONLY be pushed via group-push, not individual pushes
        group_push_sources = ['group_push', 'group-push', 'group_view', 'GroupView']
        if job_source not in group_push_sources:
            default_warehouse = Warehouse.get_default()
            warehouse_stock = WarehouseStock.query.filter_by(
                sku=item.sku,
                warehouse_id=default_warehouse.id
            ).first()
            
            if warehouse_stock and warehouse_stock.is_group_controlled:
                logging.warning(
                    f"[GROUP_BLOCK] Individual push BLOCKED for SKU {item.sku} "
                    f"(warehouse_stock_id={warehouse_stock.id}) - "
                    f"is_group_controlled=true, source='{job_source}'. "
                    f"Only group-push is allowed for this SKU."
                )
                # Silent return - don't raise exception, just skip the push
                return
        
        job_qty = job_payload.get('quantity')
        current_qty = item.quantity or 0
        
        log_data = {
            'sku': item.sku,
            'item_id': item_id,
            'store': store.name,
            'platform': store.platform,
            'current_qty': current_qty,
            'job_payload_qty': job_qty,
        }
        
        if job_qty is not None and job_qty != current_qty:
            log_data['decision'] = 'BLOCKED'
            log_data['reason'] = f'Job qty ({job_qty}) != current item qty ({current_qty})'
            logging.warning(f"[PUSH_AUTHORITY_BLOCKED] {log_data}")
            raise ValueError(f"Push blocked: stale job payload qty={job_qty} vs current qty={current_qty}")
        
        log_data['decision'] = 'ALLOWED'
        log_data['reason'] = 'Job qty matches current item qty'
        logging.info(f"[PUSH_AUTHORITY_ALLOWED] {log_data}")

        from services.runtime_gate import is_runtime_action_allowed

        allowed, reason = is_runtime_action_allowed(
            store=store,
            action_type="push",
            manual=False
        )

        if not allowed:
            logging.warning(f"[RUNTIME_GATE_BLOCKED] Push blocked for item {item.sku} to store {store.id} ({store.name}): {reason}")
            raise ValueError(reason)

        logging.info(f"Executing push for item {item.sku} to store {store.id} ({store.name})")
        success, message = sync_item_to_store(store, item)

        if not success:
            raise Exception(message)
    
    def _execute_push_warehouse_item(self, store, warehouse_stock_id, sku, job_payload=None):
        """Execute push for a warehouse-only item (no linked InventoryItem)
        
        CRITICAL GUARDRAILS:
        - Always uses CURRENT warehouse_stock.available_quantity (never stale job payload)
        - Blocks pushes if job payload quantity differs from current warehouse quantity
          (indicates stale job that would overwrite correct data)
        - [GROUP_BLOCK] Blocks individual pushes when is_group_controlled=true
        """
        from sync_service import sync_warehouse_stock_to_store
        
        warehouse_stock = WarehouseStock.query.get(warehouse_stock_id)
        if not warehouse_stock:
            raise ValueError(f"WarehouseStock {warehouse_stock_id} not found")
        
        job_payload = job_payload or {}
        job_source = job_payload.get('source', 'unknown')
        
        # [GROUP_BLOCK] Check if this warehouse stock is group-controlled
        # Group-controlled SKUs can ONLY be pushed via group-push, not individual pushes
        group_push_sources = ['group_push', 'group-push', 'group_view', 'GroupView']
        if job_source not in group_push_sources:
            if warehouse_stock.is_group_controlled:
                logging.warning(
                    f"[GROUP_BLOCK] Individual push BLOCKED for SKU {sku} "
                    f"(warehouse_stock_id={warehouse_stock_id}) - "
                    f"is_group_controlled=true, source='{job_source}'. "
                    f"Only group-push is allowed for this SKU."
                )
                # Silent return - don't raise exception, just skip the push
                return
        
        current_warehouse_qty = warehouse_stock.available_quantity or 0
        job_qty = job_payload.get('quantity')
        job_enqueued_at = job_payload.get('enqueued_at', 'unknown')
        
        log_data = {
            'sku': sku,
            'warehouse_stock_id': warehouse_stock_id,
            'store': store.name,
            'platform': store.platform,
            'current_warehouse_qty': current_warehouse_qty,
            'job_payload_qty': job_qty,
            'job_source': job_source,
            'job_enqueued_at': job_enqueued_at,
            'last_adjustment_at': str(warehouse_stock.last_adjustment_at) if warehouse_stock.last_adjustment_at else None,
            'last_adjustment_by': warehouse_stock.last_adjustment_by
        }
        
        if job_qty is not None and job_qty != current_warehouse_qty:
            from datetime import timedelta
            lockout_minutes = 10
            is_in_lockout = False
            
            if warehouse_stock.last_adjustment_at and warehouse_stock.last_adjustment_by == 'product_linking':
                time_since_adjust = datetime.utcnow() - warehouse_stock.last_adjustment_at
                if time_since_adjust < timedelta(minutes=lockout_minutes):
                    is_in_lockout = True
            
            log_data['decision'] = 'BLOCKED'
            log_data['reason'] = f'Job qty ({job_qty}) != current warehouse qty ({current_warehouse_qty})'
            log_data['lockout_active'] = is_in_lockout
            
            logging.warning(f"[PUSH_AUTHORITY_BLOCKED] {log_data}")
            raise ValueError(f"Push blocked: stale job payload qty={job_qty} vs current warehouse qty={current_warehouse_qty}")
        
        log_data['decision'] = 'ALLOWED'
        log_data['reason'] = 'Job qty matches current warehouse qty'
        logging.info(f"[PUSH_AUTHORITY_ALLOWED] {log_data}")
        
        logging.info(f"Executing push for warehouse-only SKU {sku} to store {store.id} ({store.name}) qty={current_warehouse_qty}")
        success, message = sync_warehouse_stock_to_store(store, warehouse_stock)
        
        if not success:
            raise Exception(message)
    
    def _execute_import_listings(self, store):
        """Execute import listings for a store"""
        from sync_service import import_listings_from_store
        logging.info(f"Executing import listings for store {store.id} ({store.name})")
        import_listings_from_store(store)
    
    def _execute_order_import(self, store, hours_back: int = 24):
        """Execute order import for a store (Phase 1 Auto-Sync)"""
        from marketplace_order_processor import OrderImportService
        
        logging.info(f"Executing order import for store {store.id} ({store.name}), hours_back={hours_back}")
        
        result = OrderImportService.import_orders_for_store(store, hours_back)
        
        if not result.get('success'):
            raise Exception(result.get('error', 'Unknown error during order import'))
        
        logging.info(f"Order import complete for {store.name}: imported={result.get('imported', 0)}, skipped={result.get('skipped', 0)}")
    
    def _execute_auto_push_dry_run(self, job, store):
        """Execute auto-push dry-run (Phase 2 Auto-Sync) - NO real API calls"""
        from auto_push_service import execute_dry_run_push
        
        payload = job.payload or {}
        sku = payload.get('sku', 'UNKNOWN')
        
        logging.info(f"Executing AUTO_PUSH_DRY_RUN for SKU={sku} to store {store.id} ({store.name})")
        
        result = execute_dry_run_push(job)
        
        if not result.get('success'):
            raise Exception(result.get('error', 'Unknown error during dry-run push'))
        
        logging.info(f"DRY-RUN push complete: SKU={sku}, qty={result.get('quantity')}, platform={result.get('platform')}")


# ==============================
# Phase 1 Auto-Sync: Scheduled Order Import
# ==============================

class OrderImportScheduler:
    """
    Scheduler for automatic order import every 5 minutes.
    Runs as a background thread alongside the main dispatcher.
    """
    
    def __init__(self):
        self.running = False
        self.scheduler_thread = None
        self.shutdown_event = threading.Event()
        # AMAZON-ORDER-SYNC-THROTTLE-FIX-008: Increase interval to 5 minutes
        # to prevent Amazon Orders API rate limiting (6 req/min quota)
        self.import_interval_seconds = 300  # 5 minutes (THROTTLE-FIX-008)
    
    def start(self):
        """Start the order import scheduler"""
        global _app_instance
        
        if _app_instance is None:
            logging.warning("Cannot start OrderImportScheduler: Flask app instance not set")
            return
        
        if self.running:
            logging.warning("OrderImportScheduler already running")
            return
        
        self.running = True
        self.shutdown_event.clear()
        
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="OrderImportScheduler"
        )
        self.scheduler_thread.start()
        logging.info(f"Order import scheduler started (interval: {self.import_interval_seconds}s)")
    
    def stop(self):
        """Stop the scheduler gracefully"""
        if not self.running:
            return
        
        logging.info("Stopping order import scheduler...")
        self.running = False
        self.shutdown_event.set()
        
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=10)
        
        logging.info("Order import scheduler stopped")
    
    def _scheduler_loop(self):
        """Main scheduler loop - runs order import at regular intervals"""
        logging.info("Order import scheduler loop started")
        
        # Wait a bit before first run to let the app stabilize
        if self.shutdown_event.wait(timeout=30):
            return
        
        while self.running:
            try:
                self._run_scheduled_import()
            except Exception as e:
                logging.error(f"Error in order import scheduler: {str(e)}", exc_info=True)
            
            # Wait for next interval
            if self.shutdown_event.wait(timeout=self.import_interval_seconds):
                break
        
        logging.info("Order import scheduler loop ended")
    
    def _run_scheduled_import(self):
        """Execute scheduled order import for all stores"""
        global _app_instance
        
        if not _app_instance:
            logging.warning("No Flask app instance for scheduled order import")
            return
        
        with _app_instance.app_context():
            from marketplace_order_processor import OrderImportService
            
            logging.warning(f"[ORDER_IMPORT][TICK] {datetime.utcnow().isoformat()}Z interval={self.import_interval_seconds}s")
            logging.info("Starting scheduled order import...")
            result = OrderImportService.run_scheduled_import(hours_back=24)
            
            logging.info(
                f"Scheduled order import complete: "
                f"stores={result.get('stores_processed', 0)}, "
                f"imported={result.get('total_imported', 0)}, "
                f"skipped={result.get('total_skipped', 0)}, "
                f"failed={result.get('total_failed', 0)}"
            )
            
            if result.get('errors'):
                for error in result['errors'][:5]:  # Log first 5 errors
                    logging.warning(f"Order import error: {error}")


# Global scheduler instance
_order_import_scheduler = None

def get_order_import_scheduler() -> OrderImportScheduler:
    """Get the global order import scheduler instance"""
    global _order_import_scheduler
    if _order_import_scheduler is None:
        _order_import_scheduler = OrderImportScheduler()
    return _order_import_scheduler

def start_order_import_scheduler():
    """Start the global order import scheduler"""
    scheduler = get_order_import_scheduler()
    scheduler.start()

def stop_order_import_scheduler():
    """Stop the global order import scheduler"""
    scheduler = get_order_import_scheduler()
    scheduler.stop()


# Global dispatcher instance
_dispatcher = None

def get_dispatcher() -> SyncDispatcher:
    """Get the global dispatcher instance"""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = SyncDispatcher()
    return _dispatcher

def start_dispatcher():
    """Start the global dispatcher"""
    dispatcher = get_dispatcher()
    dispatcher.start()

def stop_dispatcher():
    """Stop the global dispatcher"""
    dispatcher = get_dispatcher()
    dispatcher.stop()
