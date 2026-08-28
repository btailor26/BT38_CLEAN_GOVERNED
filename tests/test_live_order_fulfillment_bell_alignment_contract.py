from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_bell_uses_persisted_fba_sfp_fbm_truth_without_marketplace_reads():
    source = _text("services/governed_notification_read_alignment.py")

    assert 'fulfillment in {"FBA", "AFN"}' in source
    assert 'profile_channel in {"FBA", "AFN"}' in source
    assert 'getattr(profile, "is_prime", None) is True' in source
    assert 'return "SFP"' in source
    assert 'return "FBM"' in source
    assert 'FBMOrderProfile' in source
    assert 'fulfillment_mode' in source
    assert 'fulfillment_label = "Prime" if fulfillment_mode == "SFP"' in source
    assert 'platform_display = f"{marketplace} · {fulfillment_label}"' in source
    assert 'requests.' not in source
    assert 'get_or_refresh_amazon_profile' not in source
    assert 'hydrate_exact_ebay_order' not in source


def test_live_ebay_packlink_and_prime_history_alignment_remain_installed():
    compat = _text("services/governed_mcf_compat.py")
    live_feed = _text("services/fbm_live_feed_alignment.py")
    prime_feed = _text("services/fbm_prime_feed_alignment.py")
    updates = _text("services/fbm_marketplace_order_update_alignment.py")

    assert "services.fbm_live_feed_alignment" in compat
    assert "hydrate_exact_ebay_order" in live_feed
    assert "packlink" in live_feed.lower()
    assert "get_or_refresh_amazon_profile" in prime_feed
    assert "is_prime" in prime_feed
    assert "refresh_amazon_prime_profiles" in updates


def test_prime_classifier_never_promotes_premium_service_names_to_prime():
    profile = _text("services/fbm_amazon_order_profile.py")

    assert 'raw_is_prime = _bool(payload.get("IsPrime"))' in profile
    assert "if raw_is_prime is not None:" in profile
    assert "return bool(raw_is_prime)" in profile
