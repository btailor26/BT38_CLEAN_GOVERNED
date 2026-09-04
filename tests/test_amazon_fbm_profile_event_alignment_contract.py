from pathlib import Path

import services.governed_amazon_fbm_profile_event_alignment as alignment


def test_prime_program_is_exact_prime_truth():
    payload = {"Payload": {"OrderChangeNotification": {"OrderPrograms": ["Prime", "Premium"]}}}
    assert alignment._prime(payload) is True


def test_prime_program_is_found_across_exact_notification_occurrences():
    payload = {
        "Summary": {"OrderPrograms": ["Premium"]},
        "OrderChangeNotification": {"OrderPrograms": ["Prime", "Premium"]},
    }
    assert alignment._prime(payload) is True
    assert alignment._program_names(payload) == {"prime", "premium"}


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
    assert "_program_names(payload)" in source


def test_prime_badge_remains_driven_by_persisted_profile_truth():
    template = Path("templates/fbm.html").read_text(encoding="utf-8")
    routes = Path("governed_fbm_routes.py").read_text(encoding="utf-8")
    assert "{% if shipping.prime_locked %}" in template
    assert 'is_prime = bool(profile and profile.is_prime is True)' in routes
    assert '"prime_locked": is_prime' in routes


def test_event_alignment_is_not_a_page_poller_or_marketplace_scan():
    source = Path(alignment.__file__).read_text(encoding="utf-8")
    assert 'request.path.rstrip("/") != "/governed/webhooks/amazon"' in source
    assert "get_orders" not in source
    assert "get_order(" not in source
    assert "requests.get" not in source
    assert "information_schema" not in source


def test_missing_historical_profile_repair_is_bounded_once_and_exact():
    source = Path(alignment.__file__).read_text(encoding="utf-8")
    assert "_backfill_started" in source
    assert "_start_missing_profile_repair_once(app)" in source
    assert "NOW() - INTERVAL '48 hours'" in source
    assert "LIMIT :limit" in source
    assert "min(int(limit), 25)" in source
    assert "get_or_refresh_amazon_profile(order, force=True)" in source
    assert "fbm_order_profiles AS fp" in source
    assert "fbm_order_operational_state AS ops" in source
    assert "fp.is_prime IS NULL" in source
    assert "ops.ship_by_at IS NULL" in source
    assert "ops.latest_delivery_at IS NULL" in source
    assert "NOT IN ('FBA','AFN','AMAZON')" in source


def test_existing_exact_profile_read_persists_all_promise_fields():
    small = Path("services/governed_fbm_small_alignment.py").read_text(encoding="utf-8")
    assert "original_fetch = amazon_profile._fetch_order" in small
    assert "payload, address_payload = original_fetch(store, order_id)" in small
    assert 'payload.get("LatestShipDate")' in small
    assert 'payload.get("EarliestDeliveryDate")' in small
    assert 'payload.get("LatestDeliveryDate")' in small
    assert "INSERT INTO fbm_order_operational_state" in small
