from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rejected_webhook_recovery_is_failure_only_and_one_shot():
    source = (
        ROOT / "services" / "governed_webhook_rejection_recovery.py"
    ).read_text(encoding="utf-8")

    assert '"/governed/webhooks/amazon": "amazon"' in source
    assert '"/governed/webhooks/ebay": "ebay"' in source
    assert "if not _response_failed(response):" in source
    assert "request_rejected_webhook_recovery(platform)" in source
    assert "_pending_platforms.add(platform)" in source
    assert "threading.Thread(" in source
    assert "daemon=True" in source
    assert "while True:" in source
    assert "if not _pending_platforms:" in source
    assert "_scan_running = False" in source
    assert "setInterval" not in source
    assert "sleep(" not in source


def test_rejected_webhook_recovery_reuses_governed_recent_order_scan():
    source = (
        ROOT / "services" / "governed_webhook_rejection_recovery.py"
    ).read_text(encoding="utf-8")

    assert "from services.governed_warehouse_sync import" in source
    assert "run_governed_warehouse_sync(" in source
    assert 'actor=f"{platform}_webhook_rejected_recovery"' in source
    assert "manual=False" in source
    assert "publish_webhook_ui_event(" in source
    assert "run_governed_marketplace_import_refresh" not in source


def test_manual_warehouse_sync_contract_is_preserved():
    source = (
        ROOT / "services" / "governed_warehouse_sync.py"
    ).read_text(encoding="utf-8")

    assert 'actor="manual-warehouse-sync"' in source
    assert "manual=True" in source
    assert "manual=bool(manual)" in source
    assert '"automatic_recovery": not bool(manual)' in source
    assert "run_governed_marketplace_order_import(" in source


def test_main_loads_rejected_webhook_recovery_alignment():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "import services.governed_webhook_rejection_recovery" in source
