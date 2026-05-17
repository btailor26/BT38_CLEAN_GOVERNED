from extensions import db
from datetime import datetime
from sqlalchemy import func, JSON, Index
from typing import TYPE_CHECKING, List, Optional
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from store_credentials import eBayCredentials, AmazonCredentials

if TYPE_CHECKING:
    from sqlalchemy.orm import Mapped

class SystemConfig(db.Model):
    """Store system configuration including Amazon credentials"""
    __tablename__ = 'system_config'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<SystemConfig {self.key}>'


class SystemLog(db.Model):
    """Section X.9: Store system logs including route failures"""
    __tablename__ = 'system_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    log_type = db.Column(db.String(50), nullable=False, index=True)
    message = db.Column(db.String(500), nullable=False)
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        return f'<SystemLog {self.log_type}: {self.message[:50]}>'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    _is_active = db.Column('is_active', db.Boolean, default=True)
    role = db.Column(db.String(20), default='viewer', nullable=False)
    permissions = db.Column(JSON, default=dict)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def is_active(self):
        """Return whether user is active - required by Flask-Login UserMixin"""
        return self._is_active
    
    @is_active.setter
    def is_active(self, value):
        """Set user active status"""
        self._is_active = value
    
    def set_password(self, password):
        """Set password hash"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check password against hash"""
        return check_password_hash(self.password_hash, password)
    
    def has_permission(self, permission):
        """Check if user has a specific permission"""
        if self.role == 'admin':
            return True
        if not self.permissions:
            return False
        return self.permissions.get(permission, False)
    
    def can_view_section(self, section):
        """Check if user can view a specific section"""
        if self.role == 'admin':
            return True
        section_key = f'view_{section}'
        return self.has_permission(section_key)
    
    def can_edit_section(self, section):
        """Check if user can edit a specific section"""
        if self.role == 'admin':
            return True
        section_key = f'edit_{section}'
        return self.has_permission(section_key)
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'is_active': self.is_active,
            'permissions': self.permissions or {},
            'last_login': self.last_login.isoformat() if self.last_login else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    def __repr__(self):
        return f'<User {self.username}>'

class ProductGroup(db.Model):
    __tablename__ = 'product_groups'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    group_key = db.Column(db.String(100), unique=True, nullable=False)  # Unique identifier for the group
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships (external_refs will be added after GroupExternalRef is defined)
    if TYPE_CHECKING:
        items: List['InventoryItem']
    else:
        items = db.relationship('InventoryItem', backref='group', lazy=True)
    
    def __repr__(self):
        return f'<ProductGroup {self.name}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'group_key': self.group_key,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'item_count': len(self.items) if self.items else 0
        }
    
    def get_aggregate_stats(self):
        """Get aggregate statistics for the group - uses warehouse stock as single source of truth"""
        if not self.items:
            return {
                'total_quantity': 0,
                'min_price': 0,
                'max_price': 0,
                'avg_price': 0,
                'item_count': 0
            }
        
        # For grouped items, use the PRIMARY/MAX warehouse quantity (not sum)
        # This represents the SHARED inventory pool across all platforms
        warehouse_quantities = []
        
        for item in self.items:
            # Find warehouse stock for this SKU
            warehouse_stock = WarehouseStock.query.filter_by(sku=item.sku).first()
            if warehouse_stock:
                warehouse_quantities.append(warehouse_stock.available_quantity)
        
        # Use the maximum warehouse quantity as the shared inventory pool
        # When items are grouped, they share the same physical inventory
        total_warehouse_qty = max(warehouse_quantities) if warehouse_quantities else 0
        
        prices = [item.price for item in self.items if item.price > 0]
        
        return {
            'total_quantity': total_warehouse_qty,  # Use max warehouse quantity (shared pool), not sum
            'min_price': min(prices) if prices else 0,
            'max_price': max(prices) if prices else 0,
            'avg_price': sum(prices) / len(prices) if prices else 0,
            'item_count': len(self.items)
        }

class InventoryItem(db.Model):
    __tablename__ = 'inventory_items'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    sku = db.Column(db.String(100), unique=True, nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    price = db.Column(db.Float, nullable=False, default=0.0)
    description = db.Column(db.Text)
    reorder_point = db.Column(db.Integer, nullable=True, default=0)  # Stock level trigger for alerts
    
    # Grouping fields
    group_id = db.Column(db.Integer, db.ForeignKey('product_groups.id', ondelete='SET NULL'), nullable=True)
    variant_attributes = db.Column(JSON, nullable=True)  # Store variant details like color, size, etc.
    
    if TYPE_CHECKING:
        # Relationships
        group: Optional['ProductGroup']
        warehouse_stock: Optional['WarehouseStock']
    else:
        # View-only relationship to WarehouseStock via SKU (no foreign key)
        warehouse_stock = db.relationship('WarehouseStock', 
                                         primaryjoin="InventoryItem.sku == foreign(WarehouseStock.sku)",
                                         uselist=False, 
                                         viewonly=True,
                                         lazy='select')
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<InventoryItem {self.name}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'sku': self.sku,
            'quantity': self.quantity,
            'price': self.price,
            'description': self.description,
            'group_id': self.group_id,
            'variant_attributes': self.variant_attributes,
            'group_name': self.group.name if self.group else None,
            'reorder_point': self.reorder_point,
            'needs_reorder': self.quantity <= (self.reorder_point or 0) if self.reorder_point else False,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class Store(db.Model):
    __tablename__ = 'stores'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    platform = db.Column(db.String(100), nullable=False)  # Amazon, eBay, etc.
    fulfillment_type = db.Column(db.String(10), nullable=True)  # DEPRECATED: Use fba_import_enabled/fbm_sync_enabled instead
    
    # Amazon FBA/FBM unified settings (single store handles both)
    fba_import_enabled = db.Column(db.Boolean, default=False)  # Enable FBA inventory import (read-only from Amazon)
    fbm_sync_enabled = db.Column(db.Boolean, default=True)  # Enable FBM stock sync (warehouse → Amazon)
    
    api_endpoint = db.Column(db.Text)  # Allow unlimited length for long URLs
    api_key = db.Column(db.Text)  # Allow unlimited length for OAuth tokens and JSON credentials
    is_active = db.Column(db.Boolean, default=True)
    
    # Basic push settings
    auto_push_enabled = db.Column(db.Boolean, default=True)  # Enable automatic stock push on inventory changes
    
    # Advanced push settings
    push_priority = db.Column(db.Integer, default=5)  # 1-10 priority for push order (10 = highest)
    push_frequency_minutes = db.Column(db.Integer, default=1)  # Minimum minutes between pushes to avoid rate limits
    push_batch_size = db.Column(db.Integer, default=10)  # Number of items to push in one batch
    
    # Push trigger conditions
    push_on_quantity_change = db.Column(db.Boolean, default=True)  # Push when quantity changes
    push_on_price_change = db.Column(db.Boolean, default=False)  # Push when price changes
    push_on_item_create = db.Column(db.Boolean, default=True)  # Push when new items are created
    push_on_item_update = db.Column(db.Boolean, default=False)  # Push when item details are updated
    
    # Error handling settings
    max_retry_attempts = db.Column(db.Integer, default=3)  # Number of retries on push failure
    auto_disable_on_failures = db.Column(db.Boolean, default=True)  # Auto-disable after repeated failures
    failure_threshold = db.Column(db.Integer, default=5)  # Number of consecutive failures before auto-disable
    current_failure_count = db.Column(db.Integer, default=0)  # Current consecutive failure count
    
    # Push preferences
    immediate_push = db.Column(db.Boolean, default=True)  # Push immediately or batch for later
    large_change_confirmation = db.Column(db.Boolean, default=False)  # Require confirmation for large inventory changes
    large_change_threshold = db.Column(db.Integer, default=100)  # Threshold for "large" quantity changes
    
    last_sync = db.Column(db.DateTime)
    last_push_attempt = db.Column(db.DateTime)  # Track last push attempt for throttling
    sync_status = db.Column(db.String(50), default='pending')  # pending, syncing, success, error
    
    # Auto-resume functionality (for rate limit cooldown, etc.)
    auto_resume_at = db.Column(db.DateTime, nullable=True)  # When to automatically re-enable auto_push
    pause_reason = db.Column(db.String(200), nullable=True)  # Why the store was paused
    
    # Amazon OAuth/Auth Status fields (for graceful auth failure handling)
    auth_status = db.Column(db.String(20), default='ok')  # ok, auth_error, pending
    auth_error_code = db.Column(db.String(100), nullable=True)  # e.g., unauthorized_client, invalid_grant
    auth_error_message = db.Column(db.Text, nullable=True)  # Full error message from Amazon
    auth_error_at = db.Column(db.DateTime, nullable=True)  # When the auth error occurred
    
    # Bidirectional sync settings (marketplace → warehouse)
    reverse_sync_enabled = db.Column(db.Boolean, default=False)  # Enable marketplace-to-warehouse syncing
    sync_priority_policy = db.Column(db.String(50), default='warehouse')  # warehouse, marketplace, or last_write_wins
    
    # Go Live / Safe Mode settings (Stage 9)
    store_mode = db.Column(db.String(20), default='safe', nullable=False)  # 'safe' or 'live'
    go_live_completed_at = db.Column(db.DateTime, nullable=True)
    go_live_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def ebay_credentials(self) -> Optional[eBayCredentials]:
        """Get parsed eBay credentials from api_key JSON field (cached)"""
        if not hasattr(self, '_ebay_creds_cache'):
            self._ebay_creds_cache = eBayCredentials.from_json(self.api_key)
        return self._ebay_creds_cache
    
    @property
    def amazon_credentials(self) -> Optional[AmazonCredentials]:
        """Get parsed Amazon credentials from api_key JSON field (always fresh)"""
        return AmazonCredentials.from_json(self.api_key)
    
    def __repr__(self):
        return f'<Store {self.name} ({self.platform})>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'platform': self.platform,
            'fulfillment_type': self.fulfillment_type,
            'fba_import_enabled': self.fba_import_enabled,
            'fbm_sync_enabled': self.fbm_sync_enabled,
            'api_endpoint': self.api_endpoint,
            'is_active': self.is_active,
            'auto_push_enabled': self.auto_push_enabled,
            'push_priority': self.push_priority,
            'push_frequency_minutes': self.push_frequency_minutes,
            'push_batch_size': self.push_batch_size,
            'push_on_quantity_change': self.push_on_quantity_change,
            'push_on_price_change': self.push_on_price_change,
            'push_on_item_create': self.push_on_item_create,
            'push_on_item_update': self.push_on_item_update,
            'max_retry_attempts': self.max_retry_attempts,
            'auto_disable_on_failures': self.auto_disable_on_failures,
            'failure_threshold': self.failure_threshold,
            'current_failure_count': self.current_failure_count,
            'immediate_push': self.immediate_push,
            'large_change_confirmation': self.large_change_confirmation,
            'large_change_threshold': self.large_change_threshold,
            'last_sync': self.last_sync.isoformat() if self.last_sync else None,
            'last_push_attempt': self.last_push_attempt.isoformat() if self.last_push_attempt else None,
            'sync_status': self.sync_status,
            'auto_resume_at': self.auto_resume_at.isoformat() if self.auto_resume_at else None,
            'pause_reason': self.pause_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class SyncJob(db.Model):
    """Work queue for sync operations - enables concurrent background and manual pushes"""
    __tablename__ = 'sync_jobs'
    
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id'), nullable=False, index=True)
    job_type = db.Column(db.String(50), nullable=False)  # 'full_sync', 'push_item', 'import_listings'
    payload = db.Column(JSON, nullable=True)  # Job-specific data (e.g., {'item_id': 123})
    priority = db.Column(db.Integer, default=5, index=True)  # 1=low (background), 10=high (manual)
    status = db.Column(db.String(50), default='pending', index=True)  # pending, running, success, failed
    
    enqueued_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    heartbeat_at = db.Column(db.DateTime, nullable=True)
    
    error_message = db.Column(db.Text, nullable=True)
    retry_at = db.Column(db.DateTime, nullable=True)  # When to retry a failed job
    retry_count = db.Column(db.Integer, default=0)
    lock_token = db.Column(db.String(100), nullable=True)
    
    # Relationship to store
    store = db.relationship('Store', backref='sync_jobs')
    
    # Index for efficient queue queries
    __table_args__ = (
        Index('idx_sync_jobs_store_status_priority', 'store_id', 'status', 'priority'),
    )
    
    def __repr__(self):
        return f'<SyncJob {self.id} {self.job_type} store={self.store_id} status={self.status}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'store_id': self.store_id,
            'job_type': self.job_type,
            'payload': self.payload,
            'priority': self.priority,
            'status': self.status,
            'enqueued_at': self.enqueued_at.isoformat() if self.enqueued_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'heartbeat_at': self.heartbeat_at.isoformat() if self.heartbeat_at else None,
            'error_message': self.error_message,
            'retry_at': self.retry_at.isoformat() if self.retry_at else None,
            'retry_count': self.retry_count,
            'lock_token': self.lock_token
        }

class BulkJob(db.Model):
    """Track bulk operations on listings (push, unblock, sync, etc.)"""
    __tablename__ = 'bulk_jobs'
    
    id = db.Column(db.Integer, primary_key=True)
    job_type = db.Column(db.String(50), nullable=False, index=True)  # bulk_push, bulk_unblock, bulk_sync_warehouse
    status = db.Column(db.String(20), default='pending', index=True)  # pending, running, success, failed, partial
    
    total_items = db.Column(db.Integer, default=0)
    processed_items = db.Column(db.Integer, default=0)
    success_count = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)
    
    item_ids = db.Column(JSON, nullable=True)  # List of listing IDs or SKUs
    results = db.Column(JSON, nullable=True)  # Detailed results per item
    error_details = db.Column(JSON, nullable=True)  # List of {item_id, error} for failures
    
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    
    # Relationship
    user = db.relationship('User', backref='bulk_jobs')
    
    def __repr__(self):
        return f'<BulkJob {self.id} {self.job_type} status={self.status}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'job_type': self.job_type,
            'status': self.status,
            'total_items': self.total_items,
            'processed_items': self.processed_items,
            'success_count': self.success_count,
            'error_count': self.error_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'created_by': self.created_by
        }


class PushSettings(db.Model):
    __tablename__ = 'push_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Global push settings
    global_push_enabled = db.Column(db.Boolean, default=True)  # Master switch for all automatic pushes
    default_push_frequency_minutes = db.Column(db.Integer, default=1)  # Default frequency for new stores
    default_batch_size = db.Column(db.Integer, default=10)  # Default batch size for new stores
    default_retry_attempts = db.Column(db.Integer, default=3)  # Default retry attempts for new stores
    
    # Push scheduling settings
    enable_batch_scheduling = db.Column(db.Boolean, default=False)  # Enable scheduled batch pushes
    batch_schedule_minutes = db.Column(db.Integer, default=30)  # How often to run scheduled batches
    off_hours_only = db.Column(db.Boolean, default=False)  # Only push during off-peak hours
    off_hours_start = db.Column(db.Integer, default=22)  # Off-hours start (24-hour format)
    off_hours_end = db.Column(db.Integer, default=6)  # Off-hours end (24-hour format)
    
    # Safety and confirmation settings
    require_confirmation_threshold = db.Column(db.Integer, default=50)  # Items count requiring confirmation
    auto_pause_on_errors = db.Column(db.Boolean, default=True)  # Auto-pause on widespread errors
    error_rate_threshold = db.Column(db.Float, default=0.3)  # Error rate (0.3 = 30%) to trigger auto-pause
    
    # Notification settings
    notify_on_large_pushes = db.Column(db.Boolean, default=True)  # Notify for large batches
    notify_on_failures = db.Column(db.Boolean, default=True)  # Notify on push failures
    daily_summary_enabled = db.Column(db.Boolean, default=False)  # Send daily push summary
    
    # Advanced settings
    concurrent_store_pushes = db.Column(db.Integer, default=3)  # Max stores to push to simultaneously
    api_rate_limit_buffer = db.Column(db.Float, default=0.8)  # Use 80% of API rate limits for safety
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<PushSettings global_enabled={self.global_push_enabled}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'global_push_enabled': self.global_push_enabled,
            'default_push_frequency_minutes': self.default_push_frequency_minutes,
            'default_batch_size': self.default_batch_size,
            'default_retry_attempts': self.default_retry_attempts,
            'enable_batch_scheduling': self.enable_batch_scheduling,
            'batch_schedule_minutes': self.batch_schedule_minutes,
            'off_hours_only': self.off_hours_only,
            'off_hours_start': self.off_hours_start,
            'off_hours_end': self.off_hours_end,
            'require_confirmation_threshold': self.require_confirmation_threshold,
            'auto_pause_on_errors': self.auto_pause_on_errors,
            'error_rate_threshold': self.error_rate_threshold,
            'notify_on_large_pushes': self.notify_on_large_pushes,
            'notify_on_failures': self.notify_on_failures,
            'daily_summary_enabled': self.daily_summary_enabled,
            'concurrent_store_pushes': self.concurrent_store_pushes,
            'api_rate_limit_buffer': self.api_rate_limit_buffer,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    @classmethod
    def get_or_create_settings(cls):
        """Get existing settings or create default ones"""
        settings = cls.query.first()
        if not settings:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings

class SyncLog(db.Model):
    __tablename__ = 'sync_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    status = db.Column(db.String(50), nullable=False)  # started, completed, failed
    message = db.Column(db.Text)
    items_synced = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    if TYPE_CHECKING:
        store: 'Store'
    else:
        store = db.relationship('Store', backref=db.backref('sync_logs', lazy=True))
    
    def __repr__(self):
        return f'<SyncLog {self.store.name if self.store else "Unknown"} - {self.status}>'

class FeedStatus(db.Model):
    """Track Amazon SP-API Feed submissions and their processing status"""
    __tablename__ = 'feed_status'
    
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    feed_id = db.Column(db.String(100), unique=True, nullable=False)  # Amazon feed ID
    feed_type = db.Column(db.String(100), nullable=False)  # POST_INVENTORY_AVAILABILITY_DATA, etc.
    processing_status = db.Column(db.String(50), default='IN_QUEUE')  # IN_QUEUE, IN_PROGRESS, DONE, FATAL, CANCELLED
    result_feed_document_id = db.Column(db.String(100))  # Document ID for results
    
    # Related item information
    sku = db.Column(db.String(100))  # SKU this feed affects
    quantity_pushed = db.Column(db.Integer)  # Quantity attempted to push
    
    # Status tracking
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow)
    processing_started_at = db.Column(db.DateTime)
    processing_ended_at = db.Column(db.DateTime)
    last_checked_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Results
    success = db.Column(db.Boolean)  # Final success/failure
    error_message = db.Column(db.Text)  # Error details if failed
    result_summary = db.Column(db.Text)  # Summary of results
    
    if TYPE_CHECKING:
        store: 'Store'
    else:
        store = db.relationship('Store', backref=db.backref('feed_submissions', lazy=True))
    
    def __repr__(self):
        return f'<FeedStatus {self.feed_id} - {self.processing_status}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'feed_id': self.feed_id,
            'feed_type': self.feed_type,
            'processing_status': self.processing_status,
            'sku': self.sku,
            'quantity_pushed': self.quantity_pushed,
            'submitted_at': self.submitted_at.isoformat() if self.submitted_at else None,
            'processing_ended_at': self.processing_ended_at.isoformat() if self.processing_ended_at else None,
            'success': self.success,
            'error_message': self.error_message
        }

class GroupExternalRef(db.Model):
    __tablename__ = 'group_external_refs'
    
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('product_groups.id', ondelete='CASCADE'), nullable=False)
    platform = db.Column(db.String(50), nullable=False)  # Amazon, eBay, etc.
    external_id = db.Column(db.String(200), nullable=False)  # External platform identifier
    external_type = db.Column(db.String(50), default='listing')  # variation_parent, listing_group, etc.
    external_data = db.Column(JSON, nullable=True)  # Store additional platform-specific data
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        product_group: 'ProductGroup'
    else:
        product_group = db.relationship('ProductGroup', backref=db.backref('external_refs', lazy=True, cascade='all, delete-orphan'))
    
    # Ensure unique external references per platform
    __table_args__ = (
        Index('idx_group_platform_external', 'platform', 'external_id', unique=True),
        Index('idx_group_id', 'group_id'),
    )
    
    def __repr__(self):
        return f'<GroupExternalRef {self.platform}:{self.external_id} -> {self.group_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'group_id': self.group_id,
            'platform': self.platform,
            'external_id': self.external_id,
            'external_type': self.external_type,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class SkuExternalRef(db.Model):
    """Direct mapping between individual SKUs and their external platform ItemIDs"""
    __tablename__ = 'sku_external_refs'
    
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(100), nullable=False)  # Our internal SKU
    platform = db.Column(db.String(50), nullable=False)  # Amazon, eBay, etc.
    external_item_id = db.Column(db.String(200), nullable=False)  # Platform's ItemID (numeric for eBay)
    external_sku = db.Column(db.String(200), nullable=True)  # Platform's SKU (if different)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Ensure unique mapping per platform-sku combination
    __table_args__ = (
        Index('idx_sku_platform_unique', 'sku', 'platform', unique=True),
        Index('idx_platform_external_item_id', 'platform', 'external_item_id'),
    )
    
    def __repr__(self):
        return f'<SkuExternalRef {self.sku} -> {self.platform}:{self.external_item_id}>'

class StoreItemSync(db.Model):
    """Track which SKUs were seen during syncs for each store"""
    __tablename__ = 'store_item_syncs'
    
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    sku = db.Column(db.String(100), nullable=False)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    if TYPE_CHECKING:
        store: 'Store'
    else:
        store = db.relationship('Store', backref=db.backref('item_syncs', lazy=True))
    
    # Ensure unique tracking per store-sku combination
    __table_args__ = (
        Index('idx_store_item_unique', 'store_id', 'sku', unique=True),
        Index('idx_store_item_last_seen', 'store_id', 'last_seen_at'),
    )
    
    def __repr__(self):
        return f'<StoreItemSync {self.store.name if self.store else "Unknown"}:{self.sku}>'


class StoreGoLiveAudit(db.Model):
    """Audit trail for Go Live mode changes"""
    __tablename__ = 'store_go_live_audit'
    
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    action = db.Column(db.String(50), nullable=False)
    checklist_snapshot = db.Column(db.JSON)
    terms_version = db.Column(db.String(50))
    ip_address = db.Column(db.String(45))
    session_id = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    store = db.relationship('Store', backref=db.backref('go_live_audits', lazy='dynamic'))
    user = db.relationship('User')


class Supplier(db.Model):
    """Supplier information for product reordering and automation"""
    __tablename__ = 'suppliers'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    whatsapp_number = db.Column(db.String(50))
    contact_person = db.Column(db.String(200))
    address = db.Column(db.Text)
    notes = db.Column(db.Text)
    
    is_active = db.Column(db.Boolean, default=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    if TYPE_CHECKING:
        warehouse_items: List['WarehouseStock']
    else:
        warehouse_items = db.relationship('WarehouseStock', back_populates='supplier', lazy=True)
    
    def __repr__(self):
        return f'<Supplier {self.name}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'whatsapp_number': self.whatsapp_number,
            'contact_person': self.contact_person,
            'address': self.address,
            'notes': self.notes,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'product_count': len(self.warehouse_items) if self.warehouse_items else 0
        }

class Warehouse(db.Model):
    """Physical warehouse locations for multi-warehouse inventory management"""
    __tablename__ = 'warehouses'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    location = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    is_default = db.Column(db.Boolean, default=False)
    priority = db.Column(db.Integer, default=0)  # Higher priority warehouses are used first
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        stock_items: List['WarehouseStock']
    else:
        stock_items = db.relationship('WarehouseStock', back_populates='warehouse', lazy=True)
    
    @classmethod
    def get_default(cls):
        """Get the default warehouse, creating one if it doesn't exist"""
        from extensions import db
        default = cls.query.filter_by(is_default=True).first()
        if not default:
            # Create default warehouse if none exists
            default = cls(
                name='Primary Warehouse',
                location='Default Location',
                is_active=True,
                is_default=True,
                priority=100
            )
            db.session.add(default)
            db.session.flush()
        return default
    
    def __repr__(self):
        return f'<Warehouse {self.name}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'location': self.location,
            'is_active': self.is_active,
            'is_default': self.is_default,
            'priority': self.priority,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'stock_count': len(self.stock_items) if self.stock_items else 0
        }

class WarehouseStock(db.Model):
    """Authoritative warehouse inventory record - single source of truth for stock quantities"""
    __tablename__ = 'warehouse_stock'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='CASCADE'), nullable=False)
    sku = db.Column(db.String(100), nullable=False)
    
    __table_args__ = (
        db.UniqueConstraint('warehouse_id', 'sku', name='uq_warehouse_sku'),
    )
    
    # Core warehouse inventory data
    available_quantity = db.Column(db.Integer, nullable=False, default=0)  # Available for sale
    reserved_quantity = db.Column(db.Integer, nullable=False, default=0)  # Reserved for orders
    allocated_quantity = db.Column(db.Integer, nullable=False, default=0)  # Allocated to specific orders
    on_order_quantity = db.Column(db.Integer, nullable=False, default=0)  # Expected from suppliers
    
    # Receiving workflow quantities - controls marketplace pushes
    pending_receipt_qty = db.Column(db.Integer, nullable=False, default=0)  # Received but NOT confirmed (NOT available for sale)
    quarantined_quantity = db.Column(db.Integer, nullable=False, default=0)  # Damaged/rejected stock (NOT available for sale)
    
    # Supplier relationship
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    
    # Product information
    product_name = db.Column(db.String(255))  # Product name/description
    image_url = db.Column(db.String(500))  # Product image URL
    
    # Warehouse management fields
    location = db.Column(db.String(100))  # Warehouse location/bin
    unit_cost = db.Column(db.Float, nullable=False, default=0.0)  # Cost per unit
    reorder_point = db.Column(db.Integer, nullable=False, default=0)  # When to reorder
    reorder_quantity = db.Column(db.Integer, nullable=False, default=0)  # How much to reorder
    
    # Financial tracking fields
    commission_rate = db.Column(db.Float, nullable=False, default=0.0)  # Commission percentage (e.g., 15.0 for 15%)
    operating_cost_per_unit = db.Column(db.Float, nullable=False, default=0.0)  # Fixed operating cost per unit
    product_weight_kg = db.Column(db.Float, nullable=False, default=0.0)  # Product weight in kilograms
    shipping_cost_per_kg = db.Column(db.Float, nullable=False, default=0.0)  # Shipping rate per kg
    
    # India import costing fields
    mrp_inr = db.Column(db.Float, nullable=True)  # Maximum retail price in Indian Rupees
    purchase_cost_inr = db.Column(db.Float, nullable=True)  # Actual purchase cost with discount (INR)
    std_pack_size = db.Column(db.Integer, nullable=True)  # Standard pack size
    freight_cost_per_unit = db.Column(db.Float, nullable=True)  # Calculated shipping cost per unit
    agent_name = db.Column(db.String(100), nullable=True)  # Sourcing agent name (e.g., Arpit)
    agent_commission_pct = db.Column(db.Float, nullable=True)  # Agent commission percentage
    agent_commission_amt = db.Column(db.Float, nullable=True)  # Agent commission amount per unit
    total_landed_cost = db.Column(db.Float, nullable=True)  # Total landed cost per unit (INR)
    cost_currency = db.Column(db.String(3), default='INR')  # Currency code for costs
    inr_to_gbp_rate = db.Column(db.Float, nullable=True)  # Exchange rate used for conversion
    
    # Product identification
    barcode = db.Column(db.String(100), index=True)  # EAN/UPC barcode for product matching
    
    # Master product group link
    master_product_group_id = db.Column(db.Integer, db.ForeignKey('master_product_groups.id', ondelete='SET NULL'), nullable=True, index=True)
    
    # Status and control
    is_active = db.Column(db.Boolean, default=True)  # Active in warehouse
    track_inventory = db.Column(db.Boolean, default=True)  # Track this SKU
    allow_negative = db.Column(db.Boolean, default=False)  # Allow negative quantities
    is_archived = db.Column(db.Boolean, default=False)  # Archived SKU (hidden but not deleted)
    is_discontinued = db.Column(db.Boolean, default=False)  # Marked as discontinued
    damaged_count = db.Column(db.Integer, nullable=False, default=0)  # Damaged/unsellable stock from FBA returns
    is_deleted = db.Column(db.Boolean, default=False)  # Soft-deleted (master delete)
    deleted_at = db.Column(db.DateTime)  # Timestamp of soft deletion
    
    # GROUP CONTROL STATE - Explicit group membership indicator
    # When True: SKU is group-controlled, individual push rules are disabled, quantity routes through group logic
    # When False: SKU operates as individual with its own push rules
    # ONLY GroupView can set this to True (on user confirmation)
    # NEVER auto-unlink or set to False without explicit user action
    is_group_controlled = db.Column(db.Boolean, default=False)
    group_controlled_at = db.Column(db.DateTime)  # When SKU became group-controlled
    
    # Stable group title for Product Linking UI (first link wins, never overwrite)
    group_title = db.Column(db.Text, nullable=True)  # Stable display title for product linking UI
    group_title_source_listing_id = db.Column(db.Integer, nullable=True)  # Listing ID that provided group_title
    
    # Concurrency control - prevents race conditions in multi-channel sales
    stock_version = db.Column(db.Integer, nullable=False, default=0)  # Incremented on each stock mutation for optimistic locking
    
    # Audit fields
    last_adjustment_at = db.Column(db.DateTime)  # Last manual adjustment
    last_adjustment_by = db.Column(db.String(100))  # Who made the last adjustment
    last_sync_at = db.Column(db.DateTime)  # Last marketplace sync
    last_reorder_alert_at = db.Column(db.DateTime)  # Last reorder notification sent
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        ledger_entries: List['StockLedgerEntry']
        marketplace_listings: List['MarketplaceListing']
        supplier: Optional['Supplier']
        warehouse: 'Warehouse'
        master_group: Optional['MasterProductGroup']
    else:
        ledger_entries = db.relationship('StockLedgerEntry', back_populates='warehouse_stock', lazy=True)
        marketplace_listings = db.relationship('MarketplaceListing', back_populates='warehouse_stock', lazy=True)
        supplier = db.relationship('Supplier', back_populates='warehouse_items', lazy=True)
        warehouse = db.relationship('Warehouse', back_populates='stock_items', lazy=True)
        master_group = db.relationship('MasterProductGroup', back_populates='warehouse_stocks', lazy=True)
    
    def __repr__(self):
        warehouse_name = self.warehouse.name if self.warehouse else 'Unknown'
        return f'<WarehouseStock {warehouse_name}/{self.sku}: {self.available_quantity} available>'
    
    @property
    def total_quantity(self):
        """Total physical quantity in warehouse"""
        return self.available_quantity + self.reserved_quantity + self.allocated_quantity
    
    @property
    def needs_reorder(self):
        """Whether this SKU needs to be reordered"""
        return self.available_quantity <= self.reorder_point and self.track_inventory
    
    @property
    def sellable_quantity(self):
        """Quantity available for marketplace pushes and new orders.
        
        CRITICAL: Only includes CONFIRMED/RECEIVED stock that is ready for sale.
        
        INVARIANT: available_quantity ONLY includes confirmed stock after receiving workflow.
        During receiving:
        - Damaged stock goes to quarantined_quantity (never touches available_quantity)
        - Pending stock stays in pending_receipt_qty (not in available_quantity)
        - Only on confirmation: confirmed_qty = received - damaged → available_quantity
        
        Therefore, available_quantity already excludes pending and quarantined stock.
        
        This property further excludes:
        - reserved_quantity: Reserved for existing orders
        - allocated_quantity: Allocated to specific orders
        
        This is the ONLY quantity that should be pushed to marketplaces.
        """
        return max(0, self.available_quantity - self.reserved_quantity - self.allocated_quantity)
    
    @classmethod
    def get_total_sellable_for_sku(cls, sku: str) -> int:
        """Get total sellable quantity across all active warehouses for a SKU"""
        from sqlalchemy import func
        result = db.session.query(
            func.sum(cls.available_quantity - cls.reserved_quantity - cls.allocated_quantity)
        ).join(Warehouse).filter(
            cls.sku == sku,
            cls.is_active == True,
            Warehouse.is_active == True
        ).scalar()
        return max(0, result or 0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_id': self.warehouse_id,
            'warehouse_name': self.warehouse.name if self.warehouse else None,
            'sku': self.sku,
            'product_name': self.product_name,
            'barcode': self.barcode,
            'master_product_group_id': self.master_product_group_id,
            'available_quantity': self.available_quantity,
            'reserved_quantity': self.reserved_quantity,
            'allocated_quantity': self.allocated_quantity,
            'on_order_quantity': self.on_order_quantity,
            'pending_receipt_qty': self.pending_receipt_qty,
            'quarantined_quantity': self.quarantined_quantity,
            'total_quantity': self.total_quantity,
            'sellable_quantity': self.sellable_quantity,
            'location': self.location,
            'unit_cost': self.unit_cost,
            'reorder_point': self.reorder_point,
            'reorder_quantity': self.reorder_quantity,
            'is_active': self.is_active,
            'track_inventory': self.track_inventory,
            'allow_negative': self.allow_negative,
            'needs_reorder': self.needs_reorder,
            'last_adjustment_at': self.last_adjustment_at.isoformat() if self.last_adjustment_at else None,
            'last_adjustment_by': self.last_adjustment_by,
            'last_sync_at': self.last_sync_at.isoformat() if self.last_sync_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def populate_product_name(self, name: str = None):
        """Populate product_name from provided name, InventoryItem, or MarketplaceListing.
        
        Priority:
        1. Explicit name parameter
        2. InventoryItem with matching SKU
        3. MarketplaceListing title linked to this stock
        """
        if name:
            self.product_name = name[:255]
            return True
        
        if not self.product_name:
            item = InventoryItem.query.filter_by(sku=self.sku).first()
            if item and item.name:
                self.product_name = item.name[:255]
                return True
            
            if self.marketplace_listings:
                for listing in self.marketplace_listings:
                    if listing.title:
                        self.product_name = listing.title[:255]
                        return True
        
        return False


class ProductCosting(db.Model):
    """Historical cost records for warehouse items - tracks cost changes over time"""
    __tablename__ = 'product_costing'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=False)
    
    # Source information
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_orders.id'), nullable=True)
    
    # India import cost fields
    mrp_inr = db.Column(db.Float, nullable=True)  # Maximum retail price (INR)
    purchase_cost_inr = db.Column(db.Float, nullable=False, default=0.0)  # Actual purchase cost with discount (INR)
    std_pack_size = db.Column(db.Integer, default=1)  # Standard pack size
    
    # Weight and shipping
    product_weight_kg = db.Column(db.Float, nullable=False, default=0.0)  # Weight per unit in kg
    freight_rate_inr_per_kg = db.Column(db.Float, nullable=False, default=450.0)  # Shipping rate per kg (INR)
    freight_cost_per_unit = db.Column(db.Float, nullable=True)  # Calculated: weight × rate
    
    # Commission
    agent_name = db.Column(db.String(100), nullable=True)  # Sourcing agent name
    agent_commission_pct = db.Column(db.Float, default=0.0)  # Commission percentage
    agent_commission_amt = db.Column(db.Float, nullable=True)  # Calculated commission amount
    
    # Totals
    cost_of_goods_inr = db.Column(db.Float, nullable=True)  # Base cost of goods
    total_cost_per_unit_inr = db.Column(db.Float, nullable=True)  # Total landed cost per unit
    
    # Currency conversion
    cost_currency = db.Column(db.String(3), default='INR')  # Source currency
    exchange_rate = db.Column(db.Float, nullable=True)  # Rate to GBP at time of purchase
    total_cost_per_unit_gbp = db.Column(db.Float, nullable=True)  # Converted to GBP
    
    # Metadata
    effective_date = db.Column(db.Date, nullable=False, default=datetime.utcnow)  # When this cost became effective
    is_current = db.Column(db.Boolean, default=True)  # Is this the current active cost
    notes = db.Column(db.Text, nullable=True)  # Additional notes
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stock: 'WarehouseStock'
        supplier: Optional['Supplier']
    else:
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('costing_records', lazy=True))
        supplier = db.relationship('Supplier', backref=db.backref('costing_records', lazy=True))
    
    def calculate_totals(self):
        """Calculate derived cost fields"""
        # Calculate freight cost per unit
        if self.product_weight_kg and self.freight_rate_inr_per_kg:
            self.freight_cost_per_unit = self.product_weight_kg * self.freight_rate_inr_per_kg
        
        # Calculate agent commission amount
        if self.agent_commission_pct and self.purchase_cost_inr:
            self.agent_commission_amt = (self.agent_commission_pct / 100.0) * self.purchase_cost_inr
        
        # Calculate cost of goods (purchase cost + freight)
        self.cost_of_goods_inr = (self.purchase_cost_inr or 0) + (self.freight_cost_per_unit or 0)
        
        # Calculate total cost per unit
        self.total_cost_per_unit_inr = self.cost_of_goods_inr + (self.agent_commission_amt or 0)
        
        # Convert to GBP if exchange rate is set
        if self.exchange_rate and self.total_cost_per_unit_inr:
            self.total_cost_per_unit_gbp = self.total_cost_per_unit_inr / self.exchange_rate
    
    def __repr__(self):
        sku = self.warehouse_stock.sku if self.warehouse_stock else 'Unknown'
        return f'<ProductCosting {sku}: ₹{self.total_cost_per_unit_inr or 0:.2f}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_stock_id': self.warehouse_stock_id,
            'sku': self.warehouse_stock.sku if self.warehouse_stock else None,
            'supplier_id': self.supplier_id,
            'supplier_name': self.supplier.name if self.supplier else None,
            'mrp_inr': self.mrp_inr,
            'purchase_cost_inr': self.purchase_cost_inr,
            'std_pack_size': self.std_pack_size,
            'product_weight_kg': self.product_weight_kg,
            'freight_rate_inr_per_kg': self.freight_rate_inr_per_kg,
            'freight_cost_per_unit': self.freight_cost_per_unit,
            'agent_name': self.agent_name,
            'agent_commission_pct': self.agent_commission_pct,
            'agent_commission_amt': self.agent_commission_amt,
            'cost_of_goods_inr': self.cost_of_goods_inr,
            'total_cost_per_unit_inr': self.total_cost_per_unit_inr,
            'exchange_rate': self.exchange_rate,
            'total_cost_per_unit_gbp': self.total_cost_per_unit_gbp,
            'effective_date': self.effective_date.isoformat() if self.effective_date else None,
            'is_current': self.is_current,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class StockLedgerEntry(db.Model):
    """Immutable audit trail for all inventory changes"""
    __tablename__ = 'stock_ledger_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=False)
    
    # Transaction details
    transaction_type = db.Column(db.String(50), nullable=False)  # adjustment, sale, return, restock, etc.
    adjustment_type = db.Column(db.String(50), nullable=False)  # increase, decrease, set
    
    # Track all quantity field changes (before and after values)
    available_quantity_before = db.Column(db.Integer, nullable=False, default=0)
    available_quantity_after = db.Column(db.Integer, nullable=False, default=0)
    reserved_quantity_before = db.Column(db.Integer, nullable=False, default=0)
    reserved_quantity_after = db.Column(db.Integer, nullable=False, default=0)
    allocated_quantity_before = db.Column(db.Integer, nullable=False, default=0)
    allocated_quantity_after = db.Column(db.Integer, nullable=False, default=0)
    on_order_quantity_before = db.Column(db.Integer, nullable=False, default=0)
    on_order_quantity_after = db.Column(db.Integer, nullable=False, default=0)
    pending_receipt_qty_before = db.Column(db.Integer, nullable=False, default=0)
    pending_receipt_qty_after = db.Column(db.Integer, nullable=False, default=0)
    quarantined_quantity_before = db.Column(db.Integer, nullable=False, default=0)
    quarantined_quantity_after = db.Column(db.Integer, nullable=False, default=0)
    
    # Context and audit information
    reason = db.Column(db.String(200))  # Human-readable reason
    reference_id = db.Column(db.String(100))  # External reference (order ID, etc.)
    reference_type = db.Column(db.String(50))  # order, return, adjustment, sync, etc.
    
    # User and system tracking
    created_by = db.Column(db.String(100))  # User or system that made the change
    batch_id = db.Column(db.String(100))  # Group related changes together
    source_system = db.Column(db.String(50), default='warehouse')  # warehouse, marketplace, api, etc.
    update_source = db.Column(db.String(50), default='warehouse_manual')  # warehouse_manual, warehouse_receiving, marketplace_sync, system_adjustment
    
    # Additional context
    notes = db.Column(db.Text)  # Additional details
    entry_metadata = db.Column(JSON)  # Store additional context data
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stock: 'WarehouseStock'
    else:
        warehouse_stock = db.relationship('WarehouseStock', back_populates='ledger_entries')
    
    def __repr__(self):
        total_change = (self.available_quantity_after - self.available_quantity_before +
                       self.reserved_quantity_after - self.reserved_quantity_before +
                       self.allocated_quantity_after - self.allocated_quantity_before +
                       self.on_order_quantity_after - self.on_order_quantity_before)
        return f'<StockLedgerEntry {self.warehouse_stock.sku if self.warehouse_stock else "Unknown"}: {total_change:+d} ({self.transaction_type})>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_stock_id': self.warehouse_stock_id,
            'sku': self.warehouse_stock.sku if self.warehouse_stock else None,
            'transaction_type': self.transaction_type,
            'adjustment_type': self.adjustment_type,
            'available_quantity_before': self.available_quantity_before,
            'available_quantity_after': self.available_quantity_after,
            'available_quantity_change': self.available_quantity_after - self.available_quantity_before,
            'reserved_quantity_before': self.reserved_quantity_before,
            'reserved_quantity_after': self.reserved_quantity_after,
            'reserved_quantity_change': self.reserved_quantity_after - self.reserved_quantity_before,
            'allocated_quantity_before': self.allocated_quantity_before,
            'allocated_quantity_after': self.allocated_quantity_after,
            'allocated_quantity_change': self.allocated_quantity_after - self.allocated_quantity_before,
            'on_order_quantity_before': self.on_order_quantity_before,
            'on_order_quantity_after': self.on_order_quantity_after,
            'on_order_quantity_change': self.on_order_quantity_after - self.on_order_quantity_before,
            'reason': self.reason,
            'reference_id': self.reference_id,
            'reference_type': self.reference_type,
            'created_by': self.created_by,
            'batch_id': self.batch_id,
            'source_system': self.source_system,
            'notes': self.notes,
            'entry_metadata': self.entry_metadata,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class ReorderNotification(db.Model):
    """Track reorder notifications sent to suppliers"""
    __tablename__ = 'reorder_notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id', ondelete='SET NULL'), nullable=True)
    
    # Notification details
    notification_type = db.Column(db.String(20), nullable=False)  # email, whatsapp, both
    recipient_email = db.Column(db.String(200))
    recipient_whatsapp = db.Column(db.String(50))
    
    # Stock information at time of notification
    current_quantity = db.Column(db.Integer, nullable=False)
    reorder_point = db.Column(db.Integer, nullable=False)
    reorder_quantity = db.Column(db.Integer, nullable=False)
    
    # Notification status
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed
    sent_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    
    # Prevent duplicate notifications
    notification_hash = db.Column(db.String(64))  # Hash of SKU+date to prevent duplicates
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stock: 'WarehouseStock'
        supplier: Optional['Supplier']
    else:
        warehouse_stock = db.relationship('WarehouseStock', backref='reorder_notifications', lazy=True)
        supplier = db.relationship('Supplier', backref='reorder_notifications', lazy=True)
    
    def __repr__(self):
        return f'<ReorderNotification {self.warehouse_stock.sku if self.warehouse_stock else "Unknown"} - {self.status}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_stock_id': self.warehouse_stock_id,
            'sku': self.warehouse_stock.sku if self.warehouse_stock else None,
            'supplier_id': self.supplier_id,
            'supplier_name': self.supplier.name if self.supplier else None,
            'notification_type': self.notification_type,
            'recipient_email': self.recipient_email,
            'recipient_whatsapp': self.recipient_whatsapp,
            'current_quantity': self.current_quantity,
            'reorder_point': self.reorder_point,
            'reorder_quantity': self.reorder_quantity,
            'status': self.status,
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class PurchaseOrder(db.Model):
    """Purchase orders from suppliers - tracks incoming inventory"""
    __tablename__ = 'purchase_orders'
    
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(100), unique=True, nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id', ondelete='SET NULL'), nullable=True)
    
    # Order details
    order_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expected_date = db.Column(db.DateTime)
    received_date = db.Column(db.DateTime)
    
    # Status tracking
    status = db.Column(db.String(20), default='draft')  # draft, sent, received, partially_received, cancelled
    
    # Financial
    total_amount = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(3), default='GBP')
    
    # Invoice tracking
    invoice_number = db.Column(db.String(100))  # Supplier invoice number
    invoice_date = db.Column(db.DateTime)  # Date on supplier invoice
    payment_status = db.Column(db.String(20), default='pending')  # pending, paid, partial
    payment_date = db.Column(db.DateTime)  # When payment was made
    payment_amount = db.Column(db.Float, default=0.0)  # Amount paid (for partial payments)
    
    # Notes
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        supplier: Optional['Supplier']
        items: List['PurchaseOrderItem']
        receiving_inspections: List['ReceivingInspection']
    else:
        supplier = db.relationship('Supplier', backref='purchase_orders', lazy=True)
        items = db.relationship('PurchaseOrderItem', back_populates='purchase_order', lazy=True, cascade='all, delete-orphan')
        receiving_inspections = db.relationship('ReceivingInspection', back_populates='purchase_order', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<PurchaseOrder {self.po_number} - {self.status}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'po_number': self.po_number,
            'supplier_id': self.supplier_id,
            'supplier_name': self.supplier.name if self.supplier else None,
            'order_date': self.order_date.isoformat() if self.order_date else None,
            'expected_date': self.expected_date.isoformat() if self.expected_date else None,
            'received_date': self.received_date.isoformat() if self.received_date else None,
            'status': self.status,
            'total_amount': self.total_amount,
            'currency': self.currency,
            'notes': self.notes,
            'item_count': len(self.items) if self.items else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class PurchaseOrderItem(db.Model):
    """Line items in a purchase order"""
    __tablename__ = 'purchase_order_items'
    
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_orders.id', ondelete='CASCADE'), nullable=False)
    sku = db.Column(db.String(100), nullable=False)
    
    # Item details
    product_name = db.Column(db.String(200), nullable=False)
    ordered_quantity = db.Column(db.Integer, nullable=False)
    received_quantity = db.Column(db.Integer, default=0)
    damaged_quantity = db.Column(db.Integer, default=0)
    
    # Pricing
    unit_cost = db.Column(db.Float, nullable=False, default=0.0)
    total_cost = db.Column(db.Float, nullable=False, default=0.0)
    
    # Notes
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        purchase_order: 'PurchaseOrder'
    else:
        purchase_order = db.relationship('PurchaseOrder', back_populates='items')
    
    def __repr__(self):
        return f'<PurchaseOrderItem {self.sku} x{self.ordered_quantity}>'
    
    @property
    def pending_quantity(self):
        """Quantity still waiting to be received"""
        return max(0, self.ordered_quantity - self.received_quantity - self.damaged_quantity)
    
    def to_dict(self):
        return {
            'id': self.id,
            'purchase_order_id': self.purchase_order_id,
            'sku': self.sku,
            'product_name': self.product_name,
            'ordered_quantity': self.ordered_quantity,
            'received_quantity': self.received_quantity,
            'damaged_quantity': self.damaged_quantity,
            'pending_quantity': self.pending_quantity,
            'unit_cost': self.unit_cost,
            'total_cost': self.total_cost,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class ReceivingInspection(db.Model):
    """Track receiving inspections with quality control and damage reporting"""
    __tablename__ = 'receiving_inspections'
    
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_orders.id', ondelete='CASCADE'), nullable=False)
    
    # Inspection details
    inspection_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    inspected_by = db.Column(db.String(100))
    
    # Status
    status = db.Column(db.String(20), default='pending')  # pending, in_progress, completed, approved
    
    # Overall inspection notes
    notes = db.Column(db.Text)
    
    # Completion tracking
    completed_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    approved_by = db.Column(db.String(100))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        purchase_order: 'PurchaseOrder'
        line_items: List['ReceivingInspectionItem']
    else:
        purchase_order = db.relationship('PurchaseOrder', back_populates='receiving_inspections')
        line_items = db.relationship('ReceivingInspectionItem', back_populates='inspection', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<ReceivingInspection PO#{self.purchase_order.po_number if self.purchase_order else "Unknown"} - {self.status}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'purchase_order_id': self.purchase_order_id,
            'po_number': self.purchase_order.po_number if self.purchase_order else None,
            'inspection_date': self.inspection_date.isoformat() if self.inspection_date else None,
            'inspected_by': self.inspected_by,
            'status': self.status,
            'notes': self.notes,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'approved_by': self.approved_by,
            'line_item_count': len(self.line_items) if self.line_items else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class ReceivingInspectionItem(db.Model):
    """Individual line item inspection with damage tracking"""
    __tablename__ = 'receiving_inspection_items'
    
    id = db.Column(db.Integer, primary_key=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('receiving_inspections.id', ondelete='CASCADE'), nullable=False)
    po_item_id = db.Column(db.Integer, db.ForeignKey('purchase_order_items.id', ondelete='CASCADE'), nullable=False)
    
    # Inspection results
    quantity_received = db.Column(db.Integer, default=0)
    quantity_accepted = db.Column(db.Integer, default=0)
    quantity_damaged = db.Column(db.Integer, default=0)
    
    # Damage tracking
    damage_type = db.Column(db.String(50))  # broken, defective, wrong_item, missing_parts, packaging_damage, expired
    damage_severity = db.Column(db.String(20))  # minor, major, total_loss
    damage_notes = db.Column(db.Text)
    
    # Quality check
    inspection_passed = db.Column(db.Boolean, default=True)
    
    # Notes
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        inspection: 'ReceivingInspection'
        po_item: 'PurchaseOrderItem'
    else:
        inspection = db.relationship('ReceivingInspection', back_populates='line_items')
        po_item = db.relationship('PurchaseOrderItem', backref='inspection_items')
    
    def __repr__(self):
        return f'<ReceivingInspectionItem SKU:{self.po_item.sku if self.po_item else "Unknown"} - {self.quantity_accepted} accepted>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'inspection_id': self.inspection_id,
            'po_item_id': self.po_item_id,
            'sku': self.po_item.sku if self.po_item else None,
            'product_name': self.po_item.product_name if self.po_item else None,
            'quantity_received': self.quantity_received,
            'quantity_accepted': self.quantity_accepted,
            'quantity_damaged': self.quantity_damaged,
            'damage_type': self.damage_type,
            'damage_severity': self.damage_severity,
            'damage_notes': self.damage_notes,
            'inspection_passed': self.inspection_passed,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class WarehouseReceipt(db.Model):
    """Tracks warehouse stock receiving confirmations - controls when stock becomes available for sale"""
    __tablename__ = 'warehouse_receipts'
    
    id = db.Column(db.Integer, primary_key=True)
    receipt_number = db.Column(db.String(100), unique=True, nullable=False)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_orders.id', ondelete='SET NULL'), nullable=True)
    inspection_id = db.Column(db.Integer, db.ForeignKey('receiving_inspections.id', ondelete='SET NULL'), nullable=True)
    
    # Receipt status - controls availability for marketplace push
    status = db.Column(db.String(20), default='pending', nullable=False)  # pending, confirmed, rejected
    
    # Timestamps
    received_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # When physically received
    confirmed_date = db.Column(db.DateTime)  # When confirmed as "ready for sale"
    confirmed_by = db.Column(db.String(100))  # Who confirmed it
    
    # Notes
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        purchase_order: Optional['PurchaseOrder']
        inspection: Optional['ReceivingInspection']
        line_items: List['WarehouseReceiptLine']
    else:
        purchase_order = db.relationship('PurchaseOrder', backref='warehouse_receipts')
        inspection = db.relationship('ReceivingInspection', backref='warehouse_receipts')
        line_items = db.relationship('WarehouseReceiptLine', back_populates='receipt', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<WarehouseReceipt {self.receipt_number} - {self.status}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'receipt_number': self.receipt_number,
            'purchase_order_id': self.purchase_order_id,
            'po_number': self.purchase_order.po_number if self.purchase_order else None,
            'inspection_id': self.inspection_id,
            'status': self.status,
            'received_date': self.received_date.isoformat() if self.received_date else None,
            'confirmed_date': self.confirmed_date.isoformat() if self.confirmed_date else None,
            'confirmed_by': self.confirmed_by,
            'notes': self.notes,
            'line_item_count': len(self.line_items) if self.line_items else 0,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class WarehouseReceiptLine(db.Model):
    """Line items for warehouse receipts - tracks per-SKU confirmation status"""
    __tablename__ = 'warehouse_receipt_lines'
    
    id = db.Column(db.Integer, primary_key=True)
    receipt_id = db.Column(db.Integer, db.ForeignKey('warehouse_receipts.id', ondelete='CASCADE'), nullable=False)
    sku = db.Column(db.String(100), nullable=False)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='SET NULL'), nullable=True)
    
    # Quantity tracking
    received_quantity = db.Column(db.Integer, nullable=False, default=0)  # Physically received
    confirmed_quantity = db.Column(db.Integer, nullable=False, default=0)  # Confirmed as sellable
    damaged_quantity = db.Column(db.Integer, nullable=False, default=0)  # Damaged/quarantined
    
    # Status
    status = db.Column(db.String(20), default='pending', nullable=False)  # pending, confirmed, rejected
    
    # Notes
    notes = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        receipt: 'WarehouseReceipt'
        warehouse_stock: Optional['WarehouseStock']
    else:
        receipt = db.relationship('WarehouseReceipt', back_populates='line_items')
        warehouse_stock = db.relationship('WarehouseStock', backref='receipt_lines')
    
    def __repr__(self):
        return f'<WarehouseReceiptLine {self.sku} - {self.confirmed_quantity} confirmed>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'receipt_id': self.receipt_id,
            'sku': self.sku,
            'warehouse_stock_id': self.warehouse_stock_id,
            'received_quantity': self.received_quantity,
            'confirmed_quantity': self.confirmed_quantity,
            'damaged_quantity': self.damaged_quantity,
            'status': self.status,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

class MarketplaceListing(db.Model):
    """Maps warehouse items to marketplace listings for push operations"""
    __tablename__ = 'marketplace_listings'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='SET NULL'), nullable=True)  # Nullable for unlinked listings
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    
    # Master product group link
    master_product_group_id = db.Column(db.Integer, db.ForeignKey('master_product_groups.id', ondelete='SET NULL'), nullable=True, index=True)
    
    # Product identification
    barcode = db.Column(db.String(100), index=True)  # EAN/UPC barcode for product matching
    
    # Marketplace identifiers
    external_listing_id = db.Column(db.String(200), nullable=False)  # Platform's listing ID
    external_sku = db.Column(db.String(200))  # Platform's SKU (if different)
    external_parent_id = db.Column(db.String(200))  # For variations/variants
    
    # Amazon-specific identifiers (critical for FBA linking)
    asin = db.Column(db.String(20))  # Amazon Standard Identification Number (catalog identifier, 10 chars)
    fnsku = db.Column(db.String(40))  # Fulfillment Network SKU (unique FBA barcode identifier, up to 40 chars)
    amazon_fulfillment_channel = db.Column(db.String(10))  # FBA or MFN (Merchant Fulfilled Network)
    
    # Listing details
    title = db.Column(db.String(500))
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False, default=0.0)
    sale_price = db.Column(db.Float)  # Sale/promotional price
    currency = db.Column(db.String(3), default='GBP')
    price_missing = db.Column(db.Boolean, default=False)  # True if no price source found
    
    # Marketplace sync settings
    sync_quantity = db.Column(db.Boolean, default=True)  # Sync quantity to marketplace
    sync_price = db.Column(db.Boolean, default=False)  # Sync price to marketplace
    quantity_buffer = db.Column(db.Integer, default=0)  # Reduce quantity by this amount
    max_quantity_limit = db.Column(db.Integer)  # Maximum quantity to show on marketplace
    
    # Listing classification for flexible handling
    listing_type = db.Column(db.String(50), default='single')  # single, variation_parent, variation_child, unmapped
    parent_item_id = db.Column(db.String(200))  # Parent ItemID for variations
    variation_sku_map = db.Column(db.Text)  # JSON map of SKU to variation attributes
    push_state = db.Column(db.String(50), default='active')  # active, blocked, needs_review, disabled
    consecutive_failures = db.Column(db.Integer, default=0)  # Count of consecutive push failures
    
    # Status tracking
    is_active = db.Column(db.Boolean, default=True)  # Active listing
    deleted_by_master = db.Column(db.Boolean, default=False)  # Deleted because warehouse SKU was deleted
    deleted_at = db.Column(db.DateTime)  # When marked as deleted_by_master
    last_push_at = db.Column(db.DateTime)  # Last successful push
    last_push_quantity = db.Column(db.Integer)  # Last quantity pushed
    last_push_status = db.Column(db.String(50), default='pending')  # pending, success, error
    last_push_error = db.Column(db.Text)  # Last error message
    push_attempts = db.Column(db.Integer, default=0)  # Failed push attempts
    
    # Reverse sync tracking (marketplace → warehouse)
    last_marketplace_qty = db.Column(db.Integer)  # Last quantity reported by marketplace
    last_synced_at = db.Column(db.DateTime)  # Last time we synced from marketplace
    
    # eBay Item Specifics (category-specific attributes like Language, Brand, etc.)
    item_specifics = db.Column(JSON)  # Store eBay item specifics for validation
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stock: Optional['WarehouseStock']
        store: 'Store'
        master_group: Optional['MasterProductGroup']
    else:
        warehouse_stock = db.relationship('WarehouseStock', back_populates='marketplace_listings')
        store = db.relationship('Store', backref=db.backref('marketplace_listings', lazy=True))
        master_group = db.relationship('MasterProductGroup', back_populates='marketplace_listings', lazy=True)
    
    # Ensure unique marketplace row per operational sellable listing.
    # eBay variations can share the same ItemID, so external_sku must be part of identity.
    __table_args__ = (
        Index('idx_store_external_listing_sku', 'store_id', 'external_listing_id', 'external_sku', unique=True),
        Index('idx_warehouse_stock_store', 'warehouse_stock_id', 'store_id'),
        Index('idx_last_push_status', 'last_push_status'),
    )
    
    def __repr__(self):
        return f'<MarketplaceListing {self.warehouse_stock.sku if self.warehouse_stock else "Unknown"} @ {self.store.name if self.store else "Unknown"}>'
    
    @property
    def effective_quantity(self):
        """Calculate the quantity that should be pushed to marketplace"""
        if not self.warehouse_stock or not self.sync_quantity:
            return 0
        
        base_quantity = self.warehouse_stock.sellable_quantity - (self.quantity_buffer or 0)
        base_quantity = max(0, base_quantity)  # Never go negative
        
        if self.max_quantity_limit:
            base_quantity = min(base_quantity, self.max_quantity_limit)
        
        return base_quantity
    
    @property
    def normalized_amazon_fulfillment_channel(self):
        """
        Normalize the raw Amazon fulfillment channel without guessing.

        Blank/unknown Amazon channels must remain unclassified so the central
        marketplace push guard can fail closed. Only explicit MFN/FBM values are
        pushable; FBA/AFN-style values are read-only.
        """
        ch = (self.amazon_fulfillment_channel or "").strip().upper()
        return ch or None
    
    @property
    def is_pushable(self):
        """Whether this listing can be pushed (not blocked or needs review)
        
        UNIFIED MODEL SAFETY:
        - FBA listings (amazon_fulfillment_channel='AFN') are NEVER pushable
        - Only FBM listings ('MFN') or non-Amazon listings can be pushed
        - Uses normalized channel so NULL/empty does not become "unknown"
        """
        platform = (self.store.platform or "").strip().lower() if self.store else ""
        ch = self.normalized_amazon_fulfillment_channel
        if "amazon" in platform and (ch or "").upper() not in ("MFN", "FBM", "MERCHANT"):
            return False
        
        return self.push_state in ['active'] and self.consecutive_failures < 5
    
    @property
    def needs_push(self):
        """Whether this listing needs to be pushed to marketplace"""
        if not self.is_active or not self.sync_quantity or not self.is_pushable:
            return False
        
        # Push if quantity changed
        if self.last_push_quantity != self.effective_quantity:
            return True
        
        # Push if there were previous errors and we haven't pushed successfully recently
        if self.last_push_status == 'error' and self.push_attempts > 0:
            return True
        
        # Push if never pushed before
        if not self.last_push_at:
            return True
        
        return False
    
    @property
    def platform(self):
        """Determine the platform type for this listing.
        
        Returns: 'amazon_fba', 'amazon_fbm', or 'ebay' based on store/fulfillment channel
        """
        if not self.store:
            return 'unknown'
        
        store_platform = self.store.platform.lower() if self.store.platform else ''
        
        if 'ebay' in store_platform:
            return 'ebay'
        elif 'amazon' in store_platform:
            # Check fulfillment channel for FBA vs FBM
            if self.amazon_fulfillment_channel == 'AFN':
                return 'amazon_fba'
            else:
                return 'amazon_fbm'
        else:
            return store_platform or 'unknown'
    
    @property
    def is_fba(self):
        """Whether this is an Amazon FBA listing (read-only)"""
        return self.amazon_fulfillment_channel == 'AFN'
    
    @property
    def is_linkable(self):
        """Whether this listing can be linked to a warehouse product.
        
        Note: FBA listings CAN be linked for reporting purposes,
        but they remain read-only (no stock push).
        """
        return True  # All listings are linkable
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_stock_id': self.warehouse_stock_id,
            'master_product_group_id': self.master_product_group_id,
            'store_id': self.store_id,
            'platform': self.platform,
            'sku': self.warehouse_stock.sku if self.warehouse_stock else None,
            'store_name': self.store.name if self.store else None,
            'external_listing_id': self.external_listing_id,
            'external_id': self.asin or self.external_listing_id,  # ASIN for Amazon, Item ID for eBay
            'external_sku': self.external_sku,
            'external_parent_id': self.external_parent_id,
            'barcode': self.barcode,
            'title': self.title,
            'description': self.description,
            'price': self.price,
            'sale_price': self.sale_price,
            'currency': self.currency,
            'quantity': self.last_marketplace_qty or 0,  # Last known marketplace quantity
            'fulfillment_channel': self.amazon_fulfillment_channel,
            'is_fba': self.is_fba,
            'is_linkable': self.is_linkable,
            'sync_quantity': self.sync_quantity,
            'sync_price': self.sync_price,
            'quantity_buffer': self.quantity_buffer,
            'max_quantity_limit': self.max_quantity_limit,
            'listing_type': self.listing_type,
            'parent_item_id': self.parent_item_id,
            'push_state': self.push_state,
            'consecutive_failures': self.consecutive_failures,
            'is_pushable': self.is_pushable,
            'effective_quantity': self.effective_quantity,
            'needs_push': self.needs_push,
            'is_active': self.is_active,
            'item_specifics': self.item_specifics,
            'last_push_at': self.last_push_at.isoformat() if self.last_push_at else None,
            'last_push_quantity': self.last_push_quantity,
            'last_push_status': self.last_push_status,
            'last_push_error': self.last_push_error,
            'push_attempts': self.push_attempts,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AmazonFBAListing(db.Model):
    """
    Amazon FBA (Fulfillment by Amazon) listings - inventory managed by Amazon.
    Quantities are read-only from Amazon FBA API, no push operations allowed.
    Supports MCF (Multi-Channel Fulfillment) shipping for orders from other channels.
    """
    __tablename__ = 'amazon_fba_listings'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=False)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    
    # Amazon identifiers
    seller_sku = db.Column(db.String(100), nullable=False)
    asin = db.Column(db.String(20), nullable=False)
    fnsku = db.Column(db.String(40), nullable=False)
    
    # Listing details
    title = db.Column(db.String(500))
    condition = db.Column(db.String(50), default='New')
    price = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(3), default='GBP')
    
    # FBA inventory quantities (Amazon-controlled, read-only from API)
    fba_available_quantity = db.Column(db.Integer, default=0)
    fba_reserved_quantity = db.Column(db.Integer, default=0)
    fba_inbound_quantity = db.Column(db.Integer, default=0)
    fba_inbound_working_quantity = db.Column(db.Integer, default=0)
    fba_inbound_shipped_quantity = db.Column(db.Integer, default=0)
    fba_inbound_receiving_quantity = db.Column(db.Integer, default=0)
    fba_unfulfillable_quantity = db.Column(db.Integer, default=0)
    fba_researching_quantity = db.Column(db.Integer, default=0)
    
    # FBA fees and size tier
    fba_size_tier = db.Column(db.String(50))
    fba_fulfillment_fee = db.Column(db.Float)
    fba_monthly_storage_fee = db.Column(db.Float)
    amazon_referral_fee_percentage = db.Column(db.Float, default=15.0)
    
    # MCF (Multi-Channel Fulfillment) settings
    mcf_enabled = db.Column(db.Boolean, default=True)
    mcf_shipping_speed = db.Column(db.String(50), default='Standard')
    mcf_multi_unit_discount = db.Column(db.Boolean, default=True)
    
    # Fulfillment center tracking
    fulfillment_center_id = db.Column(db.String(20))
    last_replenishment_date = db.Column(db.DateTime)
    days_of_supply = db.Column(db.Integer)
    
    # Sync tracking
    is_active = db.Column(db.Boolean, default=True)
    last_sync_at = db.Column(db.DateTime)
    last_sync_status = db.Column(db.String(50), default='pending')
    last_sync_error = db.Column(db.Text)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stock: 'WarehouseStock'
        store: 'Store'
    else:
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('fba_listings', lazy='dynamic'))
        store = db.relationship('Store', backref=db.backref('fba_listings', lazy='dynamic'))
    
    __table_args__ = (
        Index('idx_fba_store_sku', 'store_id', 'seller_sku', unique=True),
        Index('idx_fba_asin', 'asin'),
        Index('idx_fba_fnsku', 'fnsku'),
        Index('idx_fba_warehouse_stock', 'warehouse_stock_id'),
    )
    
    @property
    def total_fba_quantity(self):
        """Total sellable FBA quantity (available + reserved + inbound)"""
        return (self.fba_available_quantity or 0) + (self.fba_reserved_quantity or 0)
    
    @property
    def fulfillment_channel(self):
        """Return 'FBA' for compatibility"""
        return 'FBA'
    
    def __repr__(self):
        return f'<AmazonFBAListing {self.seller_sku} ASIN={self.asin}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_stock_id': self.warehouse_stock_id,
            'store_id': self.store_id,
            'sku': self.seller_sku,
            'asin': self.asin,
            'fnsku': self.fnsku,
            'title': self.title,
            'condition': self.condition,
            'price': self.price,
            'currency': self.currency,
            'fulfillment_channel': 'FBA',
            'fba_available_quantity': self.fba_available_quantity,
            'fba_reserved_quantity': self.fba_reserved_quantity,
            'fba_inbound_quantity': self.fba_inbound_quantity,
            'fba_unfulfillable_quantity': self.fba_unfulfillable_quantity,
            'total_fba_quantity': self.total_fba_quantity,
            'fba_size_tier': self.fba_size_tier,
            'fba_fulfillment_fee': self.fba_fulfillment_fee,
            'mcf_enabled': self.mcf_enabled,
            'mcf_shipping_speed': self.mcf_shipping_speed,
            'is_active': self.is_active,
            'last_sync_at': self.last_sync_at.isoformat() if self.last_sync_at else None,
            'last_sync_status': self.last_sync_status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AmazonFBMListing(db.Model):
    """
    Amazon FBM (Fulfilled by Merchant) listings - inventory controlled by warehouse.
    Quantities are pushed FROM warehouse stock TO Amazon.
    Seller handles shipping directly.
    """
    __tablename__ = 'amazon_fbm_listings'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=False)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    
    # Amazon identifiers
    seller_sku = db.Column(db.String(100), nullable=False)
    asin = db.Column(db.String(20), nullable=False)
    
    # Listing details
    title = db.Column(db.String(500))
    condition = db.Column(db.String(50), default='New')
    price = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(3), default='GBP')
    
    # FBM shipping settings
    handling_time_days = db.Column(db.Integer, default=1)
    shipping_template_id = db.Column(db.String(100))
    shipping_template_name = db.Column(db.String(200))
    
    # Quantity sync settings (warehouse → Amazon)
    sync_quantity = db.Column(db.Boolean, default=True)
    quantity_buffer = db.Column(db.Integer, default=0)
    max_quantity_limit = db.Column(db.Integer)
    
    # Fees
    amazon_referral_fee_percentage = db.Column(db.Float, default=15.0)
    vat_rate = db.Column(db.Float, default=20.0)
    
    # Push status tracking
    push_state = db.Column(db.String(50), default='active')
    consecutive_failures = db.Column(db.Integer, default=0)
    last_push_at = db.Column(db.DateTime)
    last_push_quantity = db.Column(db.Integer)
    last_push_status = db.Column(db.String(50), default='pending')
    last_push_error = db.Column(db.Text)
    push_attempts = db.Column(db.Integer, default=0)
    
    # Marketplace sync tracking
    last_marketplace_qty = db.Column(db.Integer)
    last_synced_at = db.Column(db.DateTime)
    
    # Status
    is_active = db.Column(db.Boolean, default=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stock: 'WarehouseStock'
        store: 'Store'
    else:
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('fbm_listings', lazy='dynamic'))
        store = db.relationship('Store', backref=db.backref('fbm_listings', lazy='dynamic'))
    
    __table_args__ = (
        Index('idx_fbm_store_sku', 'store_id', 'seller_sku', unique=True),
        Index('idx_fbm_asin', 'asin'),
        Index('idx_fbm_warehouse_stock', 'warehouse_stock_id'),
        Index('idx_fbm_push_status', 'last_push_status', 'is_active'),
    )
    
    @property
    def effective_quantity(self):
        """Calculate the quantity that should be pushed to Amazon"""
        if not self.warehouse_stock or not self.sync_quantity:
            return 0
        
        base_quantity = self.warehouse_stock.sellable_quantity - (self.quantity_buffer or 0)
        base_quantity = max(0, base_quantity)
        
        if self.max_quantity_limit:
            base_quantity = min(base_quantity, self.max_quantity_limit)
        
        return base_quantity
    
    @property
    def fulfillment_channel(self):
        """Return 'FBM' for compatibility"""
        return 'FBM'
    
    @property
    def is_pushable(self):
        """Whether this listing can be pushed (not blocked)"""
        return self.push_state in ['active'] and self.consecutive_failures < 5
    
    @property
    def needs_push(self):
        """Whether this listing needs to be pushed to Amazon"""
        if not self.is_active or not self.sync_quantity or not self.is_pushable:
            return False
        
        if self.last_push_quantity != self.effective_quantity:
            return True
        
        if self.last_push_status == 'error' and self.push_attempts > 0:
            return True
        
        if not self.last_push_at:
            return True
        
        return False
    
    def __repr__(self):
        return f'<AmazonFBMListing {self.seller_sku} ASIN={self.asin}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_stock_id': self.warehouse_stock_id,
            'store_id': self.store_id,
            'sku': self.seller_sku,
            'asin': self.asin,
            'title': self.title,
            'condition': self.condition,
            'price': self.price,
            'currency': self.currency,
            'fulfillment_channel': 'FBM',
            'handling_time_days': self.handling_time_days,
            'shipping_template_name': self.shipping_template_name,
            'sync_quantity': self.sync_quantity,
            'quantity_buffer': self.quantity_buffer,
            'max_quantity_limit': self.max_quantity_limit,
            'effective_quantity': self.effective_quantity,
            'is_pushable': self.is_pushable,
            'needs_push': self.needs_push,
            'push_state': self.push_state,
            'consecutive_failures': self.consecutive_failures,
            'last_push_at': self.last_push_at.isoformat() if self.last_push_at else None,
            'last_push_quantity': self.last_push_quantity,
            'last_push_status': self.last_push_status,
            'last_push_error': self.last_push_error,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class AmazonFBAInventory(db.Model):
    """
    Standalone Amazon FBA inventory - READ-ONLY data synced from Amazon.
    This table stores FBA inventory independently of warehouse stock.
    FBA inventory is controlled by Amazon - no push operations allowed.
    
    This model does NOT require a warehouse_stock_id because:
    - FBA items may not exist in our warehouse at all
    - FBA inventory comes directly from Amazon FBA API
    - This is the source of truth for what's in Amazon fulfillment centers
    """
    __tablename__ = 'amazon_fba_inventory'
    
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='CASCADE'), nullable=False)
    
    # Amazon identifiers
    seller_sku = db.Column(db.String(100), nullable=False)
    asin = db.Column(db.String(20))
    fnsku = db.Column(db.String(40))
    
    # Product details
    title = db.Column(db.String(500))
    condition = db.Column(db.String(50), default='New')
    
    # FBA inventory quantities (Amazon-controlled, read-only from API)
    available_quantity = db.Column(db.Integer, default=0)  # Sellable inventory
    reserved_quantity = db.Column(db.Integer, default=0)   # Reserved for orders
    inbound_quantity = db.Column(db.Integer, default=0)    # Total inbound
    inbound_working = db.Column(db.Integer, default=0)     # Being prepped
    inbound_shipped = db.Column(db.Integer, default=0)     # In transit
    inbound_receiving = db.Column(db.Integer, default=0)   # At FC, being received
    unfulfillable_quantity = db.Column(db.Integer, default=0)  # Damaged/unsellable
    researching_quantity = db.Column(db.Integer, default=0)    # Being researched
    
    # FBA fees (synced from Amazon)
    fba_size_tier = db.Column(db.String(50))
    fba_fulfillment_fee = db.Column(db.Float)
    fba_storage_fee = db.Column(db.Float)
    referral_fee_percentage = db.Column(db.Float, default=15.0)
    
    # Price from Amazon (informational)
    price = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(3), default='GBP')
    
    # MCF settings (for Multi-Channel Fulfillment)
    mcf_enabled = db.Column(db.Boolean, default=True)
    
    # Fulfillment center info
    fulfillment_center_id = db.Column(db.String(50))
    days_of_supply = db.Column(db.Integer)
    
    # Optional link to warehouse stock (for matching/reporting only, not required)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='SET NULL'), nullable=True)
    
    # Sync tracking
    is_active = db.Column(db.Boolean, default=True)
    last_synced_at = db.Column(db.DateTime)
    last_sync_status = db.Column(db.String(50), default='pending')
    last_sync_error = db.Column(db.Text)
    
    # Orphan/archive tracking (for warehouse master delete)
    is_orphaned = db.Column(db.Boolean, default=False)  # Warehouse SKU deleted but FBA has stock
    is_archived = db.Column(db.Boolean, default=False)  # Hidden from default view (FBA qty was 0)
    orphaned_at = db.Column(db.DateTime)  # When orphaned
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        store: 'Store'
        warehouse_stock: Optional['WarehouseStock']
    else:
        store = db.relationship('Store', backref=db.backref('fba_inventory', lazy='dynamic'))
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('fba_inventory_items', lazy='dynamic'))
    
    __table_args__ = (
        Index('idx_fba_inv_store_sku', 'store_id', 'seller_sku', unique=True),
        Index('idx_fba_inv_asin', 'asin'),
        Index('idx_fba_inv_fnsku', 'fnsku'),
    )
    
    @property
    def total_quantity(self):
        """Total FBA quantity (available + reserved)"""
        return (self.available_quantity or 0) + (self.reserved_quantity or 0)
    
    @property
    def total_inbound(self):
        """Total inbound quantity from all stages"""
        return (self.inbound_working or 0) + (self.inbound_shipped or 0) + (self.inbound_receiving or 0)
    
    def __repr__(self):
        return f'<AmazonFBAInventory {self.seller_sku} available={self.available_quantity}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'store_id': self.store_id,
            'seller_sku': self.seller_sku,
            'asin': self.asin,
            'fnsku': self.fnsku,
            'title': self.title,
            'condition': self.condition,
            'available_quantity': self.available_quantity or 0,
            'reserved_quantity': self.reserved_quantity or 0,
            'inbound_quantity': self.total_inbound,
            'unfulfillable_quantity': self.unfulfillable_quantity or 0,
            'total_quantity': self.total_quantity,
            'fba_size_tier': self.fba_size_tier,
            'fba_fulfillment_fee': self.fba_fulfillment_fee,
            'price': self.price,
            'currency': self.currency,
            'mcf_enabled': self.mcf_enabled,
            'is_active': self.is_active,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'last_sync_status': self.last_sync_status,
            'warehouse_stock_id': self.warehouse_stock_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class MarketplaceOrder(db.Model):
    """
    Tracks marketplace orders for idempotent processing and stock reservation.
    Prevents double-counting sales from the same marketplace order.
    Supports both FBA (MCF) and FBM fulfillment paths.
    """
    __tablename__ = 'marketplace_orders'
    
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='SET NULL'), nullable=True)  # Nullable to preserve orders when stores are deleted
    
    # Marketplace order identification
    marketplace_order_id = db.Column(db.String(200), nullable=False)  # External order ID from marketplace
    marketplace_order_item_id = db.Column(db.String(200))  # External item ID (for multi-item orders)
    
    # SKU and quantity
    sku = db.Column(db.String(100), nullable=False)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='SET NULL'), nullable=True)
    quantity = db.Column(db.Integer, nullable=False)
    
    # Fulfillment type - FBA (MCF) or FBM (warehouse)
    fulfillment_type = db.Column(db.String(10), default='FBM')  # FBA or FBM
    mcf_order_id = db.Column(db.Integer, db.ForeignKey('mcf_orders.id', ondelete='SET NULL'), nullable=True)
    
    # Pricing and fees
    unit_price = db.Column(db.Float, default=0.0)
    line_total = db.Column(db.Float, default=0.0)
    platform_fee = db.Column(db.Float, default=0.0)
    shipping_charged = db.Column(db.Float, default=0.0)
    shipping_cost = db.Column(db.Float, default=0.0)
    
    # Profit tracking
    product_cost = db.Column(db.Float, default=0.0)
    gross_profit = db.Column(db.Float, default=0.0)
    profit_margin_percent = db.Column(db.Float, default=0.0)
    
    # Shipping details
    ship_to_name = db.Column(db.String(200))
    ship_to_address = db.Column(db.String(500))
    ship_to_city = db.Column(db.String(100))
    ship_to_postcode = db.Column(db.String(20))
    ship_to_country = db.Column(db.String(2), default='GB')
    
    # Tracking
    carrier = db.Column(db.String(50))
    tracking_number = db.Column(db.String(100))
    shipped_at = db.Column(db.DateTime)
    
    # Processing status
    status = db.Column(db.String(50), nullable=False, default='pending')  # pending, processed, failed, cancelled
    processed_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    
    # Idempotency - prevents duplicate processing
    idempotency_key = db.Column(db.String(500), unique=True, nullable=False)  # Composite key: store_id:order_id:item_id:sku
    
    # Stock ledger tracking
    ledger_entry_id = db.Column(db.Integer, db.ForeignKey('stock_ledger_entries.id', ondelete='SET NULL'), nullable=True)
    
    # Audit
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        store: 'Store'
        warehouse_stock: Optional['WarehouseStock']
        ledger_entry: Optional['StockLedgerEntry']
        mcf_order: Optional['MCFOrder']
    else:
        store = db.relationship('Store', backref=db.backref('marketplace_orders', lazy=True))
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('marketplace_orders', lazy=True))
        ledger_entry = db.relationship('StockLedgerEntry', backref=db.backref('marketplace_order', lazy=True, uselist=False))
        mcf_order = db.relationship('MCFOrder', backref=db.backref('marketplace_orders', lazy='dynamic'))
    
    def calculate_profit(self):
        """Calculate profit metrics for this order"""
        total_revenue = (self.line_total or 0) + (self.shipping_charged or 0)
        total_costs = (
            (self.product_cost or 0) +
            (self.platform_fee or 0) +
            (self.shipping_cost or 0)
        )
        self.gross_profit = total_revenue - total_costs
        if total_revenue > 0:
            self.profit_margin_percent = (self.gross_profit / total_revenue) * 100
    
    def __repr__(self):
        store_name = self.store.name if self.store else 'Unknown'
        return f'<MarketplaceOrder {store_name}:{self.marketplace_order_id} {self.sku} x{self.quantity}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'store_id': self.store_id,
            'store_name': self.store.name if self.store else None,
            'marketplace_order_id': self.marketplace_order_id,
            'sku': self.sku,
            'quantity': self.quantity,
            'fulfillment_type': self.fulfillment_type,
            'unit_price': self.unit_price,
            'line_total': self.line_total,
            'platform_fee': self.platform_fee,
            'shipping_charged': self.shipping_charged,
            'shipping_cost': self.shipping_cost,
            'product_cost': self.product_cost,
            'gross_profit': self.gross_profit,
            'profit_margin_percent': self.profit_margin_percent,
            'ship_to_name': self.ship_to_name,
            'ship_to_city': self.ship_to_city,
            'ship_to_country': self.ship_to_country,
            'carrier': self.carrier,
            'tracking_number': self.tracking_number,
            'shipped_at': self.shipped_at.isoformat() if self.shipped_at else None,
            'status': self.status,
            'processed_at': self.processed_at.isoformat() if self.processed_at else None,
            'error_message': self.error_message,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
    
    @staticmethod
    def generate_idempotency_key(store_id: int, order_id: str, sku: str, item_id: Optional[str] = None) -> str:
        """Generate unique idempotency key for order processing"""
        parts = [str(store_id), order_id, sku]
        if item_id:
            parts.append(item_id)
        return ':'.join(parts)
    
    @classmethod
    def is_already_processed(cls, store_id: int, order_id: str, sku: str, item_id: Optional[str] = None) -> bool:
        """Check if this order has already been processed (prevents double-counting)"""
        key = cls.generate_idempotency_key(store_id, order_id, sku, item_id)
        existing = cls.query.filter_by(idempotency_key=key).first()
        return existing is not None and existing.status == 'processed'

class InventoryImportStaging(db.Model):
    """
    Staging table for manual inventory imports from Amazon Seller Central exports.
    Used when API access is unavailable (OAuth issues, etc.)
    Data is reviewed before being applied to warehouse stock.
    """
    __tablename__ = 'inventory_import_staging'
    
    id = db.Column(db.Integer, primary_key=True)
    import_batch_id = db.Column(db.String(100), nullable=False)  # Groups items from same import
    source = db.Column(db.String(50), nullable=False, default='amazon')  # amazon, ebay, etc.
    
    # Parsed data from export
    sku = db.Column(db.String(100), nullable=False)
    asin = db.Column(db.String(50))
    product_name = db.Column(db.String(500))
    fulfillment_type = db.Column(db.String(20))  # FBA, FBM
    marketplace_quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Numeric(10, 2))
    currency = db.Column(db.String(10), default='GBP')
    listing_status = db.Column(db.String(50))  # Active, Inactive, etc.
    
    # Comparison with warehouse
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='SET NULL'), nullable=True)
    warehouse_quantity = db.Column(db.Integer)  # Current warehouse qty at import time
    quantity_difference = db.Column(db.Integer)  # marketplace_qty - warehouse_qty
    
    # Review status
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected, applied
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    reviewed_at = db.Column(db.DateTime)
    applied_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    
    # Audit
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stock: Optional['WarehouseStock']
    else:
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('import_staging_entries', lazy=True))
    
    def __repr__(self):
        return f'<InventoryImportStaging {self.sku} {self.marketplace_quantity} vs WH:{self.warehouse_quantity}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'import_batch_id': self.import_batch_id,
            'source': self.source,
            'sku': self.sku,
            'asin': self.asin,
            'product_name': self.product_name,
            'fulfillment_type': self.fulfillment_type,
            'marketplace_quantity': self.marketplace_quantity,
            'price': float(self.price) if self.price else None,
            'currency': self.currency,
            'listing_status': self.listing_status,
            'warehouse_stock_id': self.warehouse_stock_id,
            'warehouse_quantity': self.warehouse_quantity,
            'quantity_difference': self.quantity_difference,
            'status': self.status,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'applied_at': self.applied_at.isoformat() if self.applied_at else None,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ========================================
# SECTION 4: MASTER CARTON SYSTEM
# ========================================

class ProductPackMapping(db.Model):
    """Map single items to master cartons for scanning"""
    __tablename__ = 'product_pack_mappings'
    
    id = db.Column(db.Integer, primary_key=True)
    single_sku = db.Column(db.String(100), nullable=False, index=True)
    single_barcode = db.Column(db.String(100), index=True)
    master_barcode = db.Column(db.String(100), index=True)
    units_per_carton = db.Column(db.Integer, default=1)
    carton_weight_kg = db.Column(db.Float)
    carton_length_cm = db.Column(db.Float)
    carton_width_cm = db.Column(db.Float)
    carton_height_cm = db.Column(db.Float)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<ProductPackMapping {self.single_sku} x{self.units_per_carton}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'single_sku': self.single_sku,
            'single_barcode': self.single_barcode,
            'master_barcode': self.master_barcode,
            'units_per_carton': self.units_per_carton,
            'carton_weight_kg': self.carton_weight_kg,
            'carton_dimensions': f'{self.carton_length_cm}x{self.carton_width_cm}x{self.carton_height_cm}' if self.carton_length_cm else None,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class ProductMapping(db.Model):
    """Map warehouse SKUs to marketplace listings.
    
    This is the explicit linking table for Product Linking Stage 1.
    Links warehouse_stock to external platform identifiers (ASIN, eBay Item ID, etc.)
    """
    __tablename__ = 'product_mappings'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=False, index=True)
    platform = db.Column(db.String(20), nullable=False)  # 'amazon_fbm', 'amazon_fba', 'ebay'
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='SET NULL'), nullable=True, index=True)
    external_id = db.Column(db.String(200))  # ASIN, eBay Item ID, etc.
    seller_sku = db.Column(db.String(200))  # Marketplace seller SKU
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stock: 'WarehouseStock'
        store: 'Store'
    else:
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('product_mappings', lazy='dynamic'))
        store = db.relationship('Store', backref=db.backref('product_mappings', lazy='dynamic'))
    
    # Unique constraint: one mapping per warehouse+platform+store
    __table_args__ = (
        Index('idx_product_mapping_unique', 'warehouse_stock_id', 'platform', 'store_id', unique=True),
    )
    
    def __repr__(self):
        return f'<ProductMapping ws={self.warehouse_stock_id} -> {self.platform}:{self.external_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_stock_id': self.warehouse_stock_id,
            'platform': self.platform,
            'store_id': self.store_id,
            'external_id': self.external_id,
            'seller_sku': self.seller_sku,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class MasterProductGroup(db.Model):
    """Master product group - represents a single physical product with all its marketplace listings.
    
    Every physical product forms one master group containing:
    - Master SKU (Warehouse) - the source of truth for stock
    - Linked marketplace listings (eBay, Amazon FBM, Amazon FBA read-only)
    """
    __tablename__ = 'master_product_groups'
    
    id = db.Column(db.Integer, primary_key=True)
    display_title = db.Column(db.String(500))  # Title for UI display
    display_image_url = db.Column(db.String(1000))  # Main product image
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        warehouse_stocks: List['WarehouseStock']
        marketplace_listings: List['MarketplaceListing']
        master_template: Optional['MasterListingTemplate']
        channel_overrides: List['ChannelOverride']
    else:
        warehouse_stocks = db.relationship('WarehouseStock', back_populates='master_group', lazy='dynamic')
        marketplace_listings = db.relationship('MarketplaceListing', back_populates='master_group', lazy='dynamic')
        master_template = db.relationship('MasterListingTemplate', back_populates='product_group', uselist=False, lazy='joined')
        channel_overrides = db.relationship('ChannelOverride', back_populates='product_group', lazy='dynamic')
    
    def __repr__(self):
        return f'<MasterProductGroup {self.id}: {self.display_title[:50] if self.display_title else "Untitled"}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'display_title': self.display_title,
            'display_image_url': self.display_image_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }
    
    def get_primary_warehouse_stock(self):
        """Get the primary warehouse stock for this group"""
        return self.warehouse_stocks.first()
    
    def get_linked_count(self):
        """Get count of linked marketplace listings"""
        return self.marketplace_listings.count()


class MasterListingTemplate(db.Model):
    """Master listing template - standardized product data for publishing to multiple channels.
    
    Stores the master listing data that can be used to:
    - Display product information consistently
    - Publish to additional marketplaces
    - Keep listing data synchronized
    """
    __tablename__ = 'master_listing_templates'
    
    id = db.Column(db.Integer, primary_key=True)
    product_group_id = db.Column(db.Integer, db.ForeignKey('master_product_groups.id', ondelete='CASCADE'), nullable=False, unique=True)
    
    # Master listing data
    title = db.Column(db.String(500))
    description = db.Column(db.Text)
    brand = db.Column(db.String(200))
    condition = db.Column(db.String(50))  # New, Used, Refurbished
    category = db.Column(db.String(500))  # Category path
    
    # Media
    images = db.Column(JSON)  # List of image URLs
    
    # Attributes/Item Specifics
    attributes = db.Column(JSON)  # Key-value pairs for product attributes
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    if TYPE_CHECKING:
        product_group: 'MasterProductGroup'
    else:
        product_group = db.relationship('MasterProductGroup', back_populates='master_template')
    
    def __repr__(self):
        return f'<MasterListingTemplate group={self.product_group_id}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'product_group_id': self.product_group_id,
            'title': self.title,
            'description': self.description,
            'brand': self.brand,
            'condition': self.condition,
            'category': self.category,
            'images': self.images,
            'attributes': self.attributes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class ChannelOverride(db.Model):
    """Channel-specific overrides for a product group.
    
    Stores platform/store-specific settings like:
    - Price (different pricing per channel)
    - Handling time
    - Shipping template
    """
    __tablename__ = 'channel_overrides'
    
    id = db.Column(db.Integer, primary_key=True)
    product_group_id = db.Column(db.Integer, db.ForeignKey('master_product_groups.id', ondelete='CASCADE'), nullable=False, index=True)
    platform = db.Column(db.String(50), nullable=False)  # amazon_fbm, amazon_fba, ebay
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='SET NULL'), nullable=True, index=True)
    
    # Overrides
    price = db.Column(db.Float)
    title_override = db.Column(db.String(500))
    handling_time = db.Column(db.Integer)  # Days
    shipping_template = db.Column(db.String(200))
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        product_group: 'MasterProductGroup'
        store: Optional['Store']
    else:
        product_group = db.relationship('MasterProductGroup', back_populates='channel_overrides')
        store = db.relationship('Store', backref=db.backref('channel_overrides', lazy='dynamic'))
    
    # Unique constraint: one override per group+platform+store
    __table_args__ = (
        Index('idx_channel_override_unique', 'product_group_id', 'platform', 'store_id', unique=True),
    )
    
    def __repr__(self):
        return f'<ChannelOverride group={self.product_group_id} {self.platform}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'product_group_id': self.product_group_id,
            'platform': self.platform,
            'store_id': self.store_id,
            'store_name': self.store.name if self.store else None,
            'price': self.price,
            'title_override': self.title_override,
            'handling_time': self.handling_time,
            'shipping_template': self.shipping_template,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class StockMovement(db.Model):
    """Track all stock movements for audit trail"""
    __tablename__ = 'stock_movements'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=False)
    sku = db.Column(db.String(100), nullable=False, index=True)
    movement_type = db.Column(db.String(50), nullable=False, index=True)
    quantity_change = db.Column(db.Integer, nullable=False)
    quantity_before = db.Column(db.Integer, nullable=False)
    quantity_after = db.Column(db.Integer, nullable=False)
    reference_type = db.Column(db.String(50))
    reference_id = db.Column(db.Integer)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    if TYPE_CHECKING:
        warehouse_stock: 'WarehouseStock'
    else:
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('movements', lazy='dynamic'))
    
    def __repr__(self):
        return f'<StockMovement {self.sku} {self.quantity_change:+d}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'sku': self.sku,
            'movement_type': self.movement_type,
            'quantity_change': self.quantity_change,
            'quantity_before': self.quantity_before,
            'quantity_after': self.quantity_after,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# ========== SECTION 5: WAREHOUSE LAYOUT ENGINE ==========

class WarehouseZone(db.Model):
    """Warehouse zones (e.g., Zone A, B, C or Picking, Bulk, Receiving)"""
    __tablename__ = 'warehouse_zones'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='CASCADE'), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(100))
    zone_type = db.Column(db.String(50), default='storage')
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('warehouse_id', 'code', name='uq_warehouse_zone'),
    )
    
    if TYPE_CHECKING:
        warehouse: 'Warehouse'
        aisles: List['WarehouseAisle']
    else:
        warehouse = db.relationship('Warehouse', backref=db.backref('zones', lazy='dynamic'))
        aisles = db.relationship('WarehouseAisle', back_populates='zone', lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_id': self.warehouse_id,
            'code': self.code,
            'name': self.name,
            'zone_type': self.zone_type,
            'is_active': self.is_active
        }


class WarehouseAisle(db.Model):
    """Warehouse aisles within zones"""
    __tablename__ = 'warehouse_aisles'
    
    id = db.Column(db.Integer, primary_key=True)
    zone_id = db.Column(db.Integer, db.ForeignKey('warehouse_zones.id', ondelete='CASCADE'), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(100))
    aisle_type = db.Column(db.String(50), default='standard')
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('zone_id', 'code', name='uq_zone_aisle'),
    )
    
    if TYPE_CHECKING:
        zone: 'WarehouseZone'
        bays: List['WarehouseBay']
    else:
        zone = db.relationship('WarehouseZone', back_populates='aisles')
        bays = db.relationship('WarehouseBay', back_populates='aisle', lazy='dynamic', cascade='all, delete-orphan')
    
    @property
    def full_code(self):
        return f"{self.zone.code}-{self.code}" if self.zone else self.code
    
    def to_dict(self):
        return {
            'id': self.id,
            'zone_id': self.zone_id,
            'code': self.code,
            'full_code': self.full_code,
            'name': self.name,
            'is_active': self.is_active
        }


class WarehouseBay(db.Model):
    """Warehouse bays within aisles"""
    __tablename__ = 'warehouse_bays'
    
    id = db.Column(db.Integer, primary_key=True)
    aisle_id = db.Column(db.Integer, db.ForeignKey('warehouse_aisles.id', ondelete='CASCADE'), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(100))
    bay_type = db.Column(db.String(50), default='standard')
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('aisle_id', 'code', name='uq_aisle_bay'),
    )
    
    if TYPE_CHECKING:
        aisle: 'WarehouseAisle'
        shelves: List['WarehouseShelf']
    else:
        aisle = db.relationship('WarehouseAisle', back_populates='bays')
        shelves = db.relationship('WarehouseShelf', back_populates='bay', lazy='dynamic', cascade='all, delete-orphan')
    
    @property
    def full_code(self):
        return f"{self.aisle.full_code}-{self.code}" if self.aisle else self.code
    
    def to_dict(self):
        return {
            'id': self.id,
            'aisle_id': self.aisle_id,
            'code': self.code,
            'full_code': self.full_code,
            'name': self.name,
            'is_active': self.is_active
        }


class WarehouseShelf(db.Model):
    """Warehouse shelves within bays"""
    __tablename__ = 'warehouse_shelves'
    
    id = db.Column(db.Integer, primary_key=True)
    bay_id = db.Column(db.Integer, db.ForeignKey('warehouse_bays.id', ondelete='CASCADE'), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(100))
    shelf_type = db.Column(db.String(50), default='standard')
    width_cm = db.Column(db.Float)
    depth_cm = db.Column(db.Float)
    max_weight_kg = db.Column(db.Float)
    max_height_cm = db.Column(db.Float)
    current_weight_kg = db.Column(db.Float, default=0)
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('bay_id', 'code', name='uq_bay_shelf'),
    )
    
    if TYPE_CHECKING:
        bay: 'WarehouseBay'
        bins: List['WarehouseBin']
    else:
        bay = db.relationship('WarehouseBay', back_populates='shelves')
        bins = db.relationship('WarehouseBin', back_populates='shelf', lazy='dynamic', cascade='all, delete-orphan')
    
    @property
    def full_code(self):
        return f"{self.bay.full_code}-{self.code}" if self.bay else self.code
    
    @property
    def area_cm2(self):
        if self.width_cm and self.depth_cm:
            return self.width_cm * self.depth_cm
        return None
    
    @property
    def remaining_weight_capacity_kg(self):
        if self.max_weight_kg:
            return max(0, self.max_weight_kg - (self.current_weight_kg or 0))
        return None
    
    def to_dict(self):
        return {
            'id': self.id,
            'bay_id': self.bay_id,
            'code': self.code,
            'full_code': self.full_code,
            'name': self.name,
            'width_cm': self.width_cm,
            'depth_cm': self.depth_cm,
            'max_weight_kg': self.max_weight_kg,
            'max_height_cm': self.max_height_cm,
            'current_weight_kg': self.current_weight_kg,
            'remaining_weight_capacity_kg': self.remaining_weight_capacity_kg,
            'area_cm2': self.area_cm2,
            'is_active': self.is_active
        }


class WarehouseBin(db.Model):
    """Warehouse bins/slots within shelves - the lowest level storage location"""
    __tablename__ = 'warehouse_bins'
    
    id = db.Column(db.Integer, primary_key=True)
    shelf_id = db.Column(db.Integer, db.ForeignKey('warehouse_shelves.id', ondelete='CASCADE'), nullable=False)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(100))
    bin_type = db.Column(db.String(50), default='standard')
    width_cm = db.Column(db.Float)
    depth_cm = db.Column(db.Float)
    height_cm = db.Column(db.Float)
    max_weight_kg = db.Column(db.Float)
    is_active = db.Column(db.Boolean, default=True)
    is_occupied = db.Column(db.Boolean, default=False)
    current_sku = db.Column(db.String(100))
    sort_order = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('shelf_id', 'code', name='uq_shelf_bin'),
    )
    
    if TYPE_CHECKING:
        shelf: 'WarehouseShelf'
    else:
        shelf = db.relationship('WarehouseShelf', back_populates='bins')
    
    @property
    def full_code(self):
        return f"{self.shelf.full_code}-{self.code}" if self.shelf else self.code
    
    @property
    def volume_cm3(self):
        if self.width_cm and self.depth_cm and self.height_cm:
            return self.width_cm * self.depth_cm * self.height_cm
        return None
    
    def to_dict(self):
        return {
            'id': self.id,
            'shelf_id': self.shelf_id,
            'code': self.code,
            'full_code': self.full_code,
            'name': self.name,
            'dimensions': f'{self.width_cm}x{self.depth_cm}x{self.height_cm}' if self.width_cm else None,
            'volume_cm3': self.volume_cm3,
            'max_weight_kg': self.max_weight_kg,
            'is_active': self.is_active,
            'is_occupied': self.is_occupied,
            'current_sku': self.current_sku
        }


class BoxType(db.Model):
    """Standard box/container types for shelf capacity planning"""
    __tablename__ = 'box_types'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    width_cm = db.Column(db.Float, nullable=False)
    depth_cm = db.Column(db.Float, nullable=False)
    height_cm = db.Column(db.Float, nullable=False)
    max_weight_kg = db.Column(db.Float)
    color = db.Column(db.String(20), default='#6c757d')
    is_active = db.Column(db.Boolean, default=True)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    @property
    def volume_cm3(self):
        return self.width_cm * self.depth_cm * self.height_cm
    
    @property
    def footprint_cm2(self):
        return self.width_cm * self.depth_cm
    
    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'width_cm': self.width_cm,
            'depth_cm': self.depth_cm,
            'height_cm': self.height_cm,
            'dimensions': f'{self.width_cm}x{self.depth_cm}x{self.height_cm}',
            'volume_cm3': self.volume_cm3,
            'footprint_cm2': self.footprint_cm2,
            'max_weight_kg': self.max_weight_kg,
            'color': self.color,
            'is_active': self.is_active
        }
    
    def fits_on_shelf(self, shelf):
        """Check if this box fits on a given shelf"""
        if not shelf.width_cm or not shelf.depth_cm:
            return True
        if not shelf.max_height_cm or self.height_cm <= shelf.max_height_cm:
            if self.width_cm <= shelf.width_cm and self.depth_cm <= shelf.depth_cm:
                return True
            if self.depth_cm <= shelf.width_cm and self.width_cm <= shelf.depth_cm:
                return True
        return False


class ShelfAllocation(db.Model):
    """Track box/item allocations to shelf locations"""
    __tablename__ = 'shelf_allocations'
    
    id = db.Column(db.Integer, primary_key=True)
    shelf_id = db.Column(db.Integer, db.ForeignKey('warehouse_shelves.id', ondelete='CASCADE'), nullable=False)
    bin_id = db.Column(db.Integer, db.ForeignKey('warehouse_bins.id', ondelete='SET NULL'), nullable=True)
    box_type_id = db.Column(db.Integer, db.ForeignKey('box_types.id', ondelete='SET NULL'), nullable=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='SET NULL'), nullable=True)
    
    position_x = db.Column(db.Float, default=0)
    position_y = db.Column(db.Float, default=0)
    width_cm = db.Column(db.Float)
    depth_cm = db.Column(db.Float)
    height_cm = db.Column(db.Float)
    weight_kg = db.Column(db.Float)
    quantity = db.Column(db.Integer, default=1)
    
    sku = db.Column(db.String(100))
    label = db.Column(db.String(255))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    shelf = db.relationship('WarehouseShelf', backref=db.backref('allocations', lazy='dynamic'))
    box_type = db.relationship('BoxType', backref=db.backref('allocations', lazy='dynamic'))
    warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('shelf_allocations', lazy='dynamic'))
    
    @property
    def footprint_cm2(self):
        if self.width_cm and self.depth_cm:
            return self.width_cm * self.depth_cm
        return None
    
    def to_dict(self):
        return {
            'id': self.id,
            'shelf_id': self.shelf_id,
            'bin_id': self.bin_id,
            'box_type_id': self.box_type_id,
            'box_type': self.box_type.to_dict() if self.box_type else None,
            'warehouse_stock_id': self.warehouse_stock_id,
            'position': {'x': self.position_x, 'y': self.position_y},
            'width_cm': self.width_cm,
            'depth_cm': self.depth_cm,
            'height_cm': self.height_cm,
            'weight_kg': self.weight_kg,
            'quantity': self.quantity,
            'sku': self.sku,
            'label': self.label,
            'is_active': self.is_active
        }


# ========== SECTION 7: ORDER SYSTEM ==========

class Customer(db.Model):
    """Customer for sales orders"""
    __tablename__ = 'customers'
    
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True)
    name = db.Column(db.String(255), nullable=False)
    company_name = db.Column(db.String(255))
    email = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    customer_type = db.Column(db.String(20), default='retail')
    
    billing_address_line1 = db.Column(db.String(255))
    billing_address_line2 = db.Column(db.String(255))
    billing_city = db.Column(db.String(100))
    billing_state = db.Column(db.String(100))
    billing_postcode = db.Column(db.String(20))
    billing_country = db.Column(db.String(100), default='United Kingdom')
    
    shipping_address_line1 = db.Column(db.String(255))
    shipping_address_line2 = db.Column(db.String(255))
    shipping_city = db.Column(db.String(100))
    shipping_state = db.Column(db.String(100))
    shipping_postcode = db.Column(db.String(20))
    shipping_country = db.Column(db.String(100), default='United Kingdom')
    
    vat_number = db.Column(db.String(50))
    payment_terms = db.Column(db.String(50), default='net_30')
    credit_limit = db.Column(db.Float, default=0)
    current_balance = db.Column(db.Float, default=0)
    
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'company_name': self.company_name,
            'email': self.email,
            'phone': self.phone,
            'customer_type': self.customer_type,
            'billing_address': f"{self.billing_address_line1}, {self.billing_city}, {self.billing_postcode}",
            'is_active': self.is_active
        }


class SalesOrder(db.Model):
    """Sales order from customers"""
    __tablename__ = 'sales_orders'
    
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), unique=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='SET NULL'), nullable=True)
    
    status = db.Column(db.String(20), default='draft')
    order_date = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.DateTime)
    ship_date = db.Column(db.DateTime)
    
    shipping_address_line1 = db.Column(db.String(255))
    shipping_address_line2 = db.Column(db.String(255))
    shipping_city = db.Column(db.String(100))
    shipping_state = db.Column(db.String(100))
    shipping_postcode = db.Column(db.String(20))
    shipping_country = db.Column(db.String(100), default='United Kingdom')
    
    subtotal = db.Column(db.Float, default=0)
    tax_rate = db.Column(db.Float, default=20)
    tax_amount = db.Column(db.Float, default=0)
    shipping_cost = db.Column(db.Float, default=0)
    discount_amount = db.Column(db.Float, default=0)
    total = db.Column(db.Float, default=0)
    
    payment_status = db.Column(db.String(20), default='unpaid')
    payment_method = db.Column(db.String(50))
    payment_reference = db.Column(db.String(255))
    paid_at = db.Column(db.DateTime)
    
    notes = db.Column(db.Text)
    internal_notes = db.Column(db.Text)
    created_by_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    customer = db.relationship('Customer', backref=db.backref('orders', lazy='dynamic'))
    items = db.relationship('SalesOrderItem', back_populates='order', lazy='dynamic', cascade='all, delete-orphan')
    
    @property
    def item_count(self):
        return self.items.count()
    
    def calculate_totals(self):
        self.subtotal = sum(item.line_total for item in self.items.all())
        self.tax_amount = self.subtotal * (self.tax_rate / 100)
        self.total = self.subtotal + self.tax_amount + (self.shipping_cost or 0) - (self.discount_amount or 0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'order_number': self.order_number,
            'customer_id': self.customer_id,
            'customer_name': self.customer.name if self.customer else None,
            'status': self.status,
            'order_date': self.order_date.isoformat() if self.order_date else None,
            'subtotal': self.subtotal,
            'tax_amount': self.tax_amount,
            'total': self.total,
            'payment_status': self.payment_status,
            'item_count': self.item_count
        }


class SalesOrderItem(db.Model):
    """Line items for sales orders"""
    __tablename__ = 'sales_order_items'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('sales_orders.id', ondelete='CASCADE'), nullable=False)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='SET NULL'), nullable=True)
    
    sku = db.Column(db.String(100))
    description = db.Column(db.String(500))
    quantity = db.Column(db.Integer, default=1)
    unit_price = db.Column(db.Float, default=0)
    discount_percent = db.Column(db.Float, default=0)
    line_total = db.Column(db.Float, default=0)
    
    fulfilled_quantity = db.Column(db.Integer, default=0)
    reserved_quantity = db.Column(db.Integer, default=0)
    is_fulfilled = db.Column(db.Boolean, default=False)
    
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    order = db.relationship('SalesOrder', back_populates='items')
    warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('order_items', lazy='dynamic'))
    
    def calculate_line_total(self):
        base = self.quantity * self.unit_price
        discount = base * (self.discount_percent / 100)
        self.line_total = base - discount
    
    def to_dict(self):
        return {
            'id': self.id,
            'order_id': self.order_id,
            'sku': self.sku,
            'description': self.description,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'discount_percent': self.discount_percent,
            'line_total': self.line_total,
            'fulfilled_quantity': self.fulfilled_quantity,
            'is_fulfilled': self.is_fulfilled
        }


class Invoice(db.Model):
    """Invoices generated from sales orders"""
    __tablename__ = 'invoices'
    
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(50), unique=True, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('sales_orders.id', ondelete='SET NULL'), nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='SET NULL'), nullable=True)
    
    status = db.Column(db.String(20), default='draft')
    issue_date = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.DateTime)
    
    billing_name = db.Column(db.String(255))
    billing_address_line1 = db.Column(db.String(255))
    billing_address_line2 = db.Column(db.String(255))
    billing_city = db.Column(db.String(100))
    billing_postcode = db.Column(db.String(20))
    billing_country = db.Column(db.String(100))
    
    subtotal = db.Column(db.Float, default=0)
    tax_rate = db.Column(db.Float, default=20)
    tax_amount = db.Column(db.Float, default=0)
    total = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    balance_due = db.Column(db.Float, default=0)
    
    payment_method = db.Column(db.String(50))
    payment_reference = db.Column(db.String(255))
    paid_at = db.Column(db.DateTime)
    
    notes = db.Column(db.Text)
    pdf_path = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    order = db.relationship('SalesOrder', backref=db.backref('invoices', lazy='dynamic'))
    customer = db.relationship('Customer', backref=db.backref('invoices', lazy='dynamic'))
    
    def calculate_balance(self):
        self.balance_due = self.total - (self.amount_paid or 0)
        if self.balance_due <= 0:
            self.status = 'paid'
    
    def to_dict(self):
        return {
            'id': self.id,
            'invoice_number': self.invoice_number,
            'order_id': self.order_id,
            'customer_id': self.customer_id,
            'customer_name': self.customer.name if self.customer else self.billing_name,
            'status': self.status,
            'issue_date': self.issue_date.isoformat() if self.issue_date else None,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'subtotal': self.subtotal,
            'tax_amount': self.tax_amount,
            'total': self.total,
            'amount_paid': self.amount_paid,
            'balance_due': self.balance_due
        }


# ========================================
# SECTION: MCF (MULTI-CHANNEL FULFILLMENT) ORDERS
# Phase 2: FBA as Multi-Channel Fulfillment Engine
# ========================================

class MCFOrder(db.Model):
    """
    Multi-Channel Fulfillment (MCF) orders - orders fulfilled by Amazon FBA for non-Amazon channels.
    When an order comes from eBay/Etsy/TikTok/Website and the product is FBA-linked,
    we create an MCF order to have Amazon ship it directly to the customer.
    """
    __tablename__ = 'mcf_orders'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Link to source order (from any channel)
    source_order_id = db.Column(db.String(200), nullable=False)
    source_channel = db.Column(db.String(50), nullable=False)
    source_store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='SET NULL'), nullable=True)
    
    # Amazon MCF identifiers
    amazon_order_id = db.Column(db.String(100))
    displayable_order_id = db.Column(db.String(50))
    seller_fulfillment_order_id = db.Column(db.String(50), unique=True, nullable=False)
    
    # Amazon FBA store used for fulfillment
    fba_store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='SET NULL'), nullable=True)
    
    # Shipping details
    destination_name = db.Column(db.String(200))
    destination_address_line1 = db.Column(db.String(255))
    destination_address_line2 = db.Column(db.String(255))
    destination_city = db.Column(db.String(100))
    destination_state = db.Column(db.String(100))
    destination_postcode = db.Column(db.String(20))
    destination_country = db.Column(db.String(2), default='GB')
    destination_phone = db.Column(db.String(50))
    
    # MCF shipping speed
    shipping_speed = db.Column(db.String(50), default='Standard')
    displayable_comment = db.Column(db.String(500))
    
    # Status tracking
    status = db.Column(db.String(50), default='pending')
    amazon_status = db.Column(db.String(50))
    amazon_status_updated_at = db.Column(db.DateTime)
    
    # Shipment tracking
    carrier = db.Column(db.String(50))
    tracking_number = db.Column(db.String(100))
    ship_date = db.Column(db.DateTime)
    estimated_arrival_date = db.Column(db.DateTime)
    actual_arrival_date = db.Column(db.DateTime)
    
    # MCF fees and costs
    mcf_fulfillment_fee = db.Column(db.Float, default=0.0)
    mcf_per_unit_fee = db.Column(db.Float, default=0.0)
    mcf_per_shipment_fee = db.Column(db.Float, default=0.0)
    mcf_weight_handling_fee = db.Column(db.Float, default=0.0)
    total_mcf_fee = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(3), default='GBP')
    
    # Order totals (from source order)
    order_subtotal = db.Column(db.Float, default=0.0)
    order_shipping_charged = db.Column(db.Float, default=0.0)
    order_total = db.Column(db.Float, default=0.0)
    
    # Profit calculation
    product_cost = db.Column(db.Float, default=0.0)
    platform_fees = db.Column(db.Float, default=0.0)
    gross_profit = db.Column(db.Float, default=0.0)
    profit_margin_percent = db.Column(db.Float, default=0.0)
    
    # Error tracking
    last_error = db.Column(db.Text)
    retry_count = db.Column(db.Integer, default=0)
    
    # Audit
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    
    # Relationships
    if TYPE_CHECKING:
        source_store: Optional['Store']
        fba_store: Optional['Store']
        items: List['MCFOrderItem']
    else:
        source_store = db.relationship('Store', foreign_keys=[source_store_id], backref=db.backref('mcf_orders_sourced', lazy='dynamic'))
        fba_store = db.relationship('Store', foreign_keys=[fba_store_id], backref=db.backref('mcf_orders_fulfilled', lazy='dynamic'))
        items = db.relationship('MCFOrderItem', back_populates='mcf_order', lazy='dynamic', cascade='all, delete-orphan')
    
    __table_args__ = (
        Index('idx_mcf_order_source', 'source_channel', 'source_order_id'),
        Index('idx_mcf_order_amazon', 'amazon_order_id'),
        Index('idx_mcf_order_status', 'status'),
        Index('idx_mcf_order_created', 'created_at'),
    )
    
    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())
    
    def calculate_totals(self):
        """Calculate MCF fees and profit for the order"""
        items = self.items.all()
        
        self.product_cost = sum((item.product_cost or 0) * item.quantity for item in items)
        self.total_mcf_fee = (
            (self.mcf_per_shipment_fee or 0) + 
            sum((item.mcf_fulfillment_fee or 0) * item.quantity for item in items)
        )
        
        total_revenue = (self.order_total or 0)
        total_costs = self.product_cost + self.total_mcf_fee + (self.platform_fees or 0)
        
        self.gross_profit = total_revenue - total_costs
        if total_revenue > 0:
            self.profit_margin_percent = (self.gross_profit / total_revenue) * 100
    
    def __repr__(self):
        return f'<MCFOrder {self.seller_fulfillment_order_id} from {self.source_channel}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'source_order_id': self.source_order_id,
            'source_channel': self.source_channel,
            'seller_fulfillment_order_id': self.seller_fulfillment_order_id,
            'amazon_order_id': self.amazon_order_id,
            'status': self.status,
            'amazon_status': self.amazon_status,
            'shipping_speed': self.shipping_speed,
            'destination_name': self.destination_name,
            'destination_city': self.destination_city,
            'destination_country': self.destination_country,
            'carrier': self.carrier,
            'tracking_number': self.tracking_number,
            'ship_date': self.ship_date.isoformat() if self.ship_date else None,
            'estimated_arrival_date': self.estimated_arrival_date.isoformat() if self.estimated_arrival_date else None,
            'total_items': self.total_items,
            'order_total': self.order_total,
            'total_mcf_fee': self.total_mcf_fee,
            'product_cost': self.product_cost,
            'platform_fees': self.platform_fees,
            'gross_profit': self.gross_profit,
            'profit_margin_percent': self.profit_margin_percent,
            'last_error': self.last_error,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class MCFOrderItem(db.Model):
    """
    Individual items within an MCF order.
    Maps source SKU to FBA SKU with quantity and fee tracking.
    """
    __tablename__ = 'mcf_order_items'
    
    id = db.Column(db.Integer, primary_key=True)
    mcf_order_id = db.Column(db.Integer, db.ForeignKey('mcf_orders.id', ondelete='CASCADE'), nullable=False)
    
    # Source SKU (from eBay/Etsy/etc)
    source_sku = db.Column(db.String(100), nullable=False)
    
    # FBA listing link
    fba_listing_id = db.Column(db.Integer, db.ForeignKey('amazon_fba_listings.id', ondelete='SET NULL'), nullable=True)
    fba_sku = db.Column(db.String(100), nullable=False)
    asin = db.Column(db.String(20))
    fnsku = db.Column(db.String(40))
    
    # Quantities
    quantity = db.Column(db.Integer, nullable=False, default=1)
    
    # Per-unit values
    unit_price = db.Column(db.Float, default=0.0)
    product_cost = db.Column(db.Float, default=0.0)
    
    # MCF fees per unit (Amazon's MCF pricing: 1st unit + each additional)
    mcf_fulfillment_fee = db.Column(db.Float, default=0.0)
    mcf_first_unit_fee = db.Column(db.Float, default=0.0)
    mcf_additional_unit_fee = db.Column(db.Float, default=0.0)
    
    # Fulfillment status
    status = db.Column(db.String(50), default='pending')
    fulfilled_quantity = db.Column(db.Integer, default=0)
    cancelled_quantity = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    if TYPE_CHECKING:
        mcf_order: 'MCFOrder'
        fba_listing: Optional['AmazonFBAListing']
    else:
        mcf_order = db.relationship('MCFOrder', back_populates='items')
        fba_listing = db.relationship('AmazonFBAListing', backref=db.backref('mcf_order_items', lazy='dynamic'))
    
    __table_args__ = (
        Index('idx_mcf_item_order', 'mcf_order_id'),
        Index('idx_mcf_item_fba_listing', 'fba_listing_id'),
    )
    
    @property
    def line_total(self):
        return (self.unit_price or 0) * self.quantity
    
    @property
    def total_mcf_fee(self):
        """Calculate total MCF fee using first-unit + additional pricing"""
        if self.quantity <= 0:
            return 0
        
        first_unit = self.mcf_first_unit_fee or self.mcf_fulfillment_fee or 0
        additional = self.mcf_additional_unit_fee or (first_unit * 0.5)
        
        if self.quantity == 1:
            return first_unit
        else:
            return first_unit + (additional * (self.quantity - 1))
    
    def __repr__(self):
        return f'<MCFOrderItem {self.fba_sku} x{self.quantity}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'mcf_order_id': self.mcf_order_id,
            'source_sku': self.source_sku,
            'fba_sku': self.fba_sku,
            'asin': self.asin,
            'fnsku': self.fnsku,
            'quantity': self.quantity,
            'unit_price': self.unit_price,
            'product_cost': self.product_cost,
            'mcf_fulfillment_fee': self.mcf_fulfillment_fee,
            'mcf_first_unit_fee': self.mcf_first_unit_fee,
            'mcf_additional_unit_fee': self.mcf_additional_unit_fee,
            'total_mcf_fee': self.total_mcf_fee,
            'line_total': self.line_total,
            'status': self.status,
            'fulfilled_quantity': self.fulfilled_quantity,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class OrderFees(db.Model):
    """
    Detailed fee breakdown for orders - supports both FBA (MCF) and FBM fulfillment.
    Provides complete visibility into all fees for profit calculation.
    """
    __tablename__ = 'order_fees'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Order links (one or the other)
    sales_order_id = db.Column(db.Integer, db.ForeignKey('sales_orders.id', ondelete='CASCADE'), nullable=True)
    marketplace_order_id = db.Column(db.Integer, db.ForeignKey('marketplace_orders.id', ondelete='CASCADE'), nullable=True)
    mcf_order_id = db.Column(db.Integer, db.ForeignKey('mcf_orders.id', ondelete='CASCADE'), nullable=True)
    
    # Fulfillment type
    fulfillment_type = db.Column(db.String(10), nullable=False)
    
    # Product costs
    product_cost = db.Column(db.Float, default=0.0)
    landed_cost = db.Column(db.Float, default=0.0)
    
    # Platform fees
    platform_name = db.Column(db.String(50))
    platform_sale_fee = db.Column(db.Float, default=0.0)
    platform_fee_percentage = db.Column(db.Float, default=0.0)
    
    # Amazon-specific fees
    amazon_referral_fee = db.Column(db.Float, default=0.0)
    amazon_referral_fee_percentage = db.Column(db.Float, default=15.0)
    
    # FBA/MCF fees (when fulfillment_type = 'FBA')
    fba_fulfillment_fee = db.Column(db.Float, default=0.0)
    mcf_shipping_fee = db.Column(db.Float, default=0.0)
    mcf_per_unit_fee = db.Column(db.Float, default=0.0)
    
    # FBM fees (when fulfillment_type = 'FBM')
    shipping_label_cost = db.Column(db.Float, default=0.0)
    packaging_cost = db.Column(db.Float, default=0.0)
    
    # VAT
    vat_rate = db.Column(db.Float, default=20.0)
    vat_amount = db.Column(db.Float, default=0.0)
    
    # Totals
    total_fees = db.Column(db.Float, default=0.0)
    total_costs = db.Column(db.Float, default=0.0)
    
    # Revenue
    sale_price = db.Column(db.Float, default=0.0)
    shipping_charged = db.Column(db.Float, default=0.0)
    total_revenue = db.Column(db.Float, default=0.0)
    
    # Profit
    gross_profit = db.Column(db.Float, default=0.0)
    profit_margin_percent = db.Column(db.Float, default=0.0)
    
    # Margin tracking
    target_margin = db.Column(db.Float, default=15.0)
    margin_alert_threshold = db.Column(db.Float, default=10.0)
    below_target_margin = db.Column(db.Boolean, default=False)
    margin_alert_confirmed = db.Column(db.Boolean, default=False)
    margin_alert_confirmed_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    margin_alert_confirmed_at = db.Column(db.DateTime)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_order_fees_sales', 'sales_order_id'),
        Index('idx_order_fees_marketplace', 'marketplace_order_id'),
        Index('idx_order_fees_mcf', 'mcf_order_id'),
        Index('idx_order_fees_margin_alert', 'below_target_margin', 'margin_alert_confirmed'),
    )
    
    def calculate_totals(self):
        """Calculate all totals and profit metrics"""
        self.total_fees = (
            (self.platform_sale_fee or 0) +
            (self.amazon_referral_fee or 0) +
            (self.fba_fulfillment_fee or 0) +
            (self.mcf_shipping_fee or 0) +
            (self.mcf_per_unit_fee or 0) +
            (self.shipping_label_cost or 0) +
            (self.packaging_cost or 0) +
            (self.vat_amount or 0)
        )
        
        self.total_costs = (
            (self.product_cost or 0) +
            (self.landed_cost or 0) +
            self.total_fees
        )
        
        self.total_revenue = (self.sale_price or 0) + (self.shipping_charged or 0)
        self.gross_profit = self.total_revenue - self.total_costs
        
        if self.total_revenue > 0:
            self.profit_margin_percent = (self.gross_profit / self.total_revenue) * 100
        else:
            self.profit_margin_percent = 0
        
        self.below_target_margin = self.profit_margin_percent < (self.target_margin or 15.0)
    
    def to_dict(self):
        return {
            'id': self.id,
            'fulfillment_type': self.fulfillment_type,
            'product_cost': self.product_cost,
            'landed_cost': self.landed_cost,
            'platform_name': self.platform_name,
            'platform_sale_fee': self.platform_sale_fee,
            'amazon_referral_fee': self.amazon_referral_fee,
            'fba_fulfillment_fee': self.fba_fulfillment_fee,
            'mcf_shipping_fee': self.mcf_shipping_fee,
            'shipping_label_cost': self.shipping_label_cost,
            'vat_amount': self.vat_amount,
            'total_fees': self.total_fees,
            'total_costs': self.total_costs,
            'sale_price': self.sale_price,
            'shipping_charged': self.shipping_charged,
            'total_revenue': self.total_revenue,
            'gross_profit': self.gross_profit,
            'profit_margin_percent': self.profit_margin_percent,
            'target_margin': self.target_margin,
            'below_target_margin': self.below_target_margin,
            'margin_alert_confirmed': self.margin_alert_confirmed
        }


class ListingMarginConfig(db.Model):
    """
    Per-listing margin configuration and alerts.
    Allows sellers to set custom margin thresholds per listing/channel.
    """
    __tablename__ = 'listing_margin_configs'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Listing links (can link to any listing type)
    marketplace_listing_id = db.Column(db.Integer, db.ForeignKey('marketplace_listings.id', ondelete='CASCADE'), nullable=True)
    fba_listing_id = db.Column(db.Integer, db.ForeignKey('amazon_fba_listings.id', ondelete='CASCADE'), nullable=True)
    fbm_listing_id = db.Column(db.Integer, db.ForeignKey('amazon_fbm_listings.id', ondelete='CASCADE'), nullable=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=True)
    
    # Margin settings
    target_margin = db.Column(db.Float, default=15.0)
    alert_threshold = db.Column(db.Float, default=10.0)
    minimum_margin = db.Column(db.Float, default=5.0)
    
    # Alert settings
    alerts_enabled = db.Column(db.Boolean, default=True)
    block_below_minimum = db.Column(db.Boolean, default=False)
    
    # Override from channel defaults
    override_channel_defaults = db.Column(db.Boolean, default=False)
    
    # Audit
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_margin_config_warehouse', 'warehouse_stock_id'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'target_margin': self.target_margin,
            'alert_threshold': self.alert_threshold,
            'minimum_margin': self.minimum_margin,
            'alerts_enabled': self.alerts_enabled,
            'block_below_minimum': self.block_below_minimum,
            'override_channel_defaults': self.override_channel_defaults
        }


# ============================================================================
# ADMIN REPORTING SYSTEM - Comprehensive Logging Models (December 2025)
# ============================================================================

class ConfigChangeLog(db.Model):
    """Track all configuration changes with before/after snapshots"""
    __tablename__ = 'config_change_log'
    
    id = db.Column(db.Integer, primary_key=True)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    actor_type = db.Column(db.String(20), nullable=False)  # 'user', 'agent', 'system'
    actor_id = db.Column(db.Integer, nullable=True)  # User ID if actor_type='user'
    entity_type = db.Column(db.String(50), nullable=False, index=True)  # 'store', 'push_settings', 'warehouse', etc.
    entity_id = db.Column(db.Integer, nullable=True)  # ID of the entity changed
    summary = db.Column(db.String(500), nullable=False)  # Short description
    before_json = db.Column(JSON, nullable=True)  # State before change
    after_json = db.Column(JSON, nullable=True)  # State after change
    
    __table_args__ = (
        Index('idx_config_change_entity', 'entity_type', 'entity_id'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'changed_at': self.changed_at.isoformat() if self.changed_at else None,
            'actor_type': self.actor_type,
            'actor_id': self.actor_id,
            'entity_type': self.entity_type,
            'entity_id': self.entity_id,
            'summary': self.summary,
            'before_json': self.before_json,
            'after_json': self.after_json
        }


class AgentRunLog(db.Model):
    """Track agent execution runs and their outcomes"""
    __tablename__ = 'agent_run_log'
    
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    agent_name = db.Column(db.String(100), nullable=False, index=True)  # 'replit_agent', 'sync_scheduler', etc.
    scope = db.Column(db.String(200), nullable=True)  # What the agent was working on
    status = db.Column(db.String(20), default='running')  # 'running', 'success', 'partial', 'failed'
    summary = db.Column(db.Text, nullable=True)
    details_json = db.Column(JSON, nullable=True)  # Files changed, paths touched, migrations, etc.
    
    def to_dict(self):
        return {
            'id': self.id,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'agent_name': self.agent_name,
            'scope': self.scope,
            'status': self.status,
            'summary': self.summary,
            'details_json': self.details_json
        }


class APIErrorLog(db.Model):
    """Track API errors from external services (Amazon, eBay)"""
    __tablename__ = 'api_error_log'
    
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='SET NULL'), nullable=True)
    provider = db.Column(db.String(50), nullable=False, index=True)  # 'amazon', 'ebay'
    endpoint = db.Column(db.String(200), nullable=True)  # API endpoint called
    http_status = db.Column(db.Integer, nullable=True)
    error_code = db.Column(db.String(100), nullable=True, index=True)  # Provider-specific error code
    message = db.Column(db.Text, nullable=True)
    raw_payload = db.Column(db.Text, nullable=True)  # Masked/truncated payload
    
    if TYPE_CHECKING:
        store: 'Store'
    else:
        store = db.relationship('Store', backref=db.backref('api_errors', lazy=True))
    
    def to_dict(self):
        return {
            'id': self.id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'store_id': self.store_id,
            'store_name': self.store.name if self.store else None,
            'provider': self.provider,
            'endpoint': self.endpoint,
            'http_status': self.http_status,
            'error_code': self.error_code,
            'message': self.message,
            'raw_payload': self.raw_payload
        }


class SyncJobLog(db.Model):
    """Enhanced sync job logging with detailed tracking"""
    __tablename__ = 'sync_job_log'
    
    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='SET NULL'), nullable=True)
    job_type = db.Column(db.String(50), nullable=False, index=True)  # 'fba_import', 'fbm_push', 'ebay_sync', 'auto_push'
    status = db.Column(db.String(20), default='started', index=True)  # 'started', 'completed', 'failed'
    items_imported = db.Column(db.Integer, default=0)
    items_pushed = db.Column(db.Integer, default=0)
    items_failed = db.Column(db.Integer, default=0)
    message = db.Column(db.Text, nullable=True)
    error_details = db.Column(JSON, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    
    if TYPE_CHECKING:
        store: 'Store'
    else:
        store = db.relationship('Store', backref=db.backref('job_logs', lazy=True))
    
    def to_dict(self):
        return {
            'id': self.id,
            'store_id': self.store_id,
            'store_name': self.store.name if self.store else None,
            'job_type': self.job_type,
            'status': self.status,
            'items_imported': self.items_imported,
            'items_pushed': self.items_pushed,
            'items_failed': self.items_failed,
            'message': self.message,
            'error_details': self.error_details,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'duration_seconds': self.duration_seconds
        }


class SystemEvent(db.Model):
    """
    Unified system event log table for comprehensive admin reporting.
    Captures ALL significant operational events across the platform.
    """
    __tablename__ = 'system_events'
    
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    actor = db.Column(db.String(50), nullable=False, index=True)  # 'user', 'admin', 'agent', 'system', 'scheduler'
    actor_id = db.Column(db.Integer, nullable=True)  # User ID if actor is 'user' or 'admin'
    category = db.Column(db.String(50), nullable=False, index=True)  # 'sync_job', 'api_error', 'config_change', 'agent_run', 'auth_failure', 'queue_event', 'store_update', 'fba_import', 'fbm_push'
    entity_id = db.Column(db.Integer, nullable=True)  # Store ID or Listing ID if applicable
    entity_type = db.Column(db.String(50), nullable=True)  # 'store', 'listing', 'warehouse', etc.
    description = db.Column(db.Text, nullable=False)  # Human-readable description
    details_json = db.Column(JSON, nullable=True)  # Full structured payload for debugging
    
    __table_args__ = (
        Index('idx_system_events_category_ts', 'category', 'timestamp'),
        Index('idx_system_events_actor_ts', 'actor', 'timestamp'),
        Index('idx_system_events_entity', 'entity_type', 'entity_id'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'actor': self.actor,
            'actor_id': self.actor_id,
            'category': self.category,
            'entity_id': self.entity_id,
            'entity_type': self.entity_type,
            'description': self.description,
            'details_json': self.details_json
        }


class StockTransfer(db.Model):
    """
    Stock transfer records for moving inventory between warehouse and FBA.
    Tracks transfers in both directions:
    - Warehouse → FBA (outbound to Amazon)
    - FBA → Warehouse (returns/removals from Amazon)
    """
    __tablename__ = 'stock_transfers'
    
    id = db.Column(db.Integer, primary_key=True)
    warehouse_stock_id = db.Column(db.Integer, db.ForeignKey('warehouse_stock.id', ondelete='CASCADE'), nullable=False)
    
    from_location = db.Column(db.String(50), nullable=False)  # 'warehouse' or 'fba'
    to_location = db.Column(db.String(50), nullable=False)  # 'warehouse' or 'fba'
    
    qty_planned = db.Column(db.Integer, nullable=False, default=0)  # Expected quantity
    qty_received = db.Column(db.Integer, nullable=False, default=0)  # Actual received quantity
    qty_sellable = db.Column(db.Integer, nullable=False, default=0)  # Received in sellable condition
    qty_damaged = db.Column(db.Integer, nullable=False, default=0)  # Received damaged/unsellable
    
    reason = db.Column(db.String(100), nullable=True)  # Listing Removed, Customer Return, Damaged, Defective, Other
    notes = db.Column(db.Text, nullable=True)  # Additional notes
    
    status = db.Column(db.String(30), default='Planned', nullable=False, index=True)
    # Status values: Planned, In Transit, Awaiting Arrival, Completed, Cancelled
    
    # OCR/Scan data for receiving
    scan_image_url = db.Column(db.String(500), nullable=True)  # Photo upload URL
    scanned_text = db.Column(db.Text, nullable=True)  # OCR extracted text (ASIN/FNSKU/UPC)
    
    # Audit fields
    created_by = db.Column(db.String(100), nullable=True)  # User who created the transfer
    received_by = db.Column(db.String(100), nullable=True)  # User who received the transfer
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    received_at = db.Column(db.DateTime, nullable=True)  # When items were received
    
    # Relationship
    if TYPE_CHECKING:
        warehouse_stock: 'WarehouseStock'
    else:
        warehouse_stock = db.relationship('WarehouseStock', backref=db.backref('stock_transfers', lazy='dynamic'))
    
    __table_args__ = (
        Index('idx_stock_transfer_status', 'status', 'created_at'),
        Index('idx_stock_transfer_warehouse_stock', 'warehouse_stock_id'),
        Index('idx_stock_transfer_direction', 'from_location', 'to_location'),
    )
    
    def __repr__(self):
        sku = self.warehouse_stock.sku if self.warehouse_stock else 'Unknown'
        return f'<StockTransfer {sku}: {self.from_location}→{self.to_location} ({self.status})>'
    
    @property
    def direction_display(self):
        """Human-readable transfer direction"""
        if self.from_location == 'warehouse' and self.to_location == 'fba':
            return 'Warehouse → FBA'
        elif self.from_location == 'fba' and self.to_location == 'warehouse':
            return 'FBA → Warehouse'
        return f'{self.from_location} → {self.to_location}'
    
    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_stock_id': self.warehouse_stock_id,
            'sku': self.warehouse_stock.sku if self.warehouse_stock else None,
            'product_name': self.warehouse_stock.product_name if self.warehouse_stock else None,
            'from_location': self.from_location,
            'to_location': self.to_location,
            'direction_display': self.direction_display,
            'qty_planned': self.qty_planned,
            'qty_received': self.qty_received,
            'qty_sellable': self.qty_sellable,
            'qty_damaged': self.qty_damaged,
            'reason': self.reason,
            'notes': self.notes,
            'status': self.status,
            'scan_image_url': self.scan_image_url,
            'scanned_text': self.scanned_text,
            'created_by': self.created_by,
            'received_by': self.received_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'received_at': self.received_at.isoformat() if self.received_at else None
        }


class SystemSetting(db.Model):
    """System-wide settings for the inventory management system"""
    __tablename__ = 'system_settings'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    value_type = db.Column(db.String(20), default='string')  # string, int, bool, json
    description = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @classmethod
    def get_value(cls, key, default=None):
        """Get a setting value by key"""
        setting = cls.query.filter_by(key=key).first()
        if setting is None:
            return default
        if setting.value_type == 'int':
            return int(setting.value) if setting.value else default
        if setting.value_type == 'bool':
            return setting.value.lower() in ('true', '1', 'yes') if setting.value else default
        if setting.value_type == 'json':
            import json
            return json.loads(setting.value) if setting.value else default
        return setting.value
    
    @classmethod
    def set_value(cls, key, value, description=None, value_type='string'):
        """Set a setting value by key"""
        setting = cls.query.filter_by(key=key).first()
        if setting is None:
            setting = cls(key=key, value_type=value_type)
            db.session.add(setting)
        if value_type == 'json':
            import json
            setting.value = json.dumps(value)
        else:
            setting.value = str(value)
        if description:
            setting.description = description
        db.session.commit()
        return setting

    def __repr__(self):
        return f'<SystemSetting {self.key}={self.value}>'


# Create indexes for better query performance
Index('idx_product_pack_mapping_single_sku', ProductPackMapping.single_sku)
Index('idx_product_pack_mapping_master_barcode', ProductPackMapping.master_barcode)
Index('idx_stock_movement_warehouse', StockMovement.warehouse_stock_id, StockMovement.created_at)
Index('idx_stock_movement_type', StockMovement.movement_type, StockMovement.created_at)
Index('idx_inventory_import_staging_batch', InventoryImportStaging.import_batch_id)
Index('idx_inventory_import_staging_sku', InventoryImportStaging.sku)
Index('idx_inventory_import_staging_status', InventoryImportStaging.status)
Index('idx_inventory_item_group_id', InventoryItem.group_id)
Index('idx_inventory_item_sku', InventoryItem.sku)
Index('idx_product_group_key', ProductGroup.group_key)
Index('idx_sync_log_store_created', SyncLog.store_id, SyncLog.created_at)
Index('idx_warehouse_stock_sku', WarehouseStock.sku)
Index('idx_warehouse_stock_reorder', WarehouseStock.reorder_point, WarehouseStock.available_quantity)
Index('idx_warehouse_stock_supplier', WarehouseStock.supplier_id)
Index('idx_warehouse_stock_active_qty', WarehouseStock.is_active, WarehouseStock.available_quantity)
Index('idx_stock_ledger_warehouse_created', StockLedgerEntry.warehouse_stock_id, StockLedgerEntry.created_at)
Index('idx_stock_ledger_batch', StockLedgerEntry.batch_id)
Index('idx_marketplace_listing_needs_push', MarketplaceListing.last_push_status, MarketplaceListing.is_active)
Index('idx_purchase_order_supplier', PurchaseOrder.supplier_id)
Index('idx_purchase_order_status', PurchaseOrder.status)
Index('idx_purchase_order_date', PurchaseOrder.order_date)
Index('idx_store_active', Store.is_active)
Index('idx_supplier_active', Supplier.is_active)
Index('idx_marketplace_order_idempotency', MarketplaceOrder.idempotency_key)
Index('idx_marketplace_order_store', MarketplaceOrder.store_id, MarketplaceOrder.created_at)
Index('idx_marketplace_order_sku', MarketplaceOrder.sku, MarketplaceOrder.status)
Index('idx_warehouse_stock_version', WarehouseStock.stock_version)
Index('idx_api_error_log_store', APIErrorLog.store_id, APIErrorLog.created_at)
Index('idx_sync_job_log_store', SyncJobLog.store_id, SyncJobLog.started_at)


# ==============================
# STEP A: Marketplace Template Registry
# ==============================

class MarketplaceTemplate(db.Model):
    """
    Registry of marketplace-specific import templates.
    Each template defines field mappings, transforms, and validation rules
    for normalizing external data (CSV/API) into canonical format.
    """
    __tablename__ = 'marketplace_templates'
    
    id = db.Column(db.Integer, primary_key=True)
    template_name = db.Column(db.String(100), unique=True, nullable=False)  # e.g., 'amazon_settlement_v1'
    platform = db.Column(db.String(50), nullable=False)  # amazon, ebay, shopify
    version = db.Column(db.String(20), nullable=False, default='1.0')
    description = db.Column(db.Text)
    
    required_fields = db.Column(db.JSON, default=list)
    optional_fields = db.Column(db.JSON, default=list)
    field_mappings = db.Column(db.JSON, default=dict)
    transforms = db.Column(db.JSON, default=dict)
    validation_rules = db.Column(db.JSON, default=dict)
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_template_platform', 'platform', 'is_active'),
    )
    
    def __repr__(self):
        return f'<MarketplaceTemplate {self.template_name} v{self.version}>'


class CanonicalOrderLine(db.Model):
    """
    Canonical order line records normalized from various marketplace sources.
    This is the single internal format for all marketplace transactions.
    Supports settlement imports, API imports, and reconciliation.
    """
    __tablename__ = 'canonical_order_lines'
    
    id = db.Column(db.Integer, primary_key=True)
    
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id', ondelete='SET NULL'), nullable=True)
    template_id = db.Column(db.Integer, db.ForeignKey('marketplace_templates.id', ondelete='SET NULL'), nullable=True)
    
    platform = db.Column(db.String(50), nullable=False)  # amazon, ebay
    template_name = db.Column(db.String(100))  # e.g., 'amazon_settlement_v1' (denormalized for audit)
    template_version = db.Column(db.String(20))
    
    external_order_id = db.Column(db.String(200), nullable=False)
    external_line_id = db.Column(db.String(200))
    settlement_id = db.Column(db.String(100))
    
    sku = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(500))
    quantity = db.Column(db.Integer, default=0)
    
    transaction_type = db.Column(db.String(50))
    fulfillment_channel = db.Column(db.String(20))
    marketplace = db.Column(db.String(50))
    
    gross_amount = db.Column(db.Float, default=0.0)
    product_sales = db.Column(db.Float, default=0.0)
    product_sales_tax = db.Column(db.Float, default=0.0)
    shipping_credit = db.Column(db.Float, default=0.0)
    shipping_credit_tax = db.Column(db.Float, default=0.0)
    gift_wrap_credit = db.Column(db.Float, default=0.0)
    gift_wrap_credit_tax = db.Column(db.Float, default=0.0)
    promotional_rebates = db.Column(db.Float, default=0.0)
    promotional_rebates_tax = db.Column(db.Float, default=0.0)
    marketplace_withheld_tax = db.Column(db.Float, default=0.0)
    
    selling_fees = db.Column(db.Float, default=0.0)
    fba_fees = db.Column(db.Float, default=0.0)
    other_transaction_fees = db.Column(db.Float, default=0.0)
    other_fees = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    
    fees_breakdown = db.Column(db.JSON, default=dict)
    
    order_city = db.Column(db.String(100))
    order_state = db.Column(db.String(100))
    order_postal = db.Column(db.String(20))
    tax_collection_model = db.Column(db.String(50))
    
    posted_at = db.Column(db.DateTime)
    
    source_file = db.Column(db.String(255))
    source_row = db.Column(db.Integer)
    raw_payload = db.Column(db.JSON)
    
    linked_marketplace_order_id = db.Column(db.Integer, db.ForeignKey('marketplace_orders.id', ondelete='SET NULL'), nullable=True)
    
    status = db.Column(db.String(20), default='imported')
    error_message = db.Column(db.Text)
    
    idempotency_key = db.Column(db.String(500), unique=True, nullable=False)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    store = db.relationship('Store', backref=db.backref('canonical_order_lines', lazy='dynamic'))
    template = db.relationship('MarketplaceTemplate', backref=db.backref('canonical_order_lines', lazy='dynamic'))
    
    __table_args__ = (
        Index('idx_canonical_order_store', 'store_id', 'created_at'),
        Index('idx_canonical_order_sku', 'sku', 'status'),
        Index('idx_canonical_order_settlement', 'settlement_id'),
        Index('idx_canonical_order_external', 'external_order_id'),
        Index('idx_canonical_order_idempotency', 'idempotency_key'),
    )
    
    @staticmethod
    def generate_idempotency_key(platform: str, settlement_id: str, order_id: str, sku: str, row_num: int = 0) -> str:
        """Generate unique key to prevent duplicate imports"""
        parts = [platform, settlement_id or '', order_id, sku, str(row_num)]
        return ':'.join(parts)
    
    def __repr__(self):
        return f'<CanonicalOrderLine {self.platform}:{self.external_order_id} {self.sku}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'platform': self.platform,
            'external_order_id': self.external_order_id,
            'sku': self.sku,
            'quantity': self.quantity,
            'transaction_type': self.transaction_type,
            'product_sales': self.product_sales,
            'selling_fees': self.selling_fees,
            'fba_fees': self.fba_fees,
            'total_amount': self.total_amount,
            'status': self.status,
            'posted_at': self.posted_at.isoformat() if self.posted_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
