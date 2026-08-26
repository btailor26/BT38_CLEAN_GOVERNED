from inspect import getsource
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

import governed_packlink_callback_routes as callback_routes
import services.fbm_packlink_adapter as packlink_module
from services.fbm_packlink_adapter import PacklinkAdapter, PacklinkRequestError
from services.fbm_packlink_callback import _attach_by_marketplace_reference
from services.fbm_packlink_event_processor import process_packlink_event


@pytest.fixture(autouse=True)
def packlink_save_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(
        PacklinkAdapter,
        "_put_json",
        lambda self, endpoint, body: calls.append((endpoint, body)) or {},
    )
    return calls


def _verified_snapshot():
    return {
        "from": {
            "name": "B & T",
            "surname": "Outlet",
            "email": "sender@example.test",
            "phone": "07900000000",
            "street1": "Sender",
            "country": "GB",
            "city": "Leicester",
            "zip_code": "LE1 3WU",
        },
        "to": {
            "name": "Customer",
            "surname": "One",
            "email": "buyer@example.test",
            "phone": "07900000001",
            "street1": "1 Road",
            "country": "GB",
            "city": "London",
            "zip_code": "SW1A 1AA",
        },
    }


def _mock_packlink_handoff(monkeypatch, adapter):
    calls = []

    def fake_get(endpoint, *, query=None):
        calls.append((endpoint, query))
        if endpoint == "clients":
            return {"id": 77, "client_id": 88, "country": "GB"}
        if endpoint == "locations/postalzones/destinations":
            return [{"id": "gb-zone", "isoCode": "GB", "name": "United Kingdom", "hasPostalCodes": True}]
        if endpoint.startswith("locations/postalcodes/"):
            _prefix, country, postcode_part = endpoint.rsplit("/", 2)
            postcode = unquote(postcode_part).upper()
            return {
                "id": "pc_" + postcode.replace(" ", "").lower(),
                "zipcode": postcode,
                "country_code": country.upper(),
            }
        if endpoint.startswith("shipments/"):
            return _verified_snapshot()
        raise AssertionError(f"Unexpected Packlink GET before shipment POST: {endpoint}")

    monkeypatch.setattr(adapter, "_get_json", fake_get)
    return calls


def test_packlink_future_draft_posts_full_marketplace_address_with_location_ids(monkeypatch, packlink_save_calls):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-999")
    line = SimpleNamespace(
        quantity=1,
        sku="AMZ-SKU-1",
        unit_price=0,
        line_total=0,
        warehouse_stock=SimpleNamespace(product_name="Fevicryl Fabric Glue"),
    )

    monkeypatch.setattr(
        packlink_module,
        "ship_from",
        lambda: {
            "name": "B & T OUTLET LTD",
            "company": "B & T OUTLET LTD",
            "address1": "Unit 10, St Mark's Works Foundry Lane",
            "address2": "",
            "city": "Leicester",
            "region": "Leicestershire",
            "postcode": "LE1 3WU",
            "country": "United Kingdom",
            "email": "weeklydeals2014@outlook.com",
            "phone": "07903883892",
        },
    )
    monkeypatch.setattr(
        packlink_module,
        "ship_to",
        lambda _order: {
            "name": "Test Customer",
            "address1": "95 MAXEY ROAD",
            "address2": None,
            "city": "DAGENHAM",
            "region": None,
            "postcode": "RM9 5HU",
            "country": "United Kingdom",
            "email": "buyer@marketplace.amazon.co.uk",
            "phone": "+447900000000",
        },
    )
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    get_calls = _mock_packlink_handoff(monkeypatch, adapter)

    posted = {}
    monkeypatch.setattr(
        adapter,
        "_post_json",
        lambda endpoint, body: posted.update({"endpoint": endpoint, "body": body})
        or {"shipment_reference": "GB000999ABC"},
    )

    result = adapter.create_shipment_draft(
        order=order,
        parcel={"weight_kg": 1, "width_cm": 10, "length_cm": 10, "height_cm": 10},
        rate={"service_id": 21367, "carrier": "Yodel UK", "service": "InPost Shops"},
    )

    body = posted["body"]
    assert posted["endpoint"] == "shipments"
    assert len(packlink_save_calls) == 2
    country_endpoint, country_body = packlink_save_calls[0]
    postcode_endpoint, postcode_body = packlink_save_calls[1]
    assert country_endpoint == "shipments/GB000999ABC"
    assert postcode_endpoint == "shipments/GB000999ABC"
    assert country_body["additional_data"]["postal_zone_id_to"] == "gb-zone"
    assert country_body["additional_data"]["zip_code_id_to"] is None
    assert country_body["to"]["postal_zone_id"] == "gb-zone"
    assert "zip_code_id" not in country_body["to"]
    assert postcode_body["additional_data"]["postal_zone_id_to"] == "gb-zone"
    assert postcode_body["additional_data"]["zip_code_id_to"] == "pc_rm95hu"
    assert postcode_body["to"]["postal_zone_id"] == "gb-zone"
    assert postcode_body["to"]["zip_code_id"] == "pc_rm95hu"
    assert result["reference"] == "GB000999ABC"
    assert result["payment_status"] == "pending_packlink_payment"
    assert result["label_ready"] is False
    assert result["verified"] is True

    assert get_calls[0] == ("clients", None)
    postcode_calls = [endpoint for endpoint, _ in get_calls if endpoint.startswith("locations/postalcodes/")]
    assert postcode_calls == ["locations/postalcodes/GB/LE1%203WU", "locations/postalcodes/GB/RM9%205HU"]
    assert get_calls[-1] == ("shipments/GB000999ABC", None)
    assert body["user_id"] == 77
    assert body["client_id"] == 88
    assert body["platform"] == "PRO"
    assert body["platform_country"] == "GB"
    assert body["source"] == packlink_module.PACKLINK_DRAFT_SOURCE
    assert body["shipment_custom_reference"] == "AMAZON-999"
    assert body["content"] == "1 AMZ-SKU-1"
    assert body["contentvalue"] == 20.0
    assert body["contentValue_currency"] == "GBP"
    assert body["carrier"] == "Yodel UK"
    assert body["service"] == "InPost Shops"
    assert body["service_id"] == 21367

    assert body["from"]["street1"] == "Unit 10, St Mark's Works Foundry Lane"
    assert body["from"]["zip_code"] == "LE1 3WU"
    assert body["from"]["city"] == "Leicester"
    assert body["from"]["state"] == "Leicestershire"
    assert body["from"]["country"] == "GB"

    assert body["to"]["street1"] == "95 MAXEY ROAD"
    assert body["to"]["zip_code"] == "RM9 5HU"
    assert body["to"]["city"] == "DAGENHAM"
    assert body["to"]["state"] is None
    assert body["to"]["country"] == "GB"
    assert body["to"]["country_code"] == "GB"

    assert body["packages"] == [{
        "width": 10,
        "height": 10,
        "length": 10,
        "weight": 1.0,
    }]
    additional = body["additional_data"]
    assert "from" not in additional
    assert "to" not in additional
    assert additional["postal_zone_id_from"] == "gb-zone"
    assert "postal_zone_name_from" not in additional
    assert additional["zip_code_id_from"] == "pc_le13wu"
    assert additional["postal_zone_id_to"] == "gb-zone"
    assert additional["postal_zone_name_to"] == "United Kingdom"
    assert additional["zip_code_id_to"] == "pc_rm95hu"


def test_packlink_nested_selector_objects_never_leak_into_visible_address(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-NESTED")
    line = SimpleNamespace(quantity=1, sku="SKU", unit_price=5, line_total=5, warehouse_stock=None)
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"name":"B & T Outlet","company":"B & T OUTLET LTD","address1":"Sender","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Tony Longmire","address1":"5 MOOR ROAD, COLLINGHAM","address2":None,"city":"NEWARK","region":None,"postcode":"NG23 7SZ","country":"GB","email":"buyer@example.test","phone":"07900000001"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])

    def fake_get(endpoint, *, query=None):
        if endpoint == "clients":
            return {"id": 77, "client_id": 88, "country": "GB"}
        if endpoint == "locations/postalzones/destinations":
            return [{"id": "gb-zone", "isoCode": "GB", "name": "United Kingdom", "hasPostalCodes": True}]
        if endpoint.startswith("locations/postalcodes/"):
            postcode = unquote(endpoint.rsplit("/", 1)[1]).upper()
            city = "LEICESTER" if postcode == "LE1 3WU" else "NEWARK"
            return {
                "zipcode": postcode,
                "city": {"id": "gpc_20102523", "name": city},
                "country": {"id": 826, "iso_code": "GB", "name": "United Kingdom"},
            }
        if endpoint.startswith("shipments/"):
            return _verified_snapshot()
        raise AssertionError(endpoint)

    monkeypatch.setattr(adapter, "_get_json", fake_get)
    posted = {}
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: posted.update({"body": body}) or {"reference":"GB-NESTED"})

    adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    body = posted["body"]
    assert body["to"]["zip_code"] == "NG23 7SZ"
    assert body["to"]["city"] == "NEWARK"
    assert body["to"]["country"] == "GB"
    assert "{" not in body["to"]["city"]
    assert body["additional_data"]["zip_code_id_to"] == "gpc_20102523"
    assert body["additional_data"]["postal_zone_id_to"] == "gb-zone"
    assert body["additional_data"]["postal_zone_name_to"] == "United Kingdom"


def test_packlink_preserves_marketplace_second_address_line(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-ADDRESS2")
    line = SimpleNamespace(quantity=1, sku="SKU", unit_price=5, line_total=5, warehouse_stock=None)
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"name":"B & T Outlet","company":"B & T OUTLET LTD","address1":"Unit 10 Foundry Lane","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Ellen Rhodes","address1":"41","address2":"PLUMTREE PARK BIRCOTES","city":"DONCASTER","region":None,"postcode":"DN11 8QR","country":"GB","email":"buyer@example.test","phone":"07795058605"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    _mock_packlink_handoff(monkeypatch, adapter)
    posted = {}
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: posted.update({"body": body}) or {"reference":"GB-ADDRESS2"})
    result = adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    assert result["reference"] == "GB-ADDRESS2"
    assert posted["body"]["to"]["street1"] == "41"
    assert posted["body"]["to"]["street2"] == "PLUMTREE PARK BIRCOTES"
    assert posted["body"]["to"]["zip_code"] == "DN11 8QR"
    assert posted["body"]["to"]["city"] == "DONCASTER"
    assert posted["body"]["to"]["country"] == "GB"
    assert "to" not in posted["body"]["additional_data"]
    assert posted["body"]["additional_data"]["postal_zone_id_to"] == "gb-zone"
    assert posted["body"]["additional_data"]["postal_zone_name_to"] == "United Kingdom"
    assert posted["body"]["additional_data"]["zip_code_id_to"] == "pc_dn118qr"


def test_packlink_destination_selector_is_a_handoff_gate(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-FALLBACK")
    line = SimpleNamespace(quantity=1, sku="SKU", unit_price=5, line_total=5, warehouse_stock=None)
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"name":"B & T Outlet","company":"B & T OUTLET LTD","address1":"Sender","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Customer One","address1":"1 Road","address2":None,"city":"London","region":None,"postcode":"SW1A 1AA","country":"GB","email":"buyer@example.test","phone":"07900000001"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])

    def fake_get(endpoint, *, query=None):
        if endpoint == "clients":
            return {"id": 77, "client_id": 88, "country": "GB"}
        raise PacklinkRequestError("location service unavailable", status_code=503)

    monkeypatch.setattr(adapter, "_get_json", fake_get)
    posted = {}
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: posted.update({"body": body}) or {"reference":"GB-FALLBACK"})
    with pytest.raises(PacklinkRequestError, match="destination country selector"):
        adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    assert posted == {}


def test_packlink_country_selector_retries_unfiltered_zone_lookup(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-RETRY")
    line = SimpleNamespace(quantity=1, sku="SKU", unit_price=5, line_total=5, warehouse_stock=None)
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"name":"B & T Outlet","company":"B & T OUTLET LTD","address1":"Sender","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Customer One","address1":"1 Road","address2":None,"city":"London","region":None,"postcode":"SW1A 1AA","country":"GB","email":"buyer@example.test","phone":"07900000001"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    zone_calls = []

    def fake_get(endpoint, *, query=None):
        if endpoint == "clients":
            return {"id": 77, "client_id": 88, "country": "GB"}
        if endpoint == "locations/postalzones/destinations":
            zone_calls.append(query)
            if query is not None:
                return []
            return [{"id":"gb-zone","isoCode":"GB","name":"United Kingdom"}]
        if endpoint.startswith("locations/postalcodes/"):
            postcode = unquote(endpoint.rsplit("/", 1)[1]).upper()
            return {"id":"pc_" + postcode.replace(" ", "").lower(), "zipcode":postcode, "country_code":"GB"}
        if endpoint.startswith("shipments/"):
            return _verified_snapshot()
        raise AssertionError(endpoint)

    monkeypatch.setattr(adapter, "_get_json", fake_get)
    posted = {}
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: posted.update({"body": body}) or {"reference":"GB-RETRY"})
    result = adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    assert result["reference"] == "GB-RETRY"
    assert None in zone_calls
    assert posted["body"]["additional_data"]["postal_zone_id_to"] == "gb-zone"


def test_packlink_provider_reference_requires_exact_remote_shipment_readback(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-DIRECT")
    line = SimpleNamespace(quantity=1, sku="SKU", unit_price=5, line_total=5, warehouse_stock=None)
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"name":"B & T Outlet","company":"B & T OUTLET LTD","address1":"Sender","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Customer One","address1":"1 Road","address2":None,"city":"London","region":None,"postcode":"SW1A 1AA","country":"GB","email":"buyer@example.test","phone":"07900000001"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    _mock_packlink_handoff(monkeypatch, adapter)
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: {"reference":"GB-DIRECT"})
    result = adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    assert result["reference"] == "GB-DIRECT"
    assert result["payment_status"] == "pending_packlink_payment"
    assert result["verified"] is True


def test_packlink_uses_real_order_value_when_available(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-1000")
    line = SimpleNamespace(quantity=2, sku="SKU-2", unit_price=4.50, line_total=9.00, warehouse_stock=SimpleNamespace(product_name="Test Product"))
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"name":"B & T Outlet","company":"B & T OUTLET LTD","address1":"Sender","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Customer One","address1":"1 Road","address2":None,"city":"London","region":None,"postcode":"SW1A 1AA","country":"GB","email":"buyer@example.test","phone":"07900000001"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    _mock_packlink_handoff(monkeypatch, adapter)
    posted = {}
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: posted.update({"body":body}) or {"shipment_reference":"GB001000ABC"})
    adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    assert posted["body"]["content"] == "2 SKU-2"
    assert posted["body"]["contentvalue"] == 9.0
    assert posted["body"]["additional_data"]["items"][0]["price"] == 9.0


def test_packlink_paid_label_can_attach_by_marketplace_reference_without_bt38_draft():
    attach_source = getsource(_attach_by_marketplace_reference)
    event_source = getsource(process_packlink_event)
    assert "marketplace_order_id=custom_reference" in attach_source
    assert "packlink_external:" in attach_source
    assert "provider_shipment_id=reference" in attach_source
    assert "_find_exact_shipment" in event_source
    assert "persist_external_label" in event_source


def test_packlink_runtime_sleeps_until_exact_provider_event():
    route_source = getsource(callback_routes.packlink_callback)
    event_source = getsource(process_packlink_event)
    recovery_source = getsource(callback_routes.recover_packlink_today)
    assert "process_packlink_event(payload)" in route_source
    assert "recover_packlink_shipments_for_day" not in route_source
    assert "recover_packlink_shipments_for_day" not in recovery_source
    assert "_register_callback" not in recovery_source
    label_branch = event_source.split('if event_name == "shipment.label.ready":', 1)[1]
    before_label_branch = event_source.split('if event_name == "shipment.label.ready":', 1)[0]
    assert "get_labels(reference)" in label_branch
    assert "get_labels(reference)" not in before_label_branch
    tracking_branch = event_source.split('if event_name == "shipment.tracking.update":', 1)[1]
    assert "get_tracking_status" in tracking_branch
    assert "get_labels" not in tracking_branch
    assert "recover_packlink_shipments_for_day" not in event_source
    assert ".all()" not in event_source
    assert "created_at >=" not in event_source
