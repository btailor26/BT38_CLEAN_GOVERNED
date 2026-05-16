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

    Unknown/empty Amazon fulfillment must NOT default to FBM.
    """
    if not listing:
        return False
    ch = (getattr(listing, "amazon_fulfillment_channel", None) or "").strip().upper()
    return ch == "MFN"

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

    Amazon listings must be explicit FBM/MFN. Unknown fulfillment fails closed.
    """
    if not listing:
        return False

    if store and is_amazon_store(store):
        if not has_fbm_enabled(store):
            return False
        channel_type = classify_fulfillment_channel(
            getattr(listing, "amazon_fulfillment_channel", None)
        )
        return channel_type == "FBM"

    if is_fba_listing(listing):
        return False

    return True

def get_fulfillment_type_from_channel(channel):
    """Convert Amazon fulfillment channel to BT38 fulfillment type.

    Returns:
        "FBA" for explicit Amazon fulfilled.
        "FBM" for explicit merchant fulfilled.
        None for unknown/empty values.
    """
    return classify_fulfillment_channel(channel)

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
    """
    SINGLE SOURCE OF TRUTH for Amazon API client creation.
    
    Use THIS function everywhere you need an Amazon API client.
    This ensures consistent credential loading and error handling.
    
    IMPORTANT: Checks auth_status before attempting connection.
    If store has auth_error status, returns failure immediately.
    
    Args:
        store_id: The ID of the Amazon store
        marketplace_region: The marketplace region (UK, US, DE, etc.)
        
    Returns:
        Tuple of (success: bool, client_or_error: AmazonAPIService|str, store: Store|None)
        - If success=True: (True, AmazonAPIService instance, Store instance)
        - If success=False: (False, error_message, store or None)
    """
    import logging
    from app import db
    from models import Store
    
    logger = logging.getLogger(__name__)
    
    try:
        # Get store from database
        store = db.session.get(Store, store_id)
        if not store:
            return (False, f"Store ID {store_id} not found", None)
        
        if not is_amazon_store(store):
            return (False, f"Store '{store.name}' is not an Amazon store (platform: {store.platform})", None)
        
        if not store.is_active:
            return (False, f"Store '{store.name}' is not active", None)
        
        # Check auth status - skip if store has auth error
        from amazon_auth import should_skip_amazon_sync
        should_skip, skip_reason = should_skip_amazon_sync(store)
        if should_skip:
            logger.warning(f"Skipping Amazon client creation for store {store.name}: {skip_reason}")
            return (False, f"Store has auth error: {skip_reason}. Use 'Reconnect Amazon' to fix.", store)
        
        # Import Amazon service
        from amazon_service import AmazonAPIService
        
        # Determine region from store credentials or use default
        region = marketplace_region
        if store.api_key:
            try:
                import json
                creds = json.loads(store.api_key)
                region = creds.get('region', marketplace_region)
            except:
                pass
        
        # Create Amazon service
        amazon_service = AmazonAPIService(marketplace_region=region)
        
        # Test connection (this will use ensure_access_token internally)
        if not amazon_service.authenticate_store(store):
            # Check if auth error was set
            db.session.refresh(store)
            if getattr(store, 'auth_status', 'ok') == 'auth_error':
                error_msg = f"Authentication failed: {store.auth_error_code or 'unknown'}"
            else:
                error_msg = f"Authentication failed for store '{store.name}'"
            return (False, error_msg, store)
        
        logger.info(f"✅ Amazon client created successfully for store: {store.name}")
        return (True, amazon_service, store)
        
    except Exception as e:
        logger.error(f"Error creating Amazon client for store {store_id}: {str(e)}")
        # Check if it's an auth error
        from amazon_auth import is_auth_failure
        if is_auth_failure(error_message=str(e)):
            return (False, f"Authentication failed: {str(e)}", store if 'store' in locals() else None)
        return (False, str(e), store if 'store' in locals() else None)


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
