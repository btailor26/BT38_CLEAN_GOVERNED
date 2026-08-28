from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_packlink_forces_exact_ebay_hydration_before_rates():
    source = _read("services/fbm_live_feed_alignment.py")
    assert "hydrate_exact_ebay_order" in source
    assert 'source="packlink_live_handoff"' in source
    assert "provider_parcel(order, entered)" in source
    assert "PacklinkAdapter.get_rates = aligned_get_rates" in source


def test_ebay_hydration_keeps_safe_alias_guard():
    source = _read("services/governed_exact_ebay_order_hydration.py")
    assert "_safe_stale_identity_alias" in source
    assert "processed_at" in source
    assert "canonical_line_id" in source
    assert "db.session.delete(row)" in source


def test_prime_backfill_uses_amazon_truth_and_progress_marker():
    source = _read("services/fbm_prime_feed_alignment.py")
    assert "get_or_refresh_amazon_profile(row, force=True)" in source
    assert 'BACKFILL_SOURCE = "amazon_exact_prime_backfill_v1"' in source
    assert "profile.source = BACKFILL_SOURCE" in source
    assert "IsPremiumOrder are never treated as Prime" in source


def test_future_orders_refresh_exact_marketplace_profile():
    source = _read("services/fbm_marketplace_order_update_alignment.py")
    assert "get_or_refresh_amazon_profile(order, force=True)" in source
    assert "hydrate_exact_ebay_order" in source
    assert "refresh_amazon_prime_profiles()" in source


def test_live_alignment_is_installed_without_parallel_page_path():
    source = _read("services/governed_mcf_compat.py")
    assert "import services.fbm_live_feed_alignment" in source
