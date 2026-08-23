from types import SimpleNamespace

import services.fbm_packlink_adapter as packlink_module
from services.fbm_packlink_adapter import PacklinkAdapter


PACKLINK_FLAT_HEADERS = [
    "Reference", "First Name", "Last Name", "Company", "Sender Address 1",
    "Sender Address 2", "Sender Postal Code", "Sender City", "Sender Region",
    "Sender Country", "Sender Phone", "Sender Email", "Receiver First Name",
    "Receiver Last Name", "Receiver Company name", "Receiver Address 1",
    "Receiver Address 2", "Receiver Postal Code", "Receiver City",
    "Receiver Region", "Receiver Country", "Receiver Phone", "Receiver Email",
    "Insurance", "Content", "Value", "Width", "Length", "Height", "Weight",
]


def _flat_row_from_handoff(body):
    sender = body["from"]
    receiver = body["to"]
    package = body["packages"][0]
    return {
        "Reference": body["shipment_custom_reference"],
        "First Name": sender["name"],
        "Last Name": sender["surname"],
        "Company": sender["company"],
        "Sender Address 1": sender["street1"],
        "Sender Address 2": sender["street2"],
        "Sender Postal Code": sender["zip_code"],
        "Sender City": sender["city"],
        "Sender Region": sender["state"] or "",
        "Sender Country": sender["country"],
        "Sender Phone": sender["phone"],
        "Sender Email": sender["email"],
        "Receiver First Name": receiver["name"],
        "Receiver Last Name": receiver["surname"],
        "Receiver Company name": receiver["company"],
        "Receiver Address 1": receiver["street1"],
        "Receiver Address 2": receiver["street2"],
        "Receiver Postal Code": receiver["zip_code"],
        "Receiver City": receiver["city"],
        "Receiver Region": receiver["state"] or "",
        "Receiver Country": receiver["country"],
        "Receiver Phone": receiver["phone"],
        "Receiver Email": receiver["email"],
        "Insurance": "no",
        "Content": body["content"],
        "Value": str(int(body["contentvalue"])) if float(body["contentvalue"]).is_integer() else str(body["contentvalue"]),
        "Width": str(package["width"]),
        "Length": str(package["length"]),
        "Height": str(package["height"]),
        "Weight": str(int(package["weight"])) if float(package["weight"]).is_integer() else str(package["weight"]),
    }


def test_api_handoff_round_trips_to_packlink_accepted_flat_file_structure(monkeypatch):
    """The API handoff must carry the same facts as Packlink's accepted flat import.

    This contract mirrors the user-provided Packlink_Import_20260817_Evening CSV
    header and first-row shape. It deliberately tests the handoff facts rather than
    Packlink UI selector metadata.
    """
    adapter = PacklinkAdapter(api_key="test-key")
    order = SimpleNamespace(marketplace_order_id="202-3884520-0547534")
    line = SimpleNamespace(
        quantity=1,
        sku="Fevicryl Fabric Glue",
        unit_price=20,
        line_total=20,
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
            "name": "ellen rhodes",
            "company": "",
            "address1": "41",
            "address2": "PLUMTREE PARK BIRCOTES",
            "city": "DONCASTER",
            "region": "",
            "postcode": "DN11 8QR",
            "country": "GB",
            "email": "jnjm5y4m22q2ldr@marketplace.amazon.co.uk",
            "phone": "07795058605",
        },
    )
    monkeypatch.setattr(packlink_module, "order_lines", lambda _order: [line])

    def fake_get(endpoint, *, query=None):
        if endpoint == "clients":
            return {"id": 77, "client_id": 88, "country": "GB"}
        if endpoint == "locations/postalzones/destinations":
            return [{"id": 826, "isoCode": "GB", "name": "United Kingdom"}]
        if endpoint.startswith("locations/postalcodes/"):
            postcode = endpoint.rsplit("/", 1)[1].replace("%20", " ")
            return {
                "id": "pc_" + postcode.replace(" ", "").lower(),
                "zipcode": postcode,
                "postal_zone_id": 826,
                "postal_zone_name": "United Kingdom",
                "country_code": "GB",
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(adapter, "_get_json", fake_get)
    posted = {}
    monkeypatch.setattr(
        adapter,
        "_post_json",
        lambda endpoint, body: posted.update({"endpoint": endpoint, "body": body})
        or {"shipment_reference": "UN-FLAT-CONTRACT"},
    )

    adapter.create_shipment_draft(
        order=order,
        parcel={"weight_kg": 1, "width_cm": 10, "length_cm": 10, "height_cm": 10},
        rate={"service_id": 21367, "carrier": "Yodel UK", "service": "InPost Shops"},
    )

    flat = _flat_row_from_handoff(posted["body"])
    assert list(flat) == PACKLINK_FLAT_HEADERS
    assert flat == {
        "Reference": "202-3884520-0547534",
        "First Name": "Bhavin",
        "Last Name": "Tailor",
        "Company": "B & T OUTLET LTD",
        "Sender Address 1": "Unit 10, St Mark's Works Foundry Lane",
        "Sender Address 2": "",
        "Sender Postal Code": "LE1 3WU",
        "Sender City": "Leicester",
        "Sender Region": "Leicestershire",
        "Sender Country": "GB",
        "Sender Phone": "07903883892",
        "Sender Email": "weeklydeals2014@outlook.com",
        "Receiver First Name": "ellen",
        "Receiver Last Name": "rhodes",
        "Receiver Company name": "",
        "Receiver Address 1": "41",
        "Receiver Address 2": "PLUMTREE PARK BIRCOTES",
        "Receiver Postal Code": "DN11 8QR",
        "Receiver City": "DONCASTER",
        "Receiver Region": "",
        "Receiver Country": "GB",
        "Receiver Phone": "07795058605",
        "Receiver Email": "jnjm5y4m22q2ldr@marketplace.amazon.co.uk",
        "Insurance": "no",
        "Content": "1 Fevicryl Fabric Glue",
        "Value": "20",
        "Width": "10",
        "Length": "10",
        "Height": "10",
        "Weight": "1",
    }
    assert posted["body"]["to"]["country"] == flat["Receiver Country"] == "GB"
