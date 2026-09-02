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
EXACT_RECOVERY = Path(
    "services/governed_exact_webhook_recovery.py"
).read_text(encoding="utf-8")
WEBHOOK_HANDOFF = Path(
    "services/governed_webhook_rejection_recovery.py"
).read_text(encoding="utf-8")


def test_ebay_exact_readback_reads_shipping_fulfillment_tracking():
    assert '/shipping_fulfillment' in EBAY_HYDRATION
    assert 'trackingNumber' in EBAY_HYDRATION
    assert 'shippingCarrierCode' in EBAY_HYDRATION
    assert 'shippedDate' in EBAY_HYDRATION
    assert 'MarketplaceOrder.store_id == store.id' in EBAY_HYDRATION
    assert 'MarketplaceOrder.marketplace_order_id == order_id' in EBAY_HYDRATION


def test_ebay_tracking_readback_corrects_persisted_marketplace_truth_without_writing_marketplace():
    assert '_text(row.tracking_number) != _text(shipment["tracking_number"])' in EBAY_HYDRATION
    assert '_text(row.carrier) != _text(shipment["carrier"])' in EBAY_HYDRATION
    assert 'row.shipped_at != shipment["shipped_at"]' in EBAY_HYDRATION
    assert 'marketplace_status = _ebay_lifecycle_status(order)' in EBAY_HYDRATION
    assert 'payment == "PAID" and fulfillment == "FULFILLED"' in EBAY_HYDRATION
    assert 'return "shipped"' in EBAY_HYDRATION
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


def test_amazon_dispatch_status_is_authority_without_tracking_dependency():
    assert 'def _order_lifecycle(' in AMAZON_TRACKING
    assert 'order_payload.get("orderStatus")' in AMAZON_TRACKING
    assert '"SHIPPED": "shipped"' in AMAZON_TRACKING
    assert '"DELIVERED": "delivered"' in AMAZON_TRACKING
    assert 'lifecycle_status = order_lifecycle' in AMAZON_TRACKING
    assert 'tracked = [row for row in packages if _text(row.get("trackingNumber"))]' in AMAZON_TRACKING
    assert 'if not tracked:\n        return None, None' not in AMAZON_TRACKING
    assert 'package.get("shipTime")' in AMAZON_TRACKING
    assert 'package.get("createdTime")' not in AMAZON_TRACKING
    assert 'Carrier and\n        # tracking are optional enrichment; lifecycle never depends on them.' in AMAZON_TRACKING


def test_amazon_tracking_is_fbm_only_and_corrects_marketplace_owned_package_truth():
    assert '{"FBA", "AFN", "MCF"}' in AMAZON_TRACKING
    assert 'startswith("mcf_")' in AMAZON_TRACKING
    assert '_text(getattr(row, "tracking_number", None)) != _text(shipment["tracking_number"])' in AMAZON_TRACKING
    assert '_text(getattr(row, "carrier", None)) != _text(shipment["carrier"])' in AMAZON_TRACKING
    assert 'getattr(row, "shipped_at", None) != shipment["shipped_at"]' in AMAZON_TRACKING
    assert '_can_advance_lifecycle(getattr(row, "status", None), lifecycle_status)' in AMAZON_TRACKING
    assert '"marketplace_write_started": False' in AMAZON_TRACKING
    assert 'requests.put(' not in AMAZON_TRACKING
    assert 'requests.patch(' not in AMAZON_TRACKING


def test_exact_recovery_uses_same_existing_order_handoff_for_ebay_and_amazon():
    assert 'def _hydrate_existing_ebay_order(' in EXACT_RECOVERY
    assert 'hydrate_exact_ebay_order(' in EXACT_RECOVERY
    assert 'def _hydrate_existing_amazon_order(' in EXACT_RECOVERY
    assert 'hydrate_amazon_tracking_for_order(' in EXACT_RECOVERY
    assert '"order_replayed": False' in EXACT_RECOVERY
    assert '"canonical_order_missing_for_dispatch_lifecycle"' in EXACT_RECOVERY
    assert '"stock_mutation_started": False' in EXACT_RECOVERY
    missing_guard = EXACT_RECOVERY.split(
        'if dispatch_lifecycle:', 1
    )[1].split('replay_payload = dict(payload)', 1)[0]
    assert 'process_marketplace_notification' not in missing_guard


def test_successful_marketplace_dispatch_notifications_enter_exact_handoff():
    assert 'def _request_is_dispatch_lifecycle(' in WEBHOOK_HANDOFF
    assert 'ITEMMARKEDSHIPPED' in WEBHOOK_HANDOFF
    assert '"FULFILLED"' in WEBHOOK_HANDOFF
    assert '"SHIPPED"' in WEBHOOK_HANDOFF
    assert '"DELIVERED"' in WEBHOOK_HANDOFF
    assert 'if not failed and dispatch_lifecycle:' in WEBHOOK_HANDOFF
    assert 'request_rejected_webhook_recovery(' in WEBHOOK_HANDOFF
    assert 'X-BT38-Exact-Lifecycle-Handoff' in WEBHOOK_HANDOFF
    lifecycle_detector = WEBHOOK_HANDOFF.split(
        'def _request_is_dispatch_lifecycle(', 1
    )[1].split('def _notification_record_id_from_response(', 1)[0]
    assert '_deep_get(payload, "tracking_number")' not in lifecycle_detector
    assert '_deep_get(payload, "trackingNumber")' not in lifecycle_detector
    assert '_deep_get(payload, "carrier")' not in lifecycle_detector
    assert '_deep_get(payload, "carrierName")' not in lifecycle_detector


def test_manual_exact_ebay_recovery_readback_cannot_500_on_unmapped_import_source():
    assert '"import_source": getattr(row, "import_source", None)' in WEBHOOK_HANDOFF
    assert '"reason": "exact_ebay_recovery_exception"' in WEBHOOK_HANDOFF
    assert 'db.session.rollback()' in WEBHOOK_HANDOFF


def test_amazon_profile_reuses_tracking_readback_without_buy_shipping_dependency():
    assert 'hydrate_amazon_tracking_for_order' in AMAZON_PROFILE
    assert 'SHIPPED' in AMAZON_PROFILE
    assert 'PARTIALLYSHIPPED' in AMAZON_PROFILE
    assert 'AmazonShippingAdapter' not in AMAZON_TRACKING
    assert 'MerchantFulfillment' not in AMAZON_TRACKING
    assert 'create_shipment' not in AMAZON_TRACKING
