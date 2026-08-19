from types import SimpleNamespace

import pytest

import services.fbm_packlink_adapter as packlink_module
from services.fbm_carrier_mapping import _canonical_amazon_packlink_names
from services.fbm_packlink_adapter import (
    PacklinkAdapter,
    PacklinkRequestError,
)
from services.fbm_packlink_callback import _tracking_code, extract_packlink_tracking


def _complete_destination():
    return {
        "name": "Test Customer",
        "address1": "2 Test Road",
        "city": "London",
        "postcode": "SW1A 1AA",
        "country": "GB",
        "email": None,
        "phone": "02070000000",
    }


def test_packlink_connection_uses_api_key_verification_endpoint(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    called = []

    def fake_get(endpoint, *, query=None):
        called.append((endpoint, query))
        return {"token": "test-key"}

    monkeypatch.setattr(adapter, "_get_json", fake_get)

    result = adapter.connection_check()

    assert result.ok is True
    assert result.configured is True
    assert called == [("users/api/keys", None)]


def test_packlink_draft_reads_shipment_reference(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-123")
    line = SimpleNamespace(
        quantity=2,
        sku="SKU-1",
        title="OxyLife Bleach 27G",
        unit_price=4.50,
    )

    monkeypatch.setattr(
        packlink_module,
        "ship_from",
        lambda: {
            "name": "B & T Outlet",
            "company": "B & T OUTLET LTD",
            "address1": "1 Test Street",
            "city": "Leicester",
            "postcode": "LE1 1AA",
            "country": "GB",
            "email": "sender@example.test",
            "phone": "01160000000",
        },
    )
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: _complete_destination())
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])

    posted = {}

    def fake_post(endpoint, body):
        posted["endpoint"] = endpoint
        posted["body"] = body
        return {"shipment_reference": "GB000123ABC"}

    monkeypatch.setattr(adapter, "_post_json", fake_post)

    result = adapter.create_shipment_draft(
        order=order,
        parcel={
            "weight_kg": 1.25,
            "width_cm": 20,
            "height_cm": 10,
            "length_cm": 30,
        },
        rate={"service_id": 20149},
    )

    assert posted["endpoint"] == "shipments"
    assert posted["body"]["service_id"] == 20149
    assert posted["body"]["shipment_custom_reference"] == "AMAZON-123"
    assert posted["body"]["from"]["company"] == "B & T OUTLET LTD"
    assert posted["body"]["content"] == "OxyLife Bleach 27G"
    assert result["reference"] == "GB000123ABC"
    assert result["label_ready"] is False


def test_packlink_draft_content_falls_back_to_sku(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-124")
    line = SimpleNamespace(quantity=1, sku="SKU-ONLY", unit_price=2.00)

    monkeypatch.setattr(
        packlink_module,
        "ship_from",
        lambda: {
            "name": "B & T Outlet",
            "company": "B & T OUTLET LTD",
            "address1": "1 Test Street",
            "city": "Leicester",
            "postcode": "LE1 1AA",
            "country": "GB",
            "email": "sender@example.test",
            "phone": "01160000000",
        },
    )
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: _complete_destination())
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])

    posted = {}
    monkeypatch.setattr(
        adapter,
        "_post_json",
        lambda endpoint, body: posted.update({"endpoint": endpoint, "body": body})
        or {"shipment_reference": "GB000124ABC"},
    )

    adapter.create_shipment_draft(
        order=order,
        parcel={
            "weight_kg": 1.0,
            "width_cm": 10,
            "height_cm": 10,
            "length_cm": 10,
        },
        rate={"service_id": 20149},
    )

    assert posted["body"]["content"] == "SKU-ONLY"


def test_packlink_draft_requires_destination_phone(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-123")

    monkeypatch.setattr(
        packlink_module,
        "ship_from",
        lambda: {
            "name": "B & T Outlet",
            "address1": "1 Test Street",
            "city": "Leicester",
            "postcode": "LE1 1AA",
            "country": "GB",
            "email": "sender@example.test",
            "phone": "01160000000",
        },
    )
    destination = _complete_destination()
    destination["phone"] = None
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: destination)

    with pytest.raises(Exception, match="Destination phone is missing"):
        adapter.create_shipment_draft(
            order=order,
            parcel={
                "weight_kg": 1.25,
                "width_cm": 20,
                "height_cm": 10,
                "length_cm": 30,
            },
            rate={"service_id": 20149},
        )


def test_packlink_rates_require_destination_phone_before_provider_call(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-123")
    destination = _complete_destination()
    destination["phone"] = None
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: destination)

    called = []
    monkeypatch.setattr(adapter, "_get_json", lambda *args, **kwargs: called.append((args, kwargs)))

    with pytest.raises(Exception, match="Missing Packlink destination fields: phone"):
        adapter.get_rates(
            order=order,
            parcel={
                "from_country": "GB",
                "from_zip": "LE1 3WU",
                "to_country": "GB",
                "to_zip": "SW1A 1AA",
                "width_cm": 20,
                "height_cm": 10,
                "length_cm": 30,
                "weight_kg": 1,
            },
        )

    assert called == []


def test_packlink_tracking_unwraps_history(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    monkeypatch.setattr(
        adapter,
        "_get_json",
        lambda endpoint, **_: {
            "history": [
                {"city": "LEICESTER", "description": "IN TRANSIT", "timestamp": 1}
            ]
        },
    )

    history = adapter.get_tracking_status(reference="GB000123ABC")

    assert len(history) == 1
    assert history[0]["description"] == "IN TRANSIT"


def test_packlink_pending_label_404_is_not_a_shipment_failure(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")

    def missing_label(_endpoint, **_kwargs):
        raise PacklinkRequestError("Resource missing", status_code=404)

    monkeypatch.setattr(adapter, "_get_json", missing_label)

    assert adapter.get_labels("GB000123ABC") == []


def test_packlink_rate_uses_total_price_and_carrier_name():
    rate = PacklinkAdapter._normalise_rate(
        {
            "id": 20154,
            "carrier_name": "DPD",
            "name": "Classic",
            "currency": "GBP",
            "price": {
                "base_price": 3.00,
                "tax_price": 0.60,
                "total_price": 3.60,
                "currency": "GBP",
            },
            "transit_time": "24h",
        }
    )

    assert rate["service_id"] == 20154
    assert rate["carrier_name"] == "DPD"
    assert rate["service_name"] == "Classic"
    assert rate["price"]["value"] == 3.60
    assert rate["price"]["unit"] == "GBP"


def test_packlink_shipment_normalises_carrier_and_service_objects(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    monkeypatch.setattr(
        adapter,
        "_get_json",
        lambda endpoint, **_: {
            "shipment_reference": "GB000123ABC",
            "carrier": {"id": 10, "name": "Yodel"},
            "service": {"id": 20, "name": "Xpert"},
        },
    )

    shipment = adapter.get_shipment("GB000123ABC")

    assert shipment["carrier"] == "Yodel"
    assert shipment["service"] == "Xpert"
    assert shipment["carrier_id"] == 10
    assert shipment["service_id"] == 20


def test_packlink_callback_reads_tracking_codes_array():
    assert _tracking_code({"tracking_codes": ["TRACK-123"]}) == "TRACK-123"


def test_packlink_tracking_contract_prefers_provider_payload():
    tracking = extract_packlink_tracking(
        {"shipment": {"trackingNumber": "DIRECT-123"}},
        [{"tracking_number": "HISTORY-456"}],
        "SAVED-789",
    )
    assert tracking == "DIRECT-123"


def test_packlink_tracking_contract_uses_latest_history_when_payload_has_none():
    tracking = extract_packlink_tracking(
        {"status": "purchased"},
        [
            {"tracking_code": "EARLY-123"},
            {"tracking_info": {"tracking": "LATEST-456"}},
        ],
        "SAVED-789",
    )
    assert tracking == "LATEST-456"


def test_packlink_tracking_contract_preserves_saved_tracking_as_last_fallback():
    assert extract_packlink_tracking({}, [], "SAVED-789") == "SAVED-789"


def test_amazon_packlink_rates_are_not_filtered_before_purchase(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(store=SimpleNamespace(platform="Amazon"))
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: _complete_destination())
    monkeypatch.setattr(
        adapter,
        "_get_json",
        lambda endpoint, **_: [
            {
                "id": 1,
                "carrier_name": "Yodel",
                "name": "Xpert",
                "price": {"total_price": 2.50, "currency": "GBP"},
            },
            {
                "id": 2,
                "carrier_name": "Evri",
                "name": "2nd Day Drop Off",
                "price": {"total_price": 2.40, "currency": "GBP"},
            },
            {
                "id": 3,
                "carrier_name": "Another Carrier",
                "name": "Tracked",
                "price": {"total_price": 2.20, "currency": "GBP"},
            },
        ],
    )

    rates = adapter.get_rates(
        order=order,
        parcel={
            "from_country": "GB",
            "from_zip": "LE1 3WU",
            "to_country": "GB",
            "to_zip": "SW1A 1AA",
            "width_cm": 20,
            "height_cm": 10,
            "length_cm": 30,
            "weight_kg": 1,
        },
    )

    assert len(rates) == 3
    assert rates[0]["carrier_name"] == "Yodel"
    assert rates[0]["service_name"] == "Xpert"
    assert rates[1]["carrier_name"] == "Evri"
    assert rates[1]["service_name"] == "2nd Day Drop Off"


def test_amazon_yodel_mapping_uses_account_proven_display_values():
    mapping = SimpleNamespace(
        marketplace="amazon",
        provider="packlink",
        provider_carrier_display="Yodel",
    )

    carrier_name, service_name = _canonical_amazon_packlink_names(
        mapping,
        carrier_code="Yodel",
        carrier_name=None,
        service_code="Xpert",
        service_name=None,
    )

    assert carrier_name == "Yodel"
    assert service_name == "Xpert"


def test_amazon_hermes_mapping_uses_exact_amazon_display_values():
    mapping = SimpleNamespace(
        marketplace="amazon",
        provider="packlink",
        provider_carrier_display="Evri",
    )

    carrier_name, service_name = _canonical_amazon_packlink_names(
        mapping,
        carrier_code="HERMES_UK",
        carrier_name=None,
        service_code="HERMES_UK_MFN_TWODAY_DROPOFF",
        service_name=None,
    )

    assert carrier_name == "Hermes UK"
    assert service_name == "Hermes Two Day - Drop Off"
