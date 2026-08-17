from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_fba_led_ebay_sale_waits_for_amazon_confirmation_before_push():
    source = _read("services/governed_webhook_execution.py")

    waiting = source.index('status="fba_group_waiting_for_amazon_confirmation"')
    group_push = source.index("push_result = push_group_listings(", waiting)

    assert "fba_authority = next(" in source
    assert "if fba_authority is not None:" in source
    assert 'push_started=False' in source[waiting:group_push]
    assert 'waiting_for_amazon_fba_confirmation=True' in source[waiting:group_push]


def test_fbm_group_keeps_existing_immediate_group_push():
    source = _read("services/governed_webhook_execution.py")

    assert 'source=f"webhook_{marketplace}_group_notification"' in source
    assert 'status="group_processed"' in source


def test_post_submit_mcf_cannot_queue_fba_or_marketplace_write():
    source = _read("services/governed_mcf_confirmation.py")

    assert "notify_governed_runtime_work" not in source
    assert '"fba_exact_verifications_queued": 0' in source
    assert '"fba_verification_waiting_for_amazon_webhook": True' in source
    assert '"marketplace_write_started": False' in source


def test_mcf_status_webhook_is_lifecycle_only_not_second_inventory_trigger():
    source = _read("services/governed_webhook_execution.py")

    start = source.index('== "FULFILLMENT_ORDER_STATUS"')
    end = source.index('if business_event == "cancellation":', start)
    block = source[start:end]

    assert "refresh_mcf_from_amazon_signal(payload)" in block
    assert "push_group_listings" not in block
    assert "push_marketplace_listing" not in block
    assert "inventory_push_started=False" in block


def test_exact_fba_verifier_only_propagates_when_amazon_truth_changed():
    source = _read("services/governed_runtime_engine.py")

    start = source.index("def _verify_exact_fba")
    end = source.index("def _verify_exact_listing", start)
    block = source[start:end]

    assert 'fba_result.get("stock_changed")' in block
    assert '"source": "amazon_webhook_exact_fba_handoff"' in block
