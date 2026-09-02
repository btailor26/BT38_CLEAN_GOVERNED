from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EBAY = (ROOT / "services" / "governed_ebay_packlink_confirmation_alignment.py").read_text(encoding="utf-8")
CONFIRM = (ROOT / "services" / "fbm_marketplace_confirmation.py").read_text(encoding="utf-8")


def test_ebay_external_dispatch_does_not_use_amazon_mapping_gate():
    assert 'marketplace != "ebay" or shipment is None' in EBAY
    assert 'if platform != "ebay":' in EBAY
    assert 'mapping_required_for_confirmation": False' in EBAY
    assert 'provider != "packlink"' not in EBAY
    assert 'provider != "packlink"' not in EBAY
    assert "_confirm_ebay_external" in EBAY
    assert "tracking_required" in EBAY


def test_amazon_keeps_verified_carrier_service_mapping_gate():
    assert 'mapping.verification_status != "verified"' in CONFIRM
    assert '"mapping_under_review"' in CONFIRM
    assert '"amazon_vtr_mapping_blocked"' in CONFIRM
    assert 'carrierCode": carrier_code' in CONFIRM
    assert 'shippingMethod": shipping_method' in CONFIRM
    assert 'trackingNumber": tracking' in CONFIRM
