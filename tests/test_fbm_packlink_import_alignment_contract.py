from inspect import getsource
from types import SimpleNamespace

import governed_packlink_callback_routes as callback_routes
import services.fbm_packlink_adapter as packlink_module
from services.fbm_packlink_adapter import PacklinkAdapter, PacklinkRequestError
from services.fbm_packlink_callback import _attach_by_marketplace_reference
from services.fbm_packlink_event_processor import process_packlink_event


def _ready(monkeypatch, adapter):
    monkeypatch.setattr(adapter, "get_shipment", lambda reference: {"state": "READY_TO_PURCHASE"})


def test_packlink_future_draft_matches_required_import_layout(monkeypatch):
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
            "country": "GB",
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
            "country": "GB",
            "email": "buyer@marketplace.amazon.co.uk",
            "phone": "+447900000000",
        },
    )
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    _ready(monkeypatch, adapter)

    provider_gets = []
    original_get = adapter._get_json
    monkeypatch.setattr(adapter, "_get_json", lambda endpoint, **kwargs: provider_gets.append((endpoint, kwargs)) or original_get(endpoint, **kwargs))

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
    assert result["reference"] == "GB000999ABC"
    assert result["state"] == "READY_TO_PURCHASE"
    assert body["shipment_custom_reference"] == "AMAZON-999"
    assert body["content"] == "Fevicryl Fabric Glue"
    assert body["contentvalue"] == 20.0
    assert body["currency"] == "GBP"
    assert body["source"] == "source_inbound"
    assert body["carrier"] == "Yodel UK"
    assert body["service"] == "InPost Shops"

    assert body["from"]["name"] == "B & T"
    assert body["from"]["surname"] == "Outlet"
    assert body["from"]["company"] == "B & T OUTLET LTD"
    assert body["from"]["street1"] == "Unit 10, St Mark's Works Foundry Lane"
    assert body["from"]["street2"] is None
    assert body["from"]["zip_code"] == "LE1 3WU"
    assert body["from"]["city"] == "Leicester"
    assert body["from"]["state"] == "Leicestershire"
    assert body["from"]["country"] == "GB"
    assert body["from"]["phone"] == "07903883892"
    assert body["from"]["email"] == "weeklydeals2014@outlook.com"

    assert body["to"]["street1"] == "95 MAXEY ROAD"
    assert body["to"]["street2"] is None
    assert body["to"]["zip_code"] == "RM9 5HU"
    assert body["to"]["city"] == "DAGENHAM"
    assert body["to"]["state"] is None
    assert body["to"]["country"] == "GB"
    assert body["to"]["phone"] == "+447900000000"
    assert body["to"]["email"] == "buyer@marketplace.amazon.co.uk"

    assert body["packages"] == [{
        "width": 10,
        "height": 10,
        "length": 10,
        "weight": 1.0,
    }]
    assert "id" not in body["packages"][0]
    assert "name" not in body["packages"][0]

    additional = body["additional_data"]
    assert additional["from"] == body["from"]
    assert additional["to"] == body["to"]
    assert additional["content"] == body["content"]
    assert additional["contentvalue"] == body["contentvalue"]
    assert additional["shipment_custom_reference"] == body["shipment_custom_reference"]
    assert additional["contentValue_currency"] == "GBP"
    assert "selectedWarehouseId" not in additional
    assert "postal_zone_id_from" not in additional
    assert "postal_zone_id_to" not in additional
    assert "zip_code_id_from" not in additional
    assert "zip_code_id_to" not in additional
    assert provider_gets == []


def test_packlink_preserves_marketplace_second_address_line(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-ADDRESS2")
    line = SimpleNamespace(quantity=1, sku="SKU", unit_price=5, line_total=5, warehouse_stock=None)
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"company":"B & T OUTLET LTD","address1":"Unit 10 Foundry Lane","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Ellen Rhodes","address1":"41","address2":"PLUMTREE PARK BIRCOTES","city":"DONCASTER","region":None,"postcode":"DN11 8QR","country":"GB","email":"buyer@example.test","phone":"07795058605"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    _ready(monkeypatch, adapter)
    posted = {}
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: posted.update({"body":body}) or {"reference":"GB-ADDRESS2"})
    adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    assert posted["body"]["to"]["street1"] == "41"
    assert posted["body"]["to"]["street2"] == "PLUMTREE PARK BIRCOTES"
    assert posted["body"]["to"]["zip_code"] == "DN11 8QR"
    assert posted["body"]["to"]["city"] == "DONCASTER"
    assert posted["body"]["to"]["country"] == "GB"
    assert posted["body"]["additional_data"]["to"] == posted["body"]["to"]


def test_packlink_rejects_incomplete_remote_draft_with_provider_readback(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-INCOMPLETE")
    line = SimpleNamespace(quantity=1, sku="SKU", unit_price=5, line_total=5, warehouse_stock=None)
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"company":"B & T OUTLET LTD","address1":"Sender","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Customer One","address1":"1 Road","address2":None,"city":"London","region":None,"postcode":"SW1A 1AA","country":"GB","email":"buyer@example.test","phone":"07900000001"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: {"reference":"GB-INCOMPLETE"})
    monkeypatch.setattr(adapter, "get_shipment", lambda reference: {"state":"AWAITING_COMPLETION","from":{"country":"GB","zip_code":"","city":"Leicester","state":"Leicestershire"},"to":{"country":"","zip_code":"SW1A 1AA","city":"London","state":None}})
    try:
        adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    except PacklinkRequestError as exc:
        message = str(exc)
        assert "GB-INCOMPLETE" in message
        assert "AWAITING_COMPLETION" in message
        assert "sender=GB/-/Leicester/Leicestershire" in message
        assert "receiver=-/SW1A 1AA/London/-" in message
    else:
        raise AssertionError("Incomplete Packlink draft must not be reported as ready")


def test_packlink_uses_real_order_value_when_available(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-1000")
    line = SimpleNamespace(quantity=2, sku="SKU-2", unit_price=4.50, line_total=9.00, warehouse_stock=SimpleNamespace(product_name="Test Product"))
    monkeypatch.setattr(packlink_module, "ship_from", lambda: {"company":"B & T OUTLET LTD","address1":"Sender","address2":None,"city":"Leicester","region":"Leicestershire","postcode":"LE1 3WU","country":"GB","email":"sender@example.test","phone":"07900000000"})
    monkeypatch.setattr(packlink_module, "ship_to", lambda _order: {"name":"Customer One","address1":"1 Road","address2":None,"city":"London","region":None,"postcode":"SW1A 1AA","country":"GB","email":"buyer@example.test","phone":"07900000001"})
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])
    _ready(monkeypatch, adapter)
    posted = {}
    monkeypatch.setattr(adapter, "_post_json", lambda endpoint, body: posted.update({"body":body}) or {"shipment_reference":"GB001000ABC"})
    adapter.create_shipment_draft(order=order, parcel={"weight_kg":1,"width_cm":10,"length_cm":10,"height_cm":10}, rate={"service_id":21367})
    assert posted["body"]["content"] == "Test Product"
    assert posted["body"]["contentvalue"] == 9.0
    assert posted["body"]["additional_data"]["contentvalue"] == 9.0


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
