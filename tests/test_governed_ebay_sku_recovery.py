import xml.etree.ElementTree as ET

from services.governed_ebay_sku_recovery import generated_sku, variation_identity


def _variation(style: str, sku: str | None = None):
    xml = f'''<Variation xmlns="urn:ebay:apis:eBLBaseComponents">
      {f'<SKU>{sku}</SKU>' if sku else ''}
      <Quantity>1</Quantity>
      <StartPrice>4.99</StartPrice>
      <VariationSpecifics>
        <NameValueList><Name>Style</Name><Value>{style}</Value></NameValueList>
      </VariationSpecifics>
    </Variation>'''
    return ET.fromstring(xml)


def test_single_generated_sku_is_stable_and_bounded():
    first = generated_sku("116261042884")
    second = generated_sku("116261042884")
    assert first == second
    assert first == "BT38-EB-116261042884"
    assert len(first) <= 50


def test_variation_generated_sku_is_stable_per_exact_variation():
    style_one = _variation("1")
    same_style_one = _variation("1")
    style_two = _variation("2")

    first = generated_sku("116261042884", style_one)
    repeated = generated_sku("116261042884", same_style_one)
    different = generated_sku("116261042884", style_two)

    assert first == repeated
    assert first != different
    assert first.startswith("BT38-EB-116261042884-")
    assert len(first) <= 50


def test_variation_identity_ignores_existing_seller_sku():
    without_sku = _variation("1")
    with_sku = _variation("1", "SELLER-SKU")
    assert variation_identity(without_sku) == variation_identity(with_sku)
