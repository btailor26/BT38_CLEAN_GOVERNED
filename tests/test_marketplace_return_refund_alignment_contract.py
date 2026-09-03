from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (
    ROOT / "services" / "governed_fbm_lifecycle_alignment.py"
).read_text(encoding="utf-8")
EXECUTION = (
    ROOT / "services" / "governed_webhook_execution.py"
).read_text(encoding="utf-8")


def test_ebay_return_activity_uses_existing_exact_order_lifecycle_without_stock():
    webhook_patch = ALIGNMENT.split(
        "def _patch_webhook_lifecycle() -> None:", 1
    )[1].split("\ndef _patch_provider_lifecycle_persistence()", 1)[0]

    assert '"RETURNREQUESTED": "return_requested"' in webhook_patch
    assert '"RETURNFULFILLMENTINITIATED": "return_requested"' in webhook_patch
    assert '"RETURNFULFILLMENTCOMPLETED": "returned"' in webhook_patch
    assert '"RETURNCLOSED": "returned"' in webhook_patch
    assert 'execution._apply_marketplace_order_lifecycle_event = aligned_apply' in webhook_patch
    assert 'MarketplaceOrder.marketplace_order_id == order_id' in webhook_patch

    terminal_path = EXECUTION.split(
        'if order_lifecycle.get("terminal"):', 1
    )[1].split("listing = _find_listing(", 1)[0]
    assert 'stock_changed=False' in terminal_path
    assert 'correction_started=False' in terminal_path
    assert 'push_started=False' in terminal_path


def test_amazon_refund_transaction_cannot_fall_through_to_stock_decrement():
    webhook_patch = ALIGNMENT.split(
        "def _patch_webhook_lifecycle() -> None:", 1
    )[1].split("\ndef _patch_provider_lifecycle_persistence()", 1)[0]

    assert "original_classify = execution._classify_business_event" in webhook_patch
    assert '"refund"' in webhook_patch
    assert '"refunded"' in webhook_patch
    assert '"refund_issued"' in webhook_patch
    assert 'return "return"' in webhook_patch
    assert 'execution._classify_business_event = aligned_classify' in webhook_patch


def test_amazon_transaction_related_identifier_resolves_only_order_identity():
    webhook_patch = ALIGNMENT.split(
        "def _patch_webhook_lifecycle() -> None:", 1
    )[1].split("\ndef _patch_provider_lifecycle_persistence()", 1)[0]

    assert "original_order_id = execution._extract_marketplace_order_id" in webhook_patch
    assert 'value.get("RelatedIdentifierName")' in webhook_patch
    assert 'value.get("RelatedIdentifierValue")' in webhook_patch
    assert '{"ORDERID", "AMAZONORDERID", "MARKETPLACEORDERID"}' in webhook_patch
    assert 'execution._extract_marketplace_order_id = aligned_order_id' in webhook_patch


def test_return_and_refund_continue_to_use_existing_fbm_status_contract():
    for status in (
        '"return_requested"',
        '"returned"',
        '"refund_requested"',
        '"refunded"',
    ):
        assert status in ALIGNMENT

    assert "Thread(" not in ALIGNMENT
    assert "Queue(" not in ALIGNMENT
    assert "setInterval(" not in ALIGNMENT
