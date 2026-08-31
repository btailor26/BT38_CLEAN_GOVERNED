from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = (ROOT / "services" / "governed_fbm_fulfillment_guard.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")


def test_amazon_fbm_guard_keeps_fba_out_and_scopes_buy_shipping_to_prime():
    assert '_FBA_FULFILLMENT = {"FBA", "AFN", "MCF"}' in GUARD
    assert "_is_amazon_fba_row(row, profile)" in GUARD
    assert "return original_eligible(row, profile)" in GUARD
    assert "_is_prime_sfp(profile)" in GUARD
    assert "lifecycle._amazon_buy_shipping_approved = lambda: True" in GUARD
    assert '"governed_fbm.amazon_rates"' in GUARD
    assert '"governed_fbm.amazon_purchase"' in GUARD
    assert "Prime/SFP" in GUARD


def test_guard_reuses_existing_paths_and_is_installed_with_app():
    assert "db.session" not in GUARD
    assert "requests." not in GUARD
    assert "get_or_refresh_amazon_profile" not in GUARD
    assert "install_governed_fbm_fulfillment_guard(app)" in INSTALLER
