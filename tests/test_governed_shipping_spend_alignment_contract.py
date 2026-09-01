from pathlib import Path


def test_shipping_spend_alignment_stays_on_existing_purchase_paths():
    source = Path("services/governed_shipping_spend_alignment.py").read_text(encoding="utf-8")
    assert "AmazonShippingAdapter.purchase_shipment" in source
    assert "ebay._create_shipment" in source
    assert '"governed_fbm.amazon_purchase"' in source
    assert '"bt38_ebay_native_shipping_purchase"' in source
    assert "result.get(\"total_charge\")" in source
    assert "result.get(\"totalShippingCost\")" in source
    assert "selected.get(\"price\")" not in source


def test_spend_ledger_is_one_record_per_dispatch_not_per_unit():
    source = Path("shipping_spend_models.py").read_text(encoding="utf-8")
    assert "dispatch_key" in source
    assert "unique=True" in source
    assert "quantity" not in source


def test_missing_marketplace_cost_is_not_written_as_zero():
    source = Path("services/governed_shipping_spend_alignment.py").read_text(encoding="utf-8")
    assert "if spend.confirmed and spend.amount is not None" in source
    assert "amount = 0" not in source
