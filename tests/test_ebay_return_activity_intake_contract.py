from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_ebay_return_intake_alignment.py").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
LIFECYCLE = (ROOT / "services" / "governed_fbm_lifecycle_alignment.py").read_text(encoding="utf-8")


def test_modern_ebay_return_activity_uses_existing_webhook_lifecycle():
    assert '"ORDER_RETURN_ACTIVITY"' in ALIGNMENT
    assert '"RETURN_REQUESTED"' in ALIGNMENT
    assert '"RETURN_FULFILLMENT_INITIATED"' in ALIGNMENT
    assert '"RETURN_FULFILLMENT_COMPLETED"' in ALIGNMENT
    assert '"RETURN_CLOSED"' in ALIGNMENT
    assert '_nested_dict(payload, "metadata", "topic")' in ALIGNMENT
    assert '_nested_dict(payload, "notification", "data", "activityType")' in ALIGNMENT
    assert 'return "return"' in ALIGNMENT
    assert 'execution._event_type = aligned_event_type' in ALIGNMENT
    assert 'execution._classify_business_event = aligned_classify' in ALIGNMENT


def test_ebay_return_activity_resolves_existing_order_without_second_order_system():
    assert 'returnlineitems' in ALIGNMENT.lower()
    assert 'line.get("orderId")' in ALIGNMENT
    assert 'execution._extract_marketplace_order_id = aligned_order_id' in ALIGNMENT
    assert 'MarketplaceOrder(' not in ALIGNMENT
    assert 'db.session.add' not in ALIGNMENT
    assert 'db.session.commit' not in ALIGNMENT
    assert 'requests.' not in ALIGNMENT


def test_ebay_return_intake_never_mutates_warehouse_stock():
    assert 'Warehouse stock' in ALIGNMENT
    assert 'mutate_warehouse_stock' not in ALIGNMENT
    assert 'available_quantity' not in ALIGNMENT
    assert 'process_exact_marketplace_order_line' not in ALIGNMENT
    assert '"return_requested"' in LIFECYCLE
    assert '"returned"' in LIFECYCLE
    assert '"refund_requested"' in LIFECYCLE
    assert '"refunded"' in LIFECYCLE


def test_ebay_return_intake_is_installed_before_final_fbm_overlay():
    assert 'install_governed_ebay_return_intake_alignment' in MAIN
    assert MAIN.index('install_governed_ebay_return_intake_alignment()') < MAIN.index('install_governed_fbm_small_alignment(app)')
