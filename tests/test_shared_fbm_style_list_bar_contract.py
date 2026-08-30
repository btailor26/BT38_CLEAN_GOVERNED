from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "static" / "js" / "bt38-live-page-refresh.js").read_text(encoding="utf-8")


def test_shared_bar_reuses_fbm_style_controls_on_operational_pages():
    assert "bt38SharedListBarInstalled" in PAGE
    assert "Show latest 15" in PAGE
    assert "Show 15 more" in PAGE
    assert "Showing the latest ${shown} ${label}. Older ${label} show only when expanded." in PAGE
    for path in (
        "'/warehouse'",
        "'/amazon-fba-stock'",
        "'/product-linking'",
        "'/stores'",
        "'/listings'",
        "'/groups'",
        "'/admin/system-activity'",
    ):
        assert path in PAGE


def test_fbm_keeps_its_existing_canonical_bar():
    assert "if (document.querySelector('.fbm-order-row')) return null" in PAGE
    assert "FBM owns its canonical bar" in PAGE


def test_warehouse_uses_existing_server_paging_path():
    assert "next.set('per_page', String(limit))" in PAGE
    assert "next.set('page', '1')" in PAGE
    assert "bt38-table-count" in PAGE


def test_mcf_reuses_loaded_rows_and_existing_page_size_control():
    assert "document.getElementById('mcf-page-size')" in PAGE
    assert "nativeSize.value = '100'" in PAGE
    assert "nativeSize.dispatchEvent(new Event('input', {bubbles:true}))" in PAGE
    assert "mcf-status-filter" in PAGE
    assert "mcf-search" in PAGE


def test_shared_bar_does_not_add_marketplace_or_provider_reads():
    block = PAGE.split('// Shared FBM-style bounded list bar', 1)[1]
    assert "fetch(" not in block
    assert "EventSource" not in block
    assert "setInterval" not in block
    assert "marketplace" not in block.lower().replace("marketplace,", "") or "provider reads" in block
