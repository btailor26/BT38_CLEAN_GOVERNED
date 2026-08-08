from pathlib import Path
import ast


SYNC_PATH = Path("services/governed_warehouse_sync.py")
JS_PATH = Path("static/js/warehouse-governed.js")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(path: Path, name: str) -> str:
    source = _source(path)
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, function)


def test_warehouse_sync_uses_recent_order_recovery_not_full_hydration():
    function_source = _function_source(
        SYNC_PATH,
        "run_governed_warehouse_sync",
    )

    assert "run_governed_marketplace_order_import" in function_source
    assert "run_governed_marketplace_import_refresh" not in function_source
    assert '"mode": "governed_recent_order_recovery"' in function_source


def test_warehouse_sync_preserves_runtime_guard():
    source = _source(SYNC_PATH)

    assert "is_runtime_action_allowed" in source
    assert 'action_type="sync"' in source
    assert "manual=True" in source


def test_warehouse_button_waits_for_success_then_reloads():
    source = _source(JS_PATH)

    assert "'/governed/warehouse/sync'" in source
    assert "await postJson(" in source
    assert "window.location.reload();" in source
