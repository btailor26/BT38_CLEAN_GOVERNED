from pathlib import Path
import ast


SOURCE_PATH = Path("services/governed_amazon_inventory_import.py")


def _source():
    return SOURCE_PATH.read_text(encoding="utf-8")


def _function(tree, name):
    return next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_normal_fba_import_cannot_start_a_full_scan_by_default():
    source = _source()
    tree = ast.parse(source)
    function = _function(tree, "run_governed_amazon_inventory_import")

    keyword_defaults = {
        arg.arg: default
        for arg, default in zip(function.args.kwonlyargs, function.args.kw_defaults)
    }
    assert isinstance(keyword_defaults["full_refresh"], ast.Constant)
    assert keyword_defaults["full_refresh"].value is False

    guard_text = "if inventory_rows is None and not full_refresh"
    adapter_text = "AmazonSPAPIAdapter(store).get_inventory()"
    assert guard_text in source
    assert adapter_text in source
    assert source.index(guard_text) < source.index(adapter_text)
    assert '"full_scan_started": False' in source


def test_fba_event_path_updates_one_identity_without_marketplace_fetch():
    source = _source()
    tree = ast.parse(source)
    function = _function(tree, "apply_governed_amazon_fba_event")
    function_source = ast.get_source_segment(source, function)

    assert "_apply_inventory_row(store, payload)" in function_source
    assert "AmazonSPAPIAdapter" not in function_source
    assert '"rows_received": 1' in function_source
    assert '"rows_updated": 1' in function_source
    assert '"full_scan_started": False' in function_source


def test_full_fba_refresh_requires_explicit_true_flag():
    source = _source()
    assert "if full_refresh:" in source
    assert "rows = AmazonSPAPIAdapter(store).get_inventory()" in source
    assert '"full_scan_started": bool(full_refresh)' in source
