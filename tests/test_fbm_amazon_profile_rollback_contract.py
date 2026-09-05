from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE = (ROOT / "services" / "fbm_amazon_order_profile.py").read_text(encoding="utf-8")
LABEL = (ROOT / "services" / "governed_amazon_shipping_label_readback_alignment.py").read_text(encoding="utf-8")
PAGE = (ROOT / "services" / "governed_fbm_amazon_profile_alignment.py").read_text(encoding="utf-8")


def test_tracking_readback_failure_rolls_back_before_profile_persistence():
    tracking_block = PROFILE.split("hydrate_amazon_tracking_for_order(", 1)[1].split("# Re-resolve", 1)[0]
    assert "except Exception:" in tracking_block
    assert "db.session.rollback()" in tracking_block
    assert "Re-resolve after any defensive rollback" in PROFILE
    assert PROFILE.count("FBMOrderProfile.query.filter_by(") >= 2


def test_label_readback_failure_cannot_leave_shared_session_aborted():
    block = LABEL.split("hydrate_amazon_purchased_label_for_order(", 1)[1].split("return profile", 1)[0]
    assert "except Exception:" in block
    assert "db.session.rollback()" in block


def test_page_compensator_rolls_back_every_failed_hydration_unit():
    assert "except AmazonOrderProfileError:" in PAGE
    assert "except Exception:" in PAGE
    assert PAGE.count("db.session.rollback()") >= 2
    assert "take down the FBM desk" in PAGE
