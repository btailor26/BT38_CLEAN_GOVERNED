from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sds_dispatch_requires_explicit_selection_and_rechecks_eligibility():
    source = (ROOT / "services" / "governed_sds_dispatch_alignment.py").read_text(encoding="utf-8")
    assert 'body.get("confirm_selection") != "SELECT_SDS"' in source
    assert "sds_for_fbm_order(order, prime_sfp=prime_sfp)" in source
    assert 'if not eligibility.get("eligible")' in source
    assert '"Amazon shipping profile could not be verified; SDS remains blocked."' in source


def test_sds_dispatch_persists_one_physical_shipment_before_later_events():
    source = (ROOT / "services" / "governed_sds_dispatch_alignment.py").read_text(encoding="utf-8")
    assert 'purchase_key = f"sds:{order.store_id}:{order.marketplace_order_id}"' in source
    assert 'provider="sds"' in source
    assert 'purchase_status="selected"' in source
    assert 'status="awaiting_seller_handover"' in source
    assert "tracking_number=" not in source
    assert "marketplace_confirmed_at" not in source
    assert "guard_marketplace_write" not in source


def test_sds_dispatch_cost_is_actual_and_never_implicit_zero():
    source = (ROOT / "services" / "governed_sds_dispatch_alignment.py").read_text(encoding="utf-8")
    assert 'mode == "flat"' in source
    assert 'mode == "per_mile"' in source
    assert 'mode == "manual"' in source
    assert 'body.get("actual_cost")' in source
    assert '"Actual SDS dispatch cost is required for manual cost mode."' in source
    assert 'row.provider = "sds"' in source
    assert "row.confirmed = True" in source
    assert "amount = 0" not in source


def test_sds_dispatch_is_installed_after_read_eligibility():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "install_governed_sds_fbm_read_alignment()" in source
    assert "install_governed_sds_dispatch_alignment(app)" in source
    assert source.index("install_governed_sds_fbm_read_alignment()") < source.index("install_governed_sds_dispatch_alignment(app)")
