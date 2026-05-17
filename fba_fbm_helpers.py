"""
FBA/FBM Helper Functions
Central place for all FBA/FBM classification logic.

NEW UNIFIED ARCHITECTURE (December 2025):
- ONE Amazon store per seller with fba_import_enabled and fbm_sync_enabled flags
- Per-listing fulfillment tracking via amazon_fulfillment_channel (AFN=FBA, MFN=FBM)
- FBA inventory is READ-ONLY (synced from Amazon into AmazonFBAInventory table)
- FBM inventory is WAREHOUSE-AUTHORITATIVE (push to Amazon)

STORE-LEVEL FLAGS:
- store.fba_import_enabled = True -> Import FBA inventory (read-only)
- store.fbm_sync_enabled = True -> Push FBM inventory to Amazon

LISTING-LEVEL CLASSIFICATION:
- listing.amazon_fulfillment_channel = 'AFN' -> FBA listing (read-only)
- listing.amazon_fulfillment_channel = 'MFN' -> FBM listing (pushable)
"""

def is_amazon_store(store):
    """Check if a store is any type of Amazon store"""
    if not store:
        return False
    platform = (store.platform or '').lower()
    return platform in ('amazon', 'amazonfba', 'amazonfbm', 'amazonlegacy')

def has_fba_enabled(store):
    """Check if store has FBA import enabled (new unified model)"""
    if not store:
        return False
    if not is_amazon_store(store):
        return False
    # Primary check: new flag
    if getattr(store, 'fba_import_enabled', False):
        return True
    # Fallback: old fulfillment_type check for backward compatibility
    if getattr(store, 'fulfillment_type', None) == 'FBA':
        return True
    if store.platform == 'AmazonFBA':
        return True
    return False

def has_fbm_enabled(store):
    """Check if store has FBM sync enabled (new unified model)
    
    SAFETY: Defaults to FALSE unless explicitly enabled.
    Legacy stores must be migrated to set fbm_sync_enabled=True.
    """
    if not store:
        return False
    if not is_amazon_store(store):
        return False
    # Primary check: new flag (MUST be explicitly True - no default)
    if getattr(store, 'fbm_sync_enabled', False) == True:
        return True
    # Fallback: old fulfillment_type check for backward compatibility
    if getattr(store, 'fulfillment_type', None) == 'FBM':
        return True
    if store.platform == 'AmazonFBM':
        return True
    # Legacy 'Amazon' stores with no flags set - check for explicit fbm_sync_enabled
    # Store 80 (BT38) should have fbm_sync_enabled=True set
    return False

# DEPRECATED: Keep for backward compatibility during migration
def is_fba_store(store):
    """DEPRECATED: Use has_fba_enabled() instead. 
    Check if a store is an Amazon FBA store (read-only inventory from Amazon)"""
    return has_fba_enabled(store)

def is_fbm_store(store):
    """DEPRECATED: Use has_fbm_enabled() instead.
    Check if a store is an Amazon FBM store (warehouse-authoritative)"""
    return has_fbm_enabled(store)

def is_legacy_amazon_store(store):
    """Check if a store is a legacy Amazon store (not configured with new flags)"""
    if not store:
        return False
    if not is_amazon_store(store):
        return False
    # Legacy if no new flags set and no old fulfillment_type
    has_new_flags = getattr(store, 'fba_import_enabled', None) is not None or \
                    getattr(store, 'fbm_sync_enabled', None) is not None
    has_old_type = getattr(store, 'fulfillment_type', None) in ('FBA', 'FBM')
    if has_new_flags or has_old_type:
        return False
    if store.platform in ('AmazonFBA', 'AmazonFBM'):
        return False
    return True

def get_amazon_stores_with_fba():
    """Get all Amazon stores with FBA import enabled"""
    from app import db
    from models import Store
    stores = db.session.query(Store).filter(
        Store.is_active == True,
        Store.platform.ilike('%amazon%'),
        db.or_(
            Store.fba_import_enabled == True,
            Store.fulfillment_type == 'FBA',
            Store.platform == 'AmazonFBA'
        )
    ).all()
    return stores

def get_amazon_stores_with_fbm():
    """Get all Amazon stores with FBM sync enabled"""
    from app import db
    from models import Store
    stores = db.session.query(Store).filter(
        Store.is_active == True,
        Store.platform.ilike('%amazon%'),
        db.or_(
            Store.fbm_sync_enabled == True,
            Store.fulfillment_type == 'FBM',
            Store.platform == 'AmazonFBM'
        )
    ).all()
    return stores

# DEPRECATED: Keep for backward compatibility
def get_fba_stores():
    """DEPRECATED: Use get_amazon_stores_with_fba() instead"""
    return get_amazon_stores_with_fba()

def get_fbm_stores():
    """DEPRECATED: Use get_amazon_stores_with_fbm() instead"""
    return get_amazon_stores_with_fbm()

def get_amazon_stores():
    """Get all Amazon stores (any configuration)"""
    from app import db
    from models import Store
    stores = db.session.query(Store).filter(
        Store.is_active == True,
        Store.platform.ilike('%amazon%')
    ).all()
    return stores

# LISTING-LEVEL CLASSIFICATION

def is_fba_listing(listing):
    """Check if a listing is fulfilled by Amazon (AFN)"""
    if not listing:
        return False
    return listing.amazon_fulfillment_channel == 'AFN'

def is_fbm_listing(listing):
    """Check if a listing is explicitly merchant fulfilled (MFN).

    Unknown/empty fulfillment is not FBM. Unknown fulfillment must fail closed
    until marketplace import classifies it explicitly.
    """
    if not listing:
        return False
    return (listing.amazon_fulfillment_channel or '').upper().strip() == 'MFN'

def can_push_to_store(store):
    """Check if we can push inventory to this store (FBM-enabled stores only)"""
    if not store:
        return False
    if not is_amazon_store(store):
        return True  # Non-Amazon stores can be pushed to
    # For Amazon stores, check if FBM sync is enabled
    return has_fbm_enabled(store)

def can_push_listing(listing, store=None):
    """Check if we can push this listing to marketplace.

    Amazon listings are pushable only when explicitly classified as FBM/MFN.
    Unknown fulfillment is non-pushable and must be reviewed/import-classified first.
    """
    if not listing:
        return False

    if store and is_amazon_store(store):
        if not has_fbm_enabled(store):
            return False
        return classify_fulfillment_channel(getattr(listing, 'amazon_fulfillment_channel', None)) == 'FBM'

    if is_fba_listing(listing):
        return False
    return True

def get_fulfillment_type_from_channel(channel):
    """Convert Amazon fulfillment channel to our fulfillment type.

    Unknown/empty channels return None so callers fail closed instead of
    silently treating unclassified inventory as FBM.
    """
    classified = classify_fulfillment_channel(channel)
    if classified == 'FBA':
        return 'FBA'
    if classified == 'FBM':
        return 'FBM'
    return None

def classify_fulfillment_channel(fulfillment_channel: str) -> str:
    """
    SINGLE SOURCE OF TRUTH for FBA vs FBM classification.
    
    Use THIS function everywhere you need to decide FBA vs FBM.
    Do NOT use store.platform for that decision.
    
    Args:
        fulfillment_channel: The fulfillment channel string from Amazon API
        
    Returns:
        'FBA' for Amazon-fulfilled items (read-only, NEVER push)
        'FBM' for EXPLICITLY merchant-fulfilled items (pushable)
        None for unknown/unclassified items (treat as NON-PUSHABLE for safety)
    """
    ch = (fulfillment_channel or "").upper().strip()
    
    # FBA channels - NEVER push to these
    if ch in ("AFN", "AMAZON_NA", "AMAZON_EU", "AMAZON_FE", "DEFAULT", "FBA"):
        return "FBA"
    
    # Explicit FBM channels - OK to push
    if ch in ("MFN", "FBM", "MERCHANT"):
        return "FBM"
    
    # Unknown/empty channels - return None (caller must decide what to do)
    # SAFETY: Do NOT default to FBM - could accidentally push to FBA listings
    return None


def marketplace_push_eligibility(store, *, sku=None, item=None, warehouse_stock=None, listing=None, payload=None, query_database=True):
    """Central marketplace push eligibility guard.

    This is the single authority for outbound marketplace inventory push
    decisions. It allows non-Amazon marketplaces, allows explicit Amazon
    MFN/FBM, and fail-closes Amazon FBA/AFN/read-only or unclassified
    inventory before queueing, dispatch, service calls, or feed creation.

    Returns:
        (allowed: bool, reason: str)
    """
    payload = payload or {}
    sku = (sku or payload.get('sku') or getattr(item, 'sku', None) or getattr(warehouse_stock, 'sku', None) or '').strip()

    if not store:
        return False, 'missing store'

    if not is_amazon_store(store):
        return True, 'non-Amazon marketplace allowed'

    platform = (getattr(store, 'platform', None) or '').strip().lower()
    fulfillment_type = (getattr(store, 'fulfillment_type', None) or '').strip().upper()

    if sku.upper().startswith('FBA-'):
        return False, f'Amazon push blocked: SKU {sku} uses FBA- prefix'

    if warehouse_stock is not None:
        location = (getattr(warehouse_stock, 'location', None) or '').upper()
        if 'FBA' in location:
            return False, f'Amazon push blocked: warehouse location is FBA for SKU {sku}'

    if platform in ('amazonfba', 'amazon_fba') or 'fba' in platform:
        return False, f'Amazon push blocked: store platform {getattr(store, "platform", None)} is read-only/FBA'

    if fulfillment_type == 'FBA' and not has_fbm_enabled(store):
        return False, 'Amazon push blocked: store fulfillment_type is FBA/read-only'

    if not has_fbm_enabled(store):
        return False, 'Amazon push blocked: FBM sync is not explicitly enabled for store'

    resolved_listing = listing
    resolved_warehouse_stock = warehouse_stock

    if query_database:
        from extensions import db
        from models import MarketplaceListing, WarehouseStock, AmazonFBAInventory, AmazonFBAListing

        if not sku and payload.get('item_id'):
            from models import InventoryItem
            resolved_item = db.session.get(InventoryItem, payload.get('item_id'))
            sku = (getattr(resolved_item, 'sku', None) or '').strip()
            if sku.upper().startswith('FBA-'):
                return False, f'Amazon push blocked: SKU {sku} uses FBA- prefix'

        if resolved_warehouse_stock is None and sku:
            resolved_warehouse_stock = WarehouseStock.query.filter_by(sku=sku).first()
            if resolved_warehouse_stock is not None:
                location = (getattr(resolved_warehouse_stock, 'location', None) or '').upper()
                if 'FBA' in location:
                    return False, f'Amazon push blocked: warehouse location is FBA for SKU {sku}'

        if resolved_listing is None:
            if payload.get('listing_id'):
                resolved_listing = db.session.get(MarketplaceListing, payload.get('listing_id'))
            elif sku:
                query = MarketplaceListing.query.filter_by(store_id=store.id)
                if resolved_warehouse_stock is not None:
                    query = query.filter(MarketplaceListing.warehouse_stock_id == resolved_warehouse_stock.id)
                else:
                    query = query.filter(MarketplaceListing.external_sku == sku)
                resolved_listing = query.first()

        if sku:
            fba_inventory_exists = AmazonFBAInventory.query.filter_by(store_id=store.id, seller_sku=sku).first() is not None
            fba_listing_exists = AmazonFBAListing.query.filter_by(store_id=store.id, seller_sku=sku).first() is not None
            marketplace_listing_exists = resolved_listing is not None
            if (fba_inventory_exists or fba_listing_exists) and not marketplace_listing_exists:
                return False, f'Amazon push blocked: SKU {sku} exists only in FBA read-only tables'

    if resolved_listing is None:
        return False, f'Amazon push blocked: SKU {sku or "unknown"} has no explicit MFN MarketplaceListing classification'

    channel = getattr(resolved_listing, 'amazon_fulfillment_channel', None)
    classified = classify_fulfillment_channel(channel)
    if classified == 'FBA':
        return False, f'Amazon push blocked: fulfillment channel {channel} is FBA/read-only for SKU {sku}'
    if classified is None:
        return False, f'Amazon push blocked: fulfillment channel is unknown/blank for SKU {sku}'

    return True, f'Amazon explicit {classified} push allowed for SKU {sku}'


def assert_marketplace_push_allowed(store, **kwargs):
    """Raise ValueError unless marketplace_push_eligibility allows the push."""
    allowed, reason = marketplace_push_eligibility(store, **kwargs)
    if not allowed:
        raise ValueError(reason)
    return True, reason


def get_store_type_label(store):
    """Get a human-readable label for store type"""
    if not store:
        return 'Unknown'
    if not is_amazon_store(store):
        return store.platform
    
    # Build label based on enabled features
    labels = []
    if has_fba_enabled(store):
        labels.append('FBA Import')
    if has_fbm_enabled(store):
        labels.append('FBM Sync')
    
    if labels:
        return f"Amazon ({', '.join(labels)})"
    return 'Amazon (Not Configured)'

def get_store_badges(store):
    """Get list of badge info dicts for store capabilities"""
    badges = []
    if not store:
        return badges
    
    if not is_amazon_store(store):
        badges.append({
            'text': store.platform,
            'class': 'badge-primary' if 'ebay' in (store.platform or '').lower() else 'badge-secondary'
        })
        return badges
    
    # Amazon store - show capability badges
    badges.append({'text': 'Amazon', 'class': 'badge-warning'})
    
    if has_fba_enabled(store):
        badges.append({'text': 'FBA (Read-Only)', 'class': 'badge-info'})
    
    if has_fbm_enabled(store):
        badges.append({'text': 'FBM (Push)', 'class': 'badge-success'})
    
    if not has_fba_enabled(store) and not has_fbm_enabled(store):
        badges.append({'text': 'Not Configured', 'class': 'badge-secondary'})
    
    return badges

def get_store_badge_class(store):
    """Get Bootstrap badge class for store type (legacy, use get_store_badges instead)"""
    if not store:
        return 'badge-secondary'
    if not is_amazon_store(store):
        if store.platform and 'ebay' in store.platform.lower():
            return 'badge-primary'
        return 'badge-secondary'
    # Amazon stores
    if has_fba_enabled(store) and has_fbm_enabled(store):
        return 'badge-warning'  # Dual-mode
    if has_fba_enabled(store):
        return 'badge-info'
    if has_fbm_enabled(store):
        return 'badge-success'
    return 'badge-secondary'


# ============================================================================
# AMAZON API CLIENT HELPERS - Single Source of Truth for Amazon API Access
# ============================================================================

def get_amazon_client_for_store(store_id: int, marketplace_region: str = 'UK'):
    """Fail closed: old Amazon client creation is disabled during shutdown proof."""
    from old_path_shutdown import disabled_response

    disabled = disabled_response(
        "get_amazon_client_for_store",
        store_id=store_id,
        marketplace_region=marketplace_region,
    )
    return (False, disabled["error"], None)

def test_amazon_connection_for_store(store_id: int):
    """
    Test Amazon connection for a specific store.
    
    Returns:
        Dict with 'success', 'message', and 'error' (if any)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    success, result, store = get_amazon_client_for_store(store_id)
    
    if success:
        return {
            'success': True,
            'message': f"Connection successful for '{store.name}'",
            'store_name': store.name,
            'store_id': store.id
        }
    else:
        # result contains the error message
        error_msg = result
        
        # Extract more specific error info
        if 'unauthorized_client' in error_msg.lower():
            error_msg = "Amazon API rejected credentials (unauthorized_client). Check LWA app roles in Seller Central."
        elif 'missing required roles' in error_msg.lower():
            error_msg = "Your Amazon app is missing required roles. Add 'Inventory and Order Tracking' and 'Feeds' roles in Seller Central."
        elif 'authentication failed' in error_msg.lower():
            error_msg = "Authentication failed. Verify your refresh token and LWA credentials are correct."
        
        return {
            'success': False,
            'error': error_msg,
            'store_name': store.name if store else None,
            'store_id': store_id
        }


def get_unified_amazon_store():
    """
    Get the single unified Amazon store (the main one with FBA/FBM flags).
    
    Returns the first active Amazon store with platform='Amazon'.
    In the unified model, there should only be one such store.
    
    Returns:
        Store object or None if no Amazon store found
    """
    from app import db
    from models import Store
    
    # Find the unified Amazon store (platform='Amazon', is_active=True)
    store = db.session.query(Store).filter(
        Store.platform == 'Amazon',
        Store.is_active == True
    ).first()
    
    return store
