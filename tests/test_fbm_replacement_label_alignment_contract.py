from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL = (ROOT / "fbm_models.py").read_text(encoding="utf-8")
ALIGNMENT = (ROOT / "services" / "governed_fbm_replacement_label_alignment.py").read_text(encoding="utf-8")
SCRIPT = (ROOT / "static" / "js" / "fbm_replacement_label_alignment.js").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def test_replacement_reason_is_persisted_on_existing_fbm_shipment_authority():
    assert "replacement_reason_code" in MODEL
    assert "replacement_reason = db.Column(db.Text" in MODEL
    assert "replacement_reason_recorded_at" in MODEL
    assert "replacement_reason_recorded_by" in MODEL
    assert "FBMShipment" in ALIGNMENT
    assert "ALTER TABLE fbm_shipments" in ALIGNMENT
    assert "CREATE TABLE" not in ALIGNMENT


def test_replacement_label_requires_dispatched_original_and_explicit_reason():
    assert "Replacement labels are only available after the original order has been dispatched" in ALIGNMENT
    assert "_completed_original_exists" in ALIGNMENT
    assert "shipment_purpose" in ALIGNMENT
    assert 'purpose != "replacement"' in ALIGNMENT
    assert "replacement_reason_code" in ALIGNMENT
    assert "State the reason for this replacement label purchase" in ALIGNMENT
    assert "REPLACEMENT_REASON_CODES" in ALIGNMENT


def test_replacement_reuses_existing_packlink_path_without_background_purchase():
    assert 'endpoint = "governed_fbm.packlink_create_draft"' in ALIGNMENT
    assert "return current(*args, **kwargs)" in ALIGNMENT
    assert "PacklinkAdapter" not in ALIGNMENT
    assert "AmazonShippingAdapter" not in ALIGNMENT
    assert "purchase_shipment" not in ALIGNMENT


def test_dispatched_workspace_exposes_replacement_label_and_reason_capture():
    assert "Replacement label" in SCRIPT
    assert "bt38-replacement-label" in SCRIPT
    assert "lifecycleStatus" in SCRIPT
    assert "Why is another label being purchased?" in SCRIPT
    assert "label_damaged" in SCRIPT
    assert "parcel_damaged" in SCRIPT
    assert "customer_replacement" in SCRIPT
    assert "shipment_purpose = 'replacement'" in SCRIPT
    assert "confirm_additional_shipment = 'CONFIRM_REPLACEMENT'" in SCRIPT


def test_amazon_native_second_purchase_is_not_faked_for_dispatched_replacement():
    assert "amazon_buy_shipping" in SCRIPT
    assert "Amazon native replacement unavailable" in SCRIPT
    assert "Choose an eligible external carrier" in SCRIPT


def test_replacement_alignment_is_installed_in_governed_runtime():
    assert "install_governed_fbm_replacement_label_alignment" in MAIN
    assert "existing Packlink/FBMShipment path" in MAIN
