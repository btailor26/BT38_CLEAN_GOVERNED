from pathlib import Path


EXECUTION = Path("services/governed_webhook_execution.py").read_text(encoding="utf-8")
EBAY_IMPORT = Path("services/governed_ebay_inventory_import.py").read_text(encoding="utf-8")
UI_SIGNAL = Path("services/governed_ui_event_signal.py").read_text(encoding="utf-8")
ROUTES = Path("governed_routes.py").read_text(encoding="utf-8")


def test_listing_notifications_refresh_known_and_missing_listings():
    assert "if listing_was_missing or listing_notification:" in EXECUTION
    assert "LISTINGS_ITEM_STATUS_CHANGE" in EXECUTION
    assert "LISTINGS_ITEM_MFN_QUANTITY_CHANGE" in EXECUTION
    assert 'topic == "LISTING"' in EXECUTION


def test_listing_import_returns_exact_ui_mutation_scope():
    for field in (
        "affected_listing_ids",
        "affected_warehouse_stock_ids",
        "affected_group_ids",
    ):
        assert field in EXECUTION
        assert field in EBAY_IMPORT
    assert "created=listing_discovered" in EXECUTION
    assert '"listing_discovery"' in UI_SIGNAL


def test_ebay_notification_uses_exact_item_before_bounded_fallback():
    assert "item_id = _notification_item_id(payload)" in EBAY_IMPORT
    exact = EBAY_IMPORT.index("if item_id:")
    fallback = EBAY_IMPORT.index("run_governed_ebay_inventory_import(", exact)
    assert exact < fallback
    assert "_get_item_detail(creds, item_id)" in EBAY_IMPORT


def test_ebay_import_assigns_permanent_original_group():
    assert "ensure_permanent_original_group(stock)" in EBAY_IMPORT
    assert "if listing.master_product_group_id is None:" in EBAY_IMPORT


def test_ebay_variation_identity_includes_item_id_and_sku():
    assert "if is_variation_child:" in EBAY_IMPORT
    assert "MarketplaceListing.external_listing_id == item_id" in EBAY_IMPORT
    assert "MarketplaceListing.external_sku == sku" in EBAY_IMPORT


def test_token_refresh_reconciles_order_and_listing_subscriptions():
    refresh = ROUTES.split('def governed_ebay_oauth_refresh_token():', 1)[1]
    assert "ensure_ebay_order_notification_registration(" in refresh
    assert '"notification_registration": notification_registration' in refresh
