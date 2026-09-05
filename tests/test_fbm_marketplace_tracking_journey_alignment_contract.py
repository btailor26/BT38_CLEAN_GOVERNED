from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_marketplace_tracking_stays_inside_bt38_journey():
    source = _read("services/governed_fbm_tracking_authority_restore.py")

    assert "fbm-tracking-journey" in source
    assert "link.removeAttribute('href')" in source
    assert "link.dataset.journeySource = 'marketplace'" in source
    assert "removeMarketplaceRedirectsFromJourney" in source
    assert "window.location.assign(`https://www.ebay.co.uk/mesh/ord/details" not in source
    assert "window.location.assign(`https://sellercentral.amazon.co.uk/orders-v3/order/" not in source


def test_ebay_physical_fulfillment_carrier_outranks_buyer_selected_service():
    ebay = _read("services/governed_exact_ebay_order_hydration.py")
    promise = _read("services/fbm_db_delivery_promise_alignment.py")

    assert 'fulfillment.get("shippingCarrierCode")' in ebay
    assert 'row.carrier = shipment["carrier"]' in ebay
    assert 'fulfillment.get("trackingNumber")' in ebay

    # eBay shipping_service remains marketplace promise context only. The
    # presentation-only marketplace shipment is enriched from promise service
    # only for Amazon package shippingService, never for eBay buyer selection.
    assert 'and platform == "amazon"' in promise
    assert 'shipment.service = service' in promise
