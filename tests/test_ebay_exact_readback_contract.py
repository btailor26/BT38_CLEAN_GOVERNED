from pathlib import Path


SOURCE = Path("marketplace_adapters/ebay.py").read_text(encoding="utf-8")


def test_single_listing_readback_uses_get_item_quantity_less_quantity_sold():
    start = SOURCE.index("if variations:")
    end = SOURCE.index("if observed_quantity is not None:", start)
    block = SOURCE[start:end]

    assert '_xml_text(detail, "{*}QuantityAvailable"' not in block
    assert '_xml_text(detail, "{*}Quantity", "0")' in block
    assert '"{*}SellingStatus/{*}QuantitySold"' in block
    assert "listed_quantity - sold_quantity" in block


def test_exact_readback_still_requires_the_observed_quantity_to_match():
    assert "int(observed_quantity) == int(quantity or 0)" in SOURCE
    assert '"readback_verified": readback_verified' in SOURCE
    assert '"write_acknowledged": write_acknowledged' in SOURCE
