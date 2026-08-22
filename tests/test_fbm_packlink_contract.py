from types import SimpleNamespace

import pytest

import services.fbm_packlink_adapter as packlink_module
from services.fbm_carrier_mapping import _canonical_amazon_packlink_names
from services.fbm_packlink_adapter import PacklinkAdapter, PacklinkRequestError
from services.fbm_packlink_callback import _tracking_code, extract_packlink_tracking


def _destination():
    return {
        "name": "Test Customer",
        "address1": "2 Test Road",
        "city": "London",
        "postcode": "SW1A 1AA",
        "country": "GB",
        "email": None,
        "phone": "02070000000",
    }


def _sender():
    return {
        "name": "B & T Outlet",
        "company": "B & T OUTLET LTD",
        "address1": "1 Test Street",
        "city": "Leicester",
        "region": "Leicestershire",
        "postcode": "LE1 1AA",
        "country": "United Kingdom",
        "email": "sender@example.test",
        "phone": "01160000000",
    }


def _packlink_handoff_get(endpoint, *, query=None):
    if endpoint == "clients":
        return {"id": 7, "client_id": 9, "country": "GB"}
    if endpoint == "locations/postalzones/destinations":
        return [{"id": 826, "iso_code": "GB", "name": "United Kingdom"}]
    if endpoint == "locations/postalcodes":
        postcode = str((query or {}).get("q") or "").upper()
        return [{"id": "pc_" + postcode.replace(" ", "").lower(), "zipcode": postcode, "postal_zone_id": 826}]
    raise AssertionError(f"Unexpected Packlink GET before shipment POST: {endpoint}")


def test_packlink_connection_uses_api_key_verification_endpoint(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    called = []
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint, query=None: called.append((endpoint, query)) or {"token": "test-key"})
    result = adapter.connection_check()
    assert result.ok is True
    assert result.configured is True
    assert called == [("users/api/keys", None)]


def test_packlink_draft_uses_proven_direct_handoff(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-123")
    line = SimpleNamespace(quantity=2, sku="SKU-1", title="OxyLife Bleach 27G", unit_price=4.50)
    monkeypatch.setattr(packlink_module, "ship_from", _sender)
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: _destination())
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])

    provider_gets = []
    def fake_get(endpoint, *, query=None):
        provider_gets.append((endpoint, query))
        return _packlink_handoff_get(endpoint, query=query)
    monkeypatch.setattr(adapter, "_get_json", fake_get)

    posted = {}
    monkeypatch.setattr(
        adapter,
        "_post_json",
        lambda endpoint, body: posted.update({"endpoint": endpoint, "body": body}) or {"shipment_reference": "UN2026PRO0009999999"},
    )

    result = adapter.create_shipment_draft(
        order=order,
        parcel={"weight_kg": 1.25, "width_cm": 20, "height_cm": 10, "length_cm": 30},
        rate={"service_id": 20149, "carrier_name": "Evri", "service_name": "ParcelShop Parcel"},
    )

    body = posted["body"]
    assert provider_gets[0] == ("clients", None)
    assert [call[0] for call in provider_gets].count("locations/postalzones/destinations") == 2
    assert [call[0] for call in provider_gets].count("locations/postalcodes") == 2
    assert posted["endpoint"] == "shipments"
    assert body["user_id"] == 7
    assert body["client_id"] == 9
    assert body["platform"] == "PRO"
    assert body["platform_country"] == "GB"
    assert body["source"] == "bt38"
    assert body["service_id"] == 20149
    assert body["shipment_custom_reference"] == "AMAZON-123"
    assert body["from"]["company"] == "B & T OUTLET LTD"
    assert body["from"]["country"] == "GB"
    assert body["from"]["zip_code"] == "LE1 1AA"
    assert body["from"]["city"] == "Leicester"
    assert body["to"]["country"] == "GB"
    assert body["to"]["zip_code"] == "SW1A 1AA"
    assert body["to"]["city"] == "London"
    assert body["packages"] == [{"width": 20, "height": 10, "length": 30, "weight": 1.25}]
    assert body["content"] == "2 SKU-1"
    assert body["additional_data"]["postal_zone_id_from"] == 826
    assert body["additional_data"]["zip_code_id_from"] == "pc_le11aa"
    assert body["additional_data"]["postal_zone_id_to"] == 826
    assert body["additional_data"]["zip_code_id_to"] == "pc_sw1a1aa"
    assert result["reference"] == "UN2026PRO0009999999"


def test_packlink_draft_accepts_reference_field(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-124")
    line = SimpleNamespace(quantity=1, sku="SKU-ONLY", unit_price=2.00)
    monkeypatch.setattr(packlink_module, "ship_from", _sender)
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: _destination())
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    monkeypatch.setattr(adapter, "_get_json", _packlink_handoff_get)
    posted = {}
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: posted.update({"body": body}) or {"reference": "UN2026PRO0009999998"})

    result = adapter.create_shipment_draft(
        order=order,
        parcel={"weight_kg": 1, "width_cm": 10, "height_cm": 10, "length_cm": 10},
        rate={"service_id": 20149},
    )
    assert posted["body"]["content"] == "1 SKU-ONLY"
    assert posted["body"]["additional_data"]["postal_zone_id_to"] == 826
    assert posted["body"]["additional_data"]["zip_code_id_to"] == "pc_sw1a1aa"
    assert result["reference"] == "UN2026PRO0009999998"


def test_packlink_draft_requires_destination_phone(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    destination = _destination()
    destination["phone"] = None
    monkeypatch.setattr(packlink_module, "ship_from", _sender)
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: destination)
    with pytest.raises(Exception, match="Destination phone is missing"):
        adapter.create_shipment_draft(order=SimpleNamespace(marketplace_order_id="AMAZON-123"), parcel={"weight_kg":1,"width_cm":20,"height_cm":10,"length_cm":30}, rate={"service_id":20149})


def test_packlink_rates_require_destination_phone_before_provider_call(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    destination = _destination()
    destination["phone"] = None
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: destination)
    called = []
    monkeypatch.setattr(adapter, "_get_json", lambda *args, **kwargs: called.append((args, kwargs)))
    with pytest.raises(Exception, match="Missing Packlink destination fields: phone"):
        adapter.get_rates(order=SimpleNamespace(), parcel={"from_country":"GB","from_zip":"LE1 3WU","to_country":"GB","to_zip":"SW1A 1AA","width_cm":20,"height_cm":10,"length_cm":30,"weight_kg":1})
    assert called == []


def test_packlink_tracking_unwraps_history(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint, **_: {"history": [{"city":"LEICESTER","description":"IN TRANSIT","timestamp":1}]})
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
    rate = PacklinkAdapter._normalise_rate({"id":20154,"carrier_name":"DPD","name":"Classic","currency":"GBP","price":{"base_price":3.00,"tax_price":0.60,"total_price":3.60,"currency":"GBP"},"transit_time":"24h"})
    assert rate["service_id"] == 20154
    assert rate["carrier_name"] == "DPD"
    assert rate["service_name"] == "Classic"
    assert rate["price"]["value"] == 3.60


def test_packlink_shipment_normalises_carrier_and_service_objects(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint, **_: {"shipment_reference":"GB000123ABC","carrier":{"id":10,"name":"Yodel"},"service":{"id":20,"name":"Xpert"}})
    shipment = adapter.get_shipment("GB000123ABC")
    assert shipment["carrier"] == "Yodel"
    assert shipment["service"] == "Xpert"
    assert shipment["carrier_id"] == 10
    assert shipment["service_id"] == 20


def test_packlink_callback_reads_tracking_codes_array():
    assert _tracking_code({"tracking_codes": ["TRACK-123"]}) == "TRACK-123"


def test_packlink_tracking_contract_prefers_provider_payload():
    assert extract_packlink_tracking({"shipment":{"trackingNumber":"DIRECT-123"}}, [{"tracking_number":"HISTORY-456"}], "SAVED-789") == "DIRECT-123"


def test_packlink_tracking_contract_uses_latest_history_when_payload_has_none():
    assert extract_packlink_tracking({"status":"purchased"}, [{"tracking_code":"EARLY-123"},{"tracking_info":{"tracking":"LATEST-456"}}], "SAVED-789") == "LATEST-456"


def test_packlink_tracking_contract_preserves_saved_tracking_as_last_fallback():
    assert extract_packlink_tracking({}, [], "SAVED-789") == "SAVED-789"


def test_amazon_packlink_rates_are_not_filtered_before_purchase(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(store=SimpleNamespace(platform="Amazon"))
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: _destination())
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint, **_: [
        {"id":1,"carrier_name":"Yodel","name":"Xpert","price":{"total_price":2.50,"currency":"GBP"}},
        {"id":2,"carrier_name":"Evri","name":"2nd Day Drop Off","price":{"total_price":2.40,"currency":"GBP"}},
        {"id":3,"carrier_name":"Another Carrier","name":"Tracked","price":{"total_price":2.20,"currency":"GBP"}},
    ])
    rates = adapter.get_rates(order=order, parcel={"from_country":"GB","from_zip":"LE1 3WU","to_country":"GB","to_zip":"SW1A 1AA","width_cm":20,"height_cm":10,"length_cm":30,"weight_kg":1})
    assert len(rates) == 3
    assert rates[0]["carrier_name"] == "Yodel"
    assert rates[1]["carrier_name"] == "Evri"


def test_amazon_yodel_mapping_uses_account_proven_display_values():
    mapping = SimpleNamespace(marketplace="amazon", provider="packlink", provider_carrier_display="Yodel")
    carrier_name, service_name = _canonical_amazon_packlink_names(mapping, carrier_code="Yodel", carrier_name=None, service_code="Xpert", service_name=None)
    assert carrier_name == "Yodel"
    assert service_name == "Xpert"


def test_amazon_hermes_mapping_uses_exact_amazon_display_values():
    mapping = SimpleNamespace(marketplace="amazon", provider="packlink", provider_carrier_display="Evri")
    carrier_name, service_name = _canonical_amazon_packlink_names(mapping, carrier_code="HERMES_UK", carrier_name=None, service_code="HERMES_UK_MFN_TWODAY_DROPOFF", service_name=None)
    assert carrier_name == "Hermes UK"
    assert service_name == "Hermes Two Day - Drop Off"
