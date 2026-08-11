from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "services/governed_operational_table_read_alignment.py").read_text(encoding="utf-8")


def test_fba_table_read_uses_shared_page_sizes_only():
    assert "ALLOWED_PAGE_SIZES = (15, 25, 50, 100)" in SOURCE
    assert 'request.args.get("per_page")' in SOURCE
    assert "per_page=per_page" in SOURCE
    assert "per_page=50" not in SOURCE


def test_fba_alignment_replaces_existing_endpoint_in_place():
    assert 'endpoint = "governed.amazon_fba_stock"' in SOURCE
    assert "app.view_functions[endpoint] = _amazon_fba_stock_page" in SOURCE
    assert '"new_route": False' in SOURCE


def test_fba_alignment_is_read_side_only():
    assert "marketplace writes" in SOURCE
    assert "Warehouse quantity mutation" in SOURCE
    assert "push or sync" in SOURCE
    assert "db.session.commit" not in SOURCE
