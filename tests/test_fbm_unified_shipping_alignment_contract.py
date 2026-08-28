from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shipping_options_refreshes_marketplace_before_reading_cached_quantity():
    text = source("services/fbm_unified_shipping_alignment.py")
    assert "shipping_options_exact_marketplace_refresh" in text
    assert "row.quantity = _positive_quantity(item.get(\"quantity\"))" in text
    assert "row.quantity = quantity" in text
    assert "exact_shipping_reader_not_configured" in text


def test_provider_rule_is_unified_not_ebay_only():
    text = source("services/fbm_unified_shipping_alignment.py")
    assert 'platform in {"amazon", "ebay"}' in text
    assert "refresh_marketplace_shipping_order" in text
    assert "provider_rate_exact_marketplace_refresh" in text


def test_exact_shipping_refresh_does_not_create_orders_or_touch_stock_path():
    text = source("services/fbm_unified_shipping_alignment.py")
    assert "MarketplaceOrder(" not in text
    assert "process_exact_marketplace_order_line" not in text
    assert "WarehouseStock" not in text


def test_parcel_autosave_is_marketplace_neutral_and_order_scoped():
    text = source("services/fbm_operational_autosave.py")
    assert '@app.post("/fbm/orders/<int:order_id>/parcel")' in text
    assert "save_order_parcel(order, values)" in text
    assert "platform ==" not in text


def test_initial_fbm_page_stays_db_only_and_batched():
    text = source("services/fbm_page_performance_alignment.py")
    assert 'request.path.rstrip("/") != "/fbm"' in text
    assert "selectinload(MarketplaceOrder.store)" in text
    assert "selectinload(MarketplaceOrder.warehouse_stock)" in text
    assert "ops._request_page_maps()" in text
    assert "requests." not in text
    assert "get_or_refresh_amazon_profile" not in text


def test_packlink_optional_selector_failure_continues_on_proven_browser_save_only():
    text = source("services/fbm_packlink_location_fallback.py")
    assert '"selector could not be resolved" in text' in text
    assert 'adapter._post_json("shipments", body)' in text
    assert "_browser_save_body(snapshot, reference)" in text
    assert 'adapter._put_json(f"shipments/{reference}", save_body)' in text
    assert "location_selector_fallback" in text


def test_compat_installs_unified_alignment_and_retires_ebay_only_provider_wrapper():
    text = source("services/governed_mcf_compat.py")
    assert "import services.fbm_unified_shipping_alignment" in text
    assert "import services.fbm_packlink_location_fallback" in text
    assert "import services.fbm_page_performance_alignment" in text
    assert "import services.fbm_live_feed_alignment" not in text
