from services.governed_amazon_eventbridge_alignment import _eventbridge_spec


def test_eventbridge_spec_reads_amazon_returned_resource_shape():
    destination = {
        "destinationId": "dest-123",
        "name": "BT38 Amazon EventBridge",
        "resource": {
            "eventBridge": {
                "name": "aws.partner/sellingpartnerapi.amazon.com/example",
                "region": "eu-west-2",
                "accountId": "123456789012",
            }
        },
    }

    assert _eventbridge_spec(destination) == {
        "name": "aws.partner/sellingpartnerapi.amazon.com/example",
        "region": "eu-west-2",
        "accountId": "123456789012",
    }


def test_eventbridge_spec_keeps_request_shape_as_defensive_fallback():
    destination = {
        "resourceSpecification": {
            "eventBridge": {
                "region": "eu-west-2",
                "accountId": "123456789012",
            }
        }
    }

    assert _eventbridge_spec(destination) == {
        "region": "eu-west-2",
        "accountId": "123456789012",
    }
