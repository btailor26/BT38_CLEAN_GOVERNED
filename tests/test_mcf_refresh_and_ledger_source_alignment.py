from pathlib import Path
import ast


LEDGER_PATH = Path("services/governed_order_stock_mutation.py")
MCF_TEMPLATE = Path("templates/mcf_orders.html")


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_source(path: Path, name: str) -> str:
    source = _source(path)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_stock_ledger_update_source_respects_existing_varchar_50_contract():
    helper = _function_source(LEDGER_PATH, "_ledger_update_source")
    mutation = _function_source(LEDGER_PATH, "mutate_warehouse_stock_from_order_line")

    assert "[:50]" in helper
    assert "update_source=_ledger_update_source(source)" in mutation
    assert 'reason=f"{source}: marketplace order updated grouped warehouse stock"' in mutation


def test_mcf_page_refreshes_from_existing_event_stream_without_polling():
    source = _source(MCF_TEMPLATE)

    assert "new EventSource('/governed/ui/events/stream')" in source
    assert "eventSource.addEventListener('marketplace'" in source
    assert "refreshMcfTable" in source
    assert "X-BT38-UI-Refresh': 'mcf-committed-state'" in source
    assert "setInterval(" not in source


def test_mcf_refresh_is_database_page_read_only():
    source = _source(MCF_TEMPLATE)

    assert "fetch(window.location.href" in source
    assert "cache: 'no-store'" in source
    assert "run_governed_marketplace_import_refresh" not in source
    assert "run_governed_marketplace_order_import" not in source
