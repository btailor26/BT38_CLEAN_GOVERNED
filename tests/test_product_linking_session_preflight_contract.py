from pathlib import Path


PREFLIGHT = Path("static/js/product-linking-session-preflight.js").read_text(
    encoding="utf-8"
)
GLOBAL_STATE = Path("static/js/bt38-global-state.js").read_text(
    encoding="utf-8"
)


def test_product_linking_uses_guarded_preflight_before_core_session():
    assert "product-linking-session-preflight.js" in GLOBAL_STATE
    assert "product-linking-session.js?v=product-linking-session-verified-link" not in GLOBAL_STATE
    assert "product-linking-session.js?v=current-relationship-session-v7" in PREFLIGHT


def test_stale_relationship_snapshot_is_deleted_before_session_load():
    assert 'OLD_CACHE_KEY = "product-linking-v4"' in PREFLIGHT
    assert ".delete(OLD_CACHE_KEY)" in PREFLIGHT
    assert ".finally(loadSessionController)" in PREFLIGHT
    assert 'REVISION = "current-relationship-session-v7"' in PREFLIGHT


def test_product_linking_stock_number_is_display_only():
    assert "td:nth-child(2) button" in PREFLIGHT
    assert "stopImmediatePropagation" in PREFLIGHT
    assert "stockQuantityReadOnlyHere: true" in PREFLIGHT


def test_preflight_does_not_create_marketplace_or_quantity_writer():
    assert "/governed/actions/groups/" not in PREFLIGHT
    assert "quantity:" not in PREFLIGHT
    assert "adjustPushQuantity" not in PREFLIGHT
