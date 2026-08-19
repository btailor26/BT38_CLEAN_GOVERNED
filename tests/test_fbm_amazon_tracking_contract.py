from services.fbm_post_purchase import _amazon_tracking_number


def test_amazon_tracking_removes_spaces_and_dashes_only():
    assert _amazon_tracking_number("EVRI-1234-5678") == "EVRI12345678"
    assert _amazon_tracking_number("EVRI 1234 5678") == "EVRI12345678"
    assert _amazon_tracking_number("EVRI–1234—5678") == "EVRI12345678"


def test_amazon_tracking_preserves_courier_letters_and_numbers():
    assert _amazon_tracking_number("UK4685797268") == "UK4685797268"
    assert _amazon_tracking_number("  1Z-999-AA1-01-2345-6784  ") == "1Z999AA10123456784"


def test_amazon_tracking_empty_value_stays_empty():
    assert _amazon_tracking_number(None) is None
    assert _amazon_tracking_number("   ") is None
