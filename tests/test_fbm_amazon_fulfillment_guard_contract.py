from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUARD = (ROOT / "services" / "governed_fbm_fulfillment_guard.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")


def test_amazon_fbm_guard_requires_positive_persisted_profile_truth():
    assert 'platform != "amazon"' in GUARD
    assert "_is_fbm_eligible(row)" in GUARD
    assert 'profile_channel in {"MFN", "FBM"}' in GUARD
    assert 'fulfillment_type' not in GUARD
    assert '"AFN"' not in GUARD
    assert '"FBA"' not in GUARD
    assert '"MCF"' not in GUARD


def test_guard_is_read_only_and_installed_before_bounded_page_binding():
    assert "db.session" not in GUARD
    assert "requests." not in GUARD
    assert "get_or_refresh_amazon_profile" not in GUARD
    assert "install_governed_fbm_fulfillment_guard()" in INSTALLER
