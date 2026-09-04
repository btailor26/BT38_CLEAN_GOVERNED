from pathlib import Path

import services.governed_amazon_fbm_profile_event_alignment as alignment


def test_prime_program_is_exact_prime_truth():
    payload = {"Payload": {"OrderChangeNotification": {"OrderPrograms": ["Prime", "Premium"]}}}
    assert alignment._prime(payload) is True


def test_non_prime_program_does_not_invent_prime():
    payload = {"Payload": {"OrderChangeNotification": {"OrderPrograms": ["Premium"]}}}
    assert alignment._prime(payload) is None


def test_event_alignment_reuses_existing_profile_and_operational_state():
    source = Path(alignment.__file__).read_text(encoding="utf-8")
    assert "FBMOrderProfile" in source
    assert "fbm_order_operational_state" in source
    assert "ON CONFLICT (store_id, marketplace_order_id)" in source
    assert "LatestShipDate" in source
    assert "EarliestDeliveryDate" in source
    assert "LatestDeliveryDate" in source
    assert "OrderPrograms" in source


def test_event_alignment_is_not_a_page_poller_or_marketplace_scan():
    source = Path(alignment.__file__).read_text(encoding="utf-8")
    assert 'request.path.rstrip("/") != "/governed/webhooks/amazon"' in source
    assert "get_orders" not in source
    assert "get_order(" not in source
    assert "requests.get" not in source
    assert "information_schema" not in source
