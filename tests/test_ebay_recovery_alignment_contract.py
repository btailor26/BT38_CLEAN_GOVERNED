from pathlib import Path
import ast


RUNTIME_PATH = Path("services/governed_runtime_engine.py")


def _source():
    return RUNTIME_PATH.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    source = _source()
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, function)


def test_ebay_recovery_reuses_canonical_marketplace_order_importer():
    function_source = _function_source("run_governed_marketplace_import_refresh")

    assert "run_governed_ebay_inventory_import" in function_source
    assert "run_governed_marketplace_order_import" in function_source
    assert 'source=f"{source}:ebay_order_recovery"' in function_source
    assert '"order_recovery": order_recovery' in function_source


def test_eight_hour_recovery_is_enabled_by_default():
    engine_source = _function_source("_engine_loop")
    status_source = _function_source("get_governed_runtime_status")

    expected = 'os.getenv("ENABLE_GOVERNED_8H_HYDRATION", "true")'
    assert expected in engine_source
    assert expected in status_source


def test_fifteen_minute_verification_stays_exact_scope_only():
    function_source = _function_source("_run_light_reconcile_cycle")

    assert "run_governed_marketplace_order_import" not in function_source
    assert "run_governed_marketplace_import_refresh" not in function_source
    assert '"full_scan_started": False' in function_source
    assert '"recent_order_import_started": False' in function_source
