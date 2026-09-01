from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sds_reference_is_derived_from_persisted_shipment_not_marketplace_order():
    source = (ROOT / "services" / "governed_sds_dispatch_alignment.py").read_text(encoding="utf-8")
    assert 'sds_reference = f"SDS-{shipment.id:010d}"' in source
    assert "shipment.provider_shipment_id = sds_reference" in source
    assert "shipment.label_storage_ref = sds_reference" in source
    assert 'label_storage_ref=f"SDS-{order.store_id}-{order.marketplace_order_id}"' not in source


def test_sds_scan_requires_exact_persisted_parcel_reference():
    source = (ROOT / "services" / "governed_sds_scan_alignment.py").read_text(encoding="utf-8")
    assert 'body.get("sds_reference")' in source
    assert "shipment.provider_shipment_id" in source
    assert "scanned_reference != persisted_reference" in source
    assert '"Scanned SDS parcel reference does not match this shipment."' in source


def test_sds_reference_is_returned_for_selection_and_scan_results():
    dispatch = (ROOT / "services" / "governed_sds_dispatch_alignment.py").read_text(encoding="utf-8")
    scans = (ROOT / "services" / "governed_sds_scan_alignment.py").read_text(encoding="utf-8")
    assert '"sds_reference": sds_reference' in dispatch
    assert '"sds_reference": shipment.provider_shipment_id' in scans
