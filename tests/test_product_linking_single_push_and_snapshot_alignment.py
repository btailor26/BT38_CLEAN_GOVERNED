from pathlib import Path


CONTROLLER = Path("static/js/bt38-page-controller.js").read_text(encoding="utf-8")
TEMPLATE = Path("templates/product_linking.html").read_text(encoding="utf-8")


def test_only_injected_link_column_push_is_removed():
    assert "tr > td:nth-child(4) .bt38-qty-push-open" in CONTROLLER
    assert "removeInjectedProductLinkingPush" in CONTROLLER
    assert "button.remove()" in CONTROLLER

    # The original right-hand Actions control remains the operative shortcut.
    assert "btn btn-outline-primary bt38-qty-push-open" in TEMPLATE
    assert 'title="Adjust quantity and push grouped marketplaces"' in TEMPLATE


def test_current_group_snapshot_is_invalidated_once_after_alignment_change():
    assert 'SNAPSHOT_ALIGNMENT_REVISION = "current-group-authority-v5"' in CONTROLLER
    assert "bt38InvalidateProductLinkingSnapshot" in CONTROLLER
    assert "localStorage.setItem" in CONTROLLER
    assert "localStorage.removeItem" in CONTROLLER


def test_product_linking_summary_uses_same_browser_relationship_dataset():
    assert "productLinkingProducts()" in CONTROLLER
    assert "productLinkingUnlinked()" in CONTROLLER
    assert "setKpi(0, products.length)" in CONTROLLER
    assert "setKpi(1, unlinked.length)" in CONTROLLER
    assert "setKpi(2, linked)" in CONTROLLER
    assert "setKpi(3, grouped)" in CONTROLLER


def test_long_listing_titles_cannot_push_actions_column_off_screen():
    assert "alignProductLinkingTableLayout" in CONTROLLER
    assert "table.style.tableLayout = 'fixed'" in CONTROLLER
    assert "cells[4].style.overflow = 'hidden'" in CONTROLLER
    assert "cells[5].style.minWidth = '86px'" in CONTROLLER
    assert "cells[5].style.whiteSpace = 'nowrap'" in CONTROLLER
    assert "actionsColumnVisible: true" in CONTROLLER
