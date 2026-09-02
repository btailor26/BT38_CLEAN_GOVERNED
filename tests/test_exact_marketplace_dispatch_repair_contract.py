from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ebay_exact_fulfillment_is_marketplace_lifecycle_truth():
    source = (ROOT / "services" / "governed_exact_ebay_order_hydration.py").read_text()
    assert 'row_marketplace_status = "shipped" if fulfillment is not None else marketplace_status' in source
    assert "fulfillment_lifecycle_rows" in source
    assert '"marketplace_write_started": False' in source


def test_amazon_exact_readback_has_legacy_order_status_fallback():
    source = (ROOT / "services" / "governed_amazon_tracking_readback.py").read_text()
    assert "/orders/v0/orders/" in source
    assert "_legacy_exact_order_lifecycle" in source
    assert '"truth_source": "orders_v0_exact_order_status"' in source
    assert '"FULFILLED": "shipped"' in source
    assert '"DISPATCHED": "shipped"' in source
    assert '"marketplace_write_started": False' in source


def test_amazon_exact_marketplace_cancellation_is_terminal_truth():
    source = (ROOT / "services" / "governed_amazon_tracking_readback.py").read_text()
    assert '"CANCELED": "cancelled"' in source
    assert '"CANCELLED": "cancelled"' in source
    assert 'if incoming_value == "cancelled":' in source
    assert 'return current_value != "delivered"' in source


def test_amazon_exact_recovery_blueprint_is_registered_from_app():
    app_source = (ROOT / "app.py").read_text()
    route_source = (
        ROOT / "services" / "governed_amazon_exact_order_recovery_route.py"
    ).read_text()
    assert "governed_amazon_exact_order_recovery_bp" in app_source
    assert "app.register_blueprint(governed_amazon_exact_order_recovery_bp)" in app_source
    assert '"/governed/actions/amazon/exact-order-recovery"' in route_source


def test_exact_dispatch_repair_does_not_use_label_or_tracking_as_lifecycle_authority():
    amazon = (ROOT / "services" / "governed_amazon_tracking_readback.py").read_text()
    ebay = (ROOT / "services" / "governed_exact_ebay_order_hydration.py").read_text()
    assert "legacy_lifecycle" in amazon
    assert "row_marketplace_status" in ebay
    assert "label_purchased_at" not in amazon
    assert "label_purchased_at" not in ebay
