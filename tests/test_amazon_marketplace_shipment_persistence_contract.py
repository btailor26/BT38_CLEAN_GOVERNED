from pathlib import Path


TRACKING = Path(
    "services/governed_amazon_tracking_readback.py"
).read_text(encoding="utf-8")
PROMISE = Path(
    "services/fbm_db_delivery_promise_alignment.py"
).read_text(encoding="utf-8")
LIFECYCLE = Path(
    "services/governed_fbm_lifecycle_alignment.py"
).read_text(encoding="utf-8")
STATE = Path(
    "services/fbm_shipping_state.py"
).read_text(encoding="utf-8")


def test_exact_amazon_package_readback_preserves_physical_shipping_service():
    assert 'package.get("shippingService")' in TRACKING
    assert '"shipping_service": shipment.get("shipping_service")' in TRACKING
    assert "fbm_order_operational_state" in TRACKING
    assert "shipping_service=EXCLUDED.shipping_service" in TRACKING
    assert "IS DISTINCT FROM EXCLUDED.shipping_service" in TRACKING


def test_marketplace_package_service_reuses_existing_fbm_shipment_presentation():
    assert 'provider == "marketplace"' in PROMISE
    assert 'shipment.service = service' in PROMISE
    assert 'item["delivery_promise"] = promise' in PROMISE


def test_marketplace_journey_uses_existing_proxy_without_fake_db_shipment():
    assert 'provider="marketplace"' in LIFECYCLE
    assert "SimpleNamespace(" in LIFECYCLE
    assert "_marketplace_proven_state" in STATE
    assert "FBMShipment(" not in TRACKING
    assert '"marketplace_shipment_persisted": False' in TRACKING
    assert '"marketplace_write_started": False' in TRACKING


def test_marketplace_lifecycle_remains_explicit_amazon_truth():
    assert '"PICKEDUPBYCARRIER": "picked_up"' in TRACKING
    assert '"CHECKEDINTOCARRIERHUB": "in_transit"' in TRACKING
    assert '"OUTFORDELIVERY": "out_for_delivery"' in TRACKING
    assert '"DELIVERED": "delivered"' in TRACKING
    assert 'if provider == "marketplace":' in STATE
