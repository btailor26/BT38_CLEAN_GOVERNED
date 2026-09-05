from pathlib import Path


SOURCE = Path("services/governed_webhook_rejection_recovery.py").read_text(encoding="utf-8")


def test_startup_recovery_is_failed_only_and_bounded_to_24_hours():
    assert "INTERVAL '24 hours'" in SOURCE
    assert "INTERVAL '48 hours'" not in SOURCE
    selector = SOURCE.split("def _queue_stranded_durable_notifications", 1)[1].split("@app.before_request", 1)[0]
    assert "processing_status = 'FAILED'" in selector
    assert "processing_status = 'PROCESSING'" in selector
    assert "processing_status = 'COMPLETED'" not in selector
    assert "amazon_orphans" not in selector
    assert "fba_settlement_gaps" not in selector


def test_successful_lifecycle_webhooks_never_enter_recovery():
    hook = SOURCE.split("def recover_when_marketplace_webhook_is_rejected", 1)[1]
    assert "if not failed:\n        return response" in hook
    assert "X-BT38-Exact-Lifecycle-Handoff" not in hook
    assert "dispatch_lifecycle =" not in hook
    assert "Successful shipment/lifecycle notifications" in SOURCE


def test_recovery_remains_exact_and_failure_driven():
    assert "request_rejected_webhook_recovery" in SOURCE
    assert "recover_exact_failed_webhook" in SOURCE
    assert "No recent-order scan" in SOURCE
    assert "marketplace-wide recovery" in SOURCE
