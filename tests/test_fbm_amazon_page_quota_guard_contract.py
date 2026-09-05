from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CURRENT = (ROOT / "services" / "governed_fbm_current_amazon_profile_alignment.py").read_text(encoding="utf-8")
VISIBLE = (ROOT / "services" / "governed_fbm_amazon_profile_alignment.py").read_text(encoding="utf-8")


def test_normal_fbm_page_hydration_is_one_exact_order_only():
    assert "def _hydrate_current_missing_profiles(limit: int = 1)" in CURRENT
    assert ".limit(1)" in CURRENT
    assert "get_or_refresh_amazon_profile(row, force=False)" in CURRENT
    assert "force=True" not in CURRENT


def test_page_attempt_blocks_second_visible_row_marketplace_read():
    assert "g._bt38_fbm_amazon_profile_hydration_checked = True" in CURRENT
    assert '_bt38_fbm_amazon_profile_hydration_checked' in VISIBLE


def test_quota_or_readback_failure_rolls_back_and_does_not_fan_out():
    assert "db.session.rollback()" in CURRENT
    assert "for row in rows:" in CURRENT
    assert "limit=20" not in CURRENT
    assert "min(int(limit), 20)" not in CURRENT
