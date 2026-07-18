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
