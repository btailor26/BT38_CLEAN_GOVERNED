from pathlib import Path


RUNTIME = Path("services/fbm_operational_ui_alignment.py").read_text(encoding="utf-8")
STATE = Path("services/fbm_operational_state.py").read_text(encoding="utf-8")
SCRIPT = Path("static/js/fbm_operational_alignment_v2.js").read_text(encoding="utf-8")
COMPAT = Path("services/governed_mcf_compat.py").read_text(encoding="utf-8")


def test_fbm_journey_runtime_removes_numbers_and_surfaces_delivery_promise():
    assert "replace(/^\\s*[123]\\s*·\\s*/, '')" in SCRIPT
    assert "'Picked up'" in SCRIPT
    assert "'In transit'" in SCRIPT
    assert "'Delivered'" in SCRIPT
    assert "'Delivered late'" in SCRIPT
    assert "'Delayed'" in SCRIPT
    assert "Promise: ${promise.label}" in SCRIPT
    assert "bg-danger" in SCRIPT


def test_prime_orders_show_prime_mark_and_hide_non_amazon_purchase_routes():
    assert "bt38-prime-mark" in SCRIPT
    assert "Seller Fulfilled Prime" in SCRIPT
    assert "amazon_buy_shipping" in SCRIPT
    assert "button.closest('.border.rounded.p-3.mb-2')?.remove()" in SCRIPT


def test_order_parcel_values_have_order_level_persistence_independent_of_sku_defaults():
    assert "class FBMOrderOperationalState" in STATE
    assert "parcel = db.Column(db.JSON" in STATE
    assert "def save_order_parcel" in STATE
    assert "/governed/fbm/orders/<int:order_id>/parcel" in RUNTIME
    assert "save_order_parcel(order, allowed)" in RUNTIME
    assert "apply_parcel_overrides(parcel_from_db(order), allowed)" in RUNTIME
    assert "Saving…" in SCRIPT
    assert "state.textContent = 'Saved'" in SCRIPT


def test_operational_alignment_is_installed_without_marketplace_write_or_postage_purchase():
    assert "install_fbm_operational_ui_alignment(app)" in COMPAT
    assert "/governed/fbm/orders/<int:order_id>/operational" in RUNTIME
    assert "get_amazon_delivery_promise" in RUNTIME
    assert "hydrate_exact_ebay_order" in RUNTIME
    assert "buy_postage" not in RUNTIME.lower()
    assert "purchase_shipment" not in RUNTIME
