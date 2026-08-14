from pathlib import Path
import ast


SOURCE_PATH = Path("services/governed_runtime_engine.py")


def _source():
    return SOURCE_PATH.read_text(encoding="utf-8")


def _function_source(name):
    source = _source()
    tree = ast.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node)


def test_15m_cycle_cannot_run_broad_order_or_stock_scans():
    body = _function_source("_run_light_reconcile_cycle")

    assert "run_governed_marketplace_order_import" not in body
    assert "mutate_recent_marketplace_order_lines" not in body
    assert "run_governed_marketplace_import_refresh" not in body
    assert "limit=100" not in body
    assert "_verify_webhook_event" in body
    assert '"full_scan_started": False' in body
    assert '"warehouse_scan_started": False' in body


def test_source_only_notification_is_not_allowed_to_touch_database():
    body = _function_source("_verify_webhook_event")

    assert "if not event.get(\"scope_present\")" in body
    assert '"reason": "webhook_scope_required"' in body
    assert '"database_touched": False' in body


def test_exact_verifiers_use_first_not_all_or_recent_windows():
    source = _source()

    for name in ("_verify_exact_order", "_verify_exact_fba", "_verify_exact_listing"):
        body = _function_source(name)
        assert ".first()" in body
        assert ".all()" not in body
        assert ".count()" not in body

    assert "timedelta(seconds=LIGHT_RECONCILE_SECONDS)" in source

def test_duplicate_event_preserves_earliest_verification_deadline():
    body = _function_source("notify_governed_runtime_work")

    assert 'queued["verify_after"] = min(' in body
    assert 'effective_verify_after = queued["verify_after"]' in body


def test_runtime_wake_signal_is_cleared_before_future_due_wait():
    body = _function_source("_engine_loop")

    clear_pos = body.index("_pending_notification_event.clear()")
    due_pos = body.index("seconds_until_due = _seconds_until_next_due()")
    assert clear_pos < due_pos

def test_fba_startup_recovery_is_bounded_and_exact():
    body = _function_source("_recover_fba_verification_events")

    assert "timedelta(hours=24)" in body
    assert "LIMIT 250" in body
    assert "amazon_fba_inventory" in body
    assert "marketplace_orders" in body
    assert "seller_sku = mo.sku" in body
    assert "run_governed_marketplace_import_refresh" not in body
    assert "get_inventory(" not in body
    assert '"full_scan_started": False' in body


def test_fba_startup_recovery_uses_original_deadlines():
    body = _function_source("_recover_fba_verification_events")

    assert "settlement_due = event_at + timedelta(seconds=90)" in body
    assert "light_due = event_at + timedelta(" in body
    assert "seconds=LIGHT_RECONCILE_SECONDS" in body
    assert 'settlement_event["verify_after"] = settlement_due' in body
    assert 'light_event["verify_after"] = light_due' in body


def test_fba_startup_recovery_does_not_repeat_satisfied_phase():
    body = _function_source("_recover_fba_verification_events")

    assert "last_synced_at < settlement_due" in body
    assert "last_synced_at < light_due" in body
    assert "duplicates_skipped" in body


def test_fba_recovery_runs_after_startup_hydration():
    body = _function_source("_engine_loop")

    hydration_pos = body.index("_run_full_sync_cycle()")
    recovery_pos = body.index("_recover_fba_verification_events(app)")
    loop_pos = body.index("while not _stop_event.is_set()")

    assert hydration_pos < recovery_pos < loop_pos

