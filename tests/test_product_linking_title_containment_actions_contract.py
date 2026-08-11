from pathlib import Path


LAYOUT = Path("static/js/product-linking-layout-guard.js").read_text(encoding="utf-8")
GLOBAL_STATE = Path("static/js/bt38-global-state.js").read_text(encoding="utf-8")


def test_layout_guard_loads_before_product_linking_session_preflight():
    layout_pos = GLOBAL_STATE.index("product-linking-layout-guard.js")
    preflight_pos = GLOBAL_STATE.index("product-linking-session-preflight.js")
    assert layout_pos < preflight_pos
    assert "title-containment-actions-v8" in GLOBAL_STATE


def test_linked_listing_content_is_contained_inside_its_cell():
    assert 'tr > td:nth-child(5)' in LAYOUT
    assert "min-width: 0 !important" in LAYOUT
    assert "max-width: 100% !important" in LAYOUT
    assert "overflow: hidden !important" in LAYOUT
    assert "overflow-wrap: anywhere !important" in LAYOUT
    assert "word-break: break-word !important" in LAYOUT
    assert "white-space: normal !important" in LAYOUT


def test_actions_column_is_reserved_and_cannot_be_covered_by_title():
    assert 'tr > td:nth-child(6)' in LAYOUT
    assert "min-width: 110px !important" in LAYOUT
    assert "white-space: nowrap !important" in LAYOUT
    assert "z-index: 3 !important" in LAYOUT
    assert "actionsProtected: true" in LAYOUT


def test_layout_guard_is_product_linking_only():
    assert '[data-bt38-page="productLinking"]' in LAYOUT
    assert "warehouseDataContainer" in LAYOUT
