from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_ebay_shipping_label_finance_alignment.py").read_text(encoding="utf-8")
SERVICES_INIT = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")


def test_exact_ebay_hydration_hooks_finance_only_after_shipment_truth():
    assert "_ORIGINAL_HYDRATE" in ALIGNMENT
    assert 'int(result.get("fulfillment_lifecycle_rows") or 0) > 0' in ALIGNMENT
    assert "read_and_persist_exact_ebay_shipping_label_purchase" in ALIGNMENT
    assert 'result["shipping_label_finance"] = finance_result' in ALIGNMENT


def test_alignment_reuses_existing_exact_hydration_and_is_zero_polling():
    assert "poll" not in ALIGNMENT.lower().replace("zero-polling", "")
    assert "worker" not in ALIGNMENT.lower()
    assert "FBMShipment(" not in ALIGNMENT
    assert "requests.get(" not in ALIGNMENT
    assert "requests.post(" not in ALIGNMENT
    assert "governed_ebay_shipping_label_finance_alignment" in SERVICES_INIT
