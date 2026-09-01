from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
AUTHORITY = (ROOT / "services" / "governed_fbm_db_authority_alignment.py").read_text(encoding="utf-8")


def test_main_installs_single_persisted_fbm_shipment_authority():
    assert "install_governed_fbm_db_authority_alignment" in MAIN
    assert "install_governed_fbm_db_authority_alignment()" in MAIN


def test_authority_reads_exact_persisted_shipment_identity_only():
    assert "FBMShipment" in AUTHORITY
    assert "tuple_(FBMShipment.store_id, FBMShipment.marketplace_order_id).in_(identities)" in AUTHORITY
    assert "exact_tracking_match" in AUTHORITY
    assert "shipment_tracking == persisted_tracking" in AUTHORITY
    assert "page._shipment_map = _canonical_persisted_shipment_map" in AUTHORITY

    # The final FBM shipment selector is DB-only. It must not synthesize a
    # marketplace shipment or call Packlink/Amazon to decide page authority.
    assert "SimpleNamespace" not in AUTHORITY
    assert "_marketplace_proxy" not in AUTHORITY
    assert "PacklinkAdapter" not in AUTHORITY
    assert "AmazonShippingAdapter" not in AUTHORITY
    assert "get_tracking_status" not in AUTHORITY
