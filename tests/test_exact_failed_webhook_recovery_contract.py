from pathlib import Path
import ast


RECOVERY_SOURCE = Path(
    "services/governed_webhook_rejection_recovery.py"
).read_text(encoding="utf-8")
EXACT_SOURCE = Path(
    "services/governed_exact_webhook_recovery.py"
).read_text(encoding="utf-8")

RECOVERY_TREE = ast.parse(RECOVERY_SOURCE)
EXACT_TREE = ast.parse(EXACT_SOURCE)


def _function_source(tree: ast.AST, source: str, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_failed_webhook_recovery_is_exact_not_platform_scan():
    runner = _function_source(
        RECOVERY_TREE,
        RECOVERY_SOURCE,
        "_run_pending_recoveries",
    )
    requester = _function_source(
        RECOVERY_TREE,
        RECOVERY_SOURCE,
        "request_rejected_webhook_recovery",
    )

    assert "recover_exact_failed_webhook" in runner
    assert "run_governed_warehouse_sync" not in runner
    assert "run_governed_marketplace_order_import" not in runner
    assert "_pending_notifications" in requester
    assert "notification_record_id" in requester


def test_exact_recovery_skips_existing_order_before_replay():
    recover = _function_source(
        EXACT_TREE,
        EXACT_SOURCE,
        "recover_exact_failed_webhook",
    )

    exists_pos = recover.index("_canonical_order_exists")
    replay_pos = recover.index("process_marketplace_notification")
    assert exists_pos < replay_pos
    assert '"duplicate_skipped": True' in recover
    assert '"already_present": True' in recover
    assert '"broad_scan_started": False' in recover


def test_missing_order_replays_only_durable_notification():
    recover = _function_source(
        EXACT_TREE,
        EXACT_SOURCE,
        "recover_exact_failed_webhook",
    )

    assert "_load_notification" in recover
    assert "notification_record_id=int(notification_record_id)" in recover
    assert "process_marketplace_notification" in recover
    assert "get_orders" not in recover
    assert "run_governed_warehouse_sync" not in recover
    assert "run_governed_marketplace_order_import" not in recover


def test_successful_exact_recovery_closes_durable_notification():
    runner = _function_source(
        RECOVERY_TREE,
        RECOVERY_SOURCE,
        "_run_pending_recoveries",
    )

    assert "mark_notification_status" in runner
    assert 'processing_status="COMPLETED"' in runner
    assert 'last_error=""' in runner
    assert "completed=True" in runner


def test_restart_recovery_selects_only_failed_or_stranded_webhook_ids():
    selector = _function_source(
        RECOVERY_TREE,
        RECOVERY_SOURCE,
        "_queue_stranded_durable_notifications",
    )

    assert "webhooks.amazon_notifications" in selector
    assert "webhooks.ebay_notifications" in selector
    assert "processing_status = 'FAILED'" in selector
    assert "processing_status = 'PROCESSING'" in selector
    assert "request_rejected_webhook_recovery" in selector
    assert "get_orders" not in selector
    assert "run_governed_warehouse_sync" not in selector
    assert "run_governed_marketplace_order_import" not in selector
