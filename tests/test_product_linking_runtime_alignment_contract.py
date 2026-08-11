from pathlib import Path


ALIGNMENT = Path("static/js/product-linking-runtime-alignment.js").read_text(
    encoding="utf-8"
)
BASE = Path("templates/base.html").read_text(encoding="utf-8")


def test_runtime_alignment_is_loaded_after_page_scripts():
    assert "product-linking-runtime-alignment.js" in BASE
    assert "current-membership-push-shortcut-v6" in BASE


def test_product_linking_push_is_direct_warehouse_shortcut_without_quantity():
    assert 'X-BT38-Shortcut": "product_linking_warehouse_shortcut"' in ALIGNMENT
    assert 'warehouse_stock_id: warehouseId' in ALIGNMENT
    assert 'source: "product_linking_warehouse_shortcut"' in ALIGNMENT
    assert "adjustPushQuantity" not in ALIGNMENT
    assert "showAdjustPushModal" not in ALIGNMENT
    assert "stopImmediatePropagation" in ALIGNMENT


def test_group_push_does_not_run_relationship_refresh():
    assert "relationshipRefreshAfterPush: false" in ALIGNMENT
    assert "Do not call bt38ApplyProductLinkingMutation here" in ALIGNMENT
    assert "window.bt38ApplyProductLinkingMutation(" not in ALIGNMENT


def test_current_membership_hides_empty_permanent_shadow_row():
    assert "isEmptyPermanentShadow" in ALIGNMENT
    assert "currentRelationshipProducts" in ALIGNMENT
    assert "listingSku(listing) !== sku" in ALIGNMENT
    assert "!sameId(listingWarehouseId, product?.id)" in ALIGNMENT


def test_auto_push_string_false_is_not_truthy():
    assert "function settingOn(value)" in ALIGNMENT
    assert 'String(value ?? "").trim().toLowerCase()' in ALIGNMENT
    assert 'Auto ${autoOn ? "ON" : "OFF"}' in ALIGNMENT


def test_corrupted_browser_snapshot_is_invalidated_once():
    assert 'SNAPSHOT_REVISION = "current-membership-push-shortcut-v6"' in ALIGNMENT
    assert "bt38InvalidateProductLinkingSnapshot" in ALIGNMENT
    assert "localStorage.setItem" in ALIGNMENT
