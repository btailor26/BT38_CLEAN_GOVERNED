from inspect import getsource
from types import SimpleNamespace

import services.fbm_packlink_adapter as packlink_module
from services.fbm_packlink_adapter import PacklinkAdapter
from services.fbm_packlink_callback import (
    _attach_by_marketplace_reference,
    process_packlink_callback,
)


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
            "name": "Bhavin Tailor",
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
        rate={"service_id": 21367},
    )

    body = posted["body"]
    assert posted["endpoint"] == "shipments"
    assert result["reference"] == "GB000999ABC"

    assert body["shipment_custom_reference"] == "AMAZON-999"
    assert body["content"] == "Fevicryl Fabric Glue"
    assert body["contentvalue"] == 20.0
    assert body["contentValue_currency"] == "GBP"

    assert body["from"]["name"] == "Bhavin"
    assert body["from"]["surname"] == "Tailor"
    assert body["from"]["company"] == "B & T OUTLET LTD"
    assert body["from"]["street1"] == "Unit 10, St Mark's Works Foundry Lane"
    assert body["from"]["zip_code"] == "LE1 3WU"
    assert body["from"]["city"] == "Leicester"
    assert body["from"]["state"] == "Leicestershire"

    assert body["to"]["street1"] == "95 MAXEY ROAD"
    assert body["to"]["zip_code"] == "RM9 5HU"
    assert body["to"]["city"] == "DAGENHAM"
    assert body["to"]["country"] == "GB"
    assert body["to"]["phone"] == "+447900000000"
    assert body["to"]["email"] == "buyer@marketplace.amazon.co.uk"

    assert body["packages"] == [{"width": 10, "height": 10, "length": 10, "weight": 1.0}]

    assert body["additional_data"]["content"] == body["content"]
    assert body["additional_data"]["contentvalue"] == body["contentvalue"]
    assert body["additional_data"]["from"] == body["from"]
    assert body["additional_data"]["to"] == body["to"]


def test_packlink_uses_real_order_value_when_available(monkeypatch):
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="AMAZON-1000")
    line = SimpleNamespace(
        quantity=2,
        sku="SKU-2",
        unit_price=4.50,
        line_total=9.00,
        warehouse_stock=SimpleNamespace(product_name="Test Product"),
    )

    monkeypatch.setattr(
        packlink_module,
        "ship_from",
        lambda: {
            "name": "Bhavin Tailor", "company": "B & T OUTLET LTD",
            "address1": "Sender", "address2": None, "city": "Leicester",
            "region": "Leicestershire", "postcode": "LE1 3WU", "country": "GB",
            "email": "sender@example.test", "phone": "07900000000",
        },
    )
    monkeypatch.setattr(
        packlink_module,
        "ship_to",
        lambda _order: {
            "name": "Customer One", "address1": "1 Road", "address2": None,
            "city": "London", "region": None, "postcode": "SW1A 1AA", "country": "GB",
            "email": "buyer@example.test", "phone": "07900000001",
        },
    )
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])

    posted = {}
    monkeypatch.setattr(
        adapter,
        "_post_json",
        lambda endpoint, body: posted.update({"body": body}) or {"shipment_reference": "GB001000ABC"},
    )

    adapter.create_shipment_draft(
        order=order,
        parcel={"weight_kg": 1, "width_cm": 10, "length_cm": 10, "height_cm": 10},
        rate={"service_id": 21367},
    )

    assert posted["body"]["content"] == "Test Product"
    assert posted["body"]["contentvalue"] == 9.0


def test_packlink_paid_label_can_attach_by_marketplace_reference_without_bt38_draft():
    attach_source = getsource(_attach_by_marketplace_reference)
    callback_source = getsource(process_packlink_callback)

    assert "marketplace_order_id=custom_reference" in attach_source
    assert "packlink_external:" in attach_source
    assert "provider_shipment_id=reference" in attach_source
    assert "_attach_by_marketplace_reference" in callback_source
    assert "shipment_custom_reference" in callback_source
    assert "persist_external_label" in callback_source
