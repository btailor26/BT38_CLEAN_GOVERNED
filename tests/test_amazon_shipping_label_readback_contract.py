from __future__ import annotations

from services.governed_amazon_shipping_label_readback import (
    _shipment_candidates,
    _shipment_values,
)


def test_finance_shipment_candidate_requires_exact_order_identity():
    transactions = [
        {
            "transactionId": "label-charge",
            "relatedIdentifiers": [
                {
                    "relatedIdentifierName": "ORDER_ID",
                    "relatedIdentifierValue": "204-2659199-8036304",
                },
                {
                    "relatedIdentifierName": "SHIPMENT_ID",
                    "relatedIdentifierValue": "49902f41-9ffc-4032-beeb-aee5cb434107",
                },
            ],
        },
        {
            "transactionId": "wrong-order",
            "relatedIdentifiers": [
                {
                    "relatedIdentifierName": "ORDER_ID",
                    "relatedIdentifierValue": "204-0000000-0000000",
                },
                {
                    "relatedIdentifierName": "SHIPMENT_ID",
                    "relatedIdentifierValue": "wrong-shipment",
                },
            ],
        },
    ]

    assert _shipment_candidates(transactions, "204-2659199-8036304") == [
        "49902f41-9ffc-4032-beeb-aee5cb434107"
    ]


def test_merchant_fulfillment_untracked_label_keeps_shipment_authority():
    values = _shipment_values(
        {
            "ShipmentId": "49902f41-9ffc-4032-beeb-aee5cb434107",
            "AmazonOrderId": "026-3732932-8616338",
            "Status": "Purchased",
            "CreatedDate": "2026-08-27T09:45:00Z",
            "ShippingService": {
                "CarrierName": "Royal Mail",
                "ShippingServiceName": "Royal Mail 2nd Class Large Letter",
                "ShippingServiceId": "RM_2ND_LL",
            },
            "Label": {
                "LabelFormat": "ShippingServiceDefault",
                "FileContents": {"FileType": "application/pdf"},
            },
        }
    )

    assert values["shipment_id"] == "49902f41-9ffc-4032-beeb-aee5cb434107"
    assert values["order_id"] == "026-3732932-8616338"
    assert values["carrier"] == "Royal Mail"
    assert values["service"] == "Royal Mail 2nd Class Large Letter"
    assert values["tracking_number"] is None
    assert values["status"] == "Purchased"
    assert values["label_format"] == "PDF"


def test_merchant_fulfillment_tracked_label_extracts_tracking_when_present():
    values = _shipment_values(
        {
            "ShipmentId": "4c455d87-7737-42e4-99d2-24ddbe0fe777",
            "AmazonOrderId": "204-2659199-8036304",
            "Status": "Purchased",
            "TrackingId": "AA123456789GB",
            "ShippingService": {
                "CarrierName": "Royal Mail",
                "ShippingServiceName": "Tracked service",
                "ShippingServiceId": "TRACKED",
            },
        }
    )

    assert values["shipment_id"] == "4c455d87-7737-42e4-99d2-24ddbe0fe777"
    assert values["tracking_number"] == "AA123456789GB"
