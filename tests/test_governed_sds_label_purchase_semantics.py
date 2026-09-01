from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_sds_selection_does_not_claim_a_purchased_postage_label():
    source = (ROOT / "services" / "governed_sds_dispatch_alignment.py").read_text(encoding="utf-8")
    assert 'purchase_status="selected"' in source
    assert "shipment.label_purchased_at =" not in source
    assert "row.recorded_at = datetime.utcnow()" in source


def test_sds_printing_is_read_only_and_does_not_change_purchase_status():
    source = (ROOT / "services" / "governed_sds_label_alignment.py").read_text(encoding="utf-8")
    assert "db.session.commit" not in source
    assert "purchase_status =" not in source
    assert "label_purchased_at" not in source
