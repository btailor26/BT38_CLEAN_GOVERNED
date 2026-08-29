from pathlib import Path


EBAY_HYDRATION = Path(
    "services/governed_exact_ebay_order_hydration.py"
).read_text(encoding="utf-8")
EBAY_NOTIFICATION = Path(
    "services/governed_ebay_shipping_notification_alignment.py"
).read_text(encoding="utf-8")
EBAY_SCOPES = Path(
    "services/governed_ebay_oauth_scopes.py"
).read_text(encoding="utf-8")
EBAY_POST_DEPLOY = Path(
    "services/governed_ebay_post_deploy_alignment.py"
).read_text(encoding="utf-8")
AMAZON_TRACKING = Path(
    "services/governed_amazon_tracking_readback.py"
).read_text(encoding="utf-8")
AMAZON_PROFILE = Path(
    "services/fbm_amazon_order_profile.py"
).read_text(encoding="utf-8")


def test_ebay_exact_readback_reads_shipping_fulfillment_tracking():
    assert '/shipping_fulfillment' in EBAY_HYDRATION
    assert 'trackingNumber' in EBAY_HYDRATION
    assert 'shippingCarrierCode' in EBAY_HYDRATION
    assert 'shippedDate' in EBAY_HYDRATION
    assert 'MarketplaceOrder.store_id == store.id' in EBAY_HYDRATION
    assert 'MarketplaceOrder.marketplace_order_id == order_id' in EBAY_HYDRATION


def test_ebay_tracking_readback_does_not_overwrite_stronger_truth_or_write_marketplace():
    assert 'and not _text(row.tracking_number)' in EBAY_HYDRATION
    assert 'and not _text(row.carrier)' in EBAY_HYDRATION
    assert 'row.shipped_at is None' in EBAY_HYDRATION
    assert '"marketplace_write_started": False' in EBAY_HYDRATION
    assert 'requests.post(' not in EBAY_HYDRATION
    assert 'requests.put(' not in EBAY_HYDRATION
    assert 'requests.patch(' not in EBAY_HYDRATION
    assert 'submit_mcf' not in EBAY_HYDRATION


def test_ebay_shipping_notification_is_optional_accelerator_with_reauth_fallback():
    assert 'ITEM_MARKED_SHIPPED' in EBAY_NOTIFICATION
    assert 'authorization_required' in EBAY_NOTIFICATION
    assert 'reauthorization_required' in EBAY_NOTIFICATION
    assert 'commerce_shipping_scope_not_granted' in EBAY_NOTIFICATION
    assert '_ensure_subscription(' in EBAY_NOTIFICATION
    assert 'marketplace_write_started' in EBAY_NOTIFICATION
    assert 'https://api.ebay.com/oauth/api_scope/commerce.shipping' in EBAY_SCOPES


def test_ebay_post_deploy_recovers_recent_missing_tracking_independent_of_latest_webhook():
    assert 'def _recover_recent_missing_tracking(' in EBAY_POST_DEPLOY
    recovery = EBAY_POST_DEPLOY.split(
        'def _recover_recent_missing_tracking(', 1
    )[1].split(
        'def align_ebay_notifications_and_recover_missed_changes(', 1
    )[0]
    assert 'MarketplaceOrder.fulfillment_type == "FBM"' in recovery
    assert 'MarketplaceOrder.tracking_number.is_(None)' in recovery
    assert 'MarketplaceOrder.tracking_number == ""' in recovery
    assert 'MarketplaceOrder.created_at >= cutoff' in recovery
    assert 'MarketplaceOrder.marketplace_created_at' not in recovery
    assert '.limit(max(1, min(int(limit), 100)))' in recovery
    assert 'hydrate_exact_ebay_order(' in recovery
    assert 'last_webhook_at' not in recovery
    assert '"marketplace_write_started": False' in recovery
    assert '"polling_started": False' in recovery
    assert '"scheduler_started": False' in recovery
    assert '"tracking_catchup": tracking_recovery' in EBAY_POST_DEPLOY


def test_amazon_tracking_uses_current_orders_packages_dataset():
    assert '/orders/2026-01-01/orders/' in AMAZON_TRACKING
    assert '"includedData": "PACKAGES"' in AMAZON_TRACKING
    assert 'trackingNumber' in AMAZON_TRACKING
    assert 'carrier' in AMAZON_TRACKING
    assert 'shipTime' in AMAZON_TRACKING
    assert 'x-amz-access-token' in AMAZON_TRACKING


def test_amazon_tracking_is_fbm_only_and_fill_missing_only():
    assert '{"FBA", "AFN", "MCF"}' in AMAZON_TRACKING
    assert 'startswith("mcf_")' in AMAZON_TRACKING
    assert 'not _text(getattr(row, "tracking_number", None))' in AMAZON_TRACKING
    assert 'not _text(getattr(row, "carrier", None))' in AMAZON_TRACKING
    assert 'getattr(row, "shipped_at", None) is None' in AMAZON_TRACKING
    assert '"marketplace_write_started": False' in AMAZON_TRACKING
    assert 'requests.put(' not in AMAZON_TRACKING
    assert 'requests.patch(' not in AMAZON_TRACKING


def test_amazon_profile_reuses_tracking_readback_without_buy_shipping_dependency():
    assert 'hydrate_amazon_tracking_for_order' in AMAZON_PROFILE
    assert 'SHIPPED' in AMAZON_PROFILE
    assert 'PARTIALLYSHIPPED' in AMAZON_PROFILE
    assert 'AmazonShippingAdapter' not in AMAZON_TRACKING
    assert 'MerchantFulfillment' not in AMAZON_TRACKING
    assert 'create_shipment' not in AMAZON_TRACKING
