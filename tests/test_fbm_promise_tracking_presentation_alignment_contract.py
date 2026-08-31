from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLARITY = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")
PROMISE = (ROOT / "services" / "fbm_db_delivery_promise_alignment.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "fbm.html").read_text(encoding="utf-8")


def test_fbm_clarity_installs_existing_db_delivery_promise_alignment():
    assert "install_fbm_db_delivery_promise_alignment" in CLARITY
    assert "fbm_order_operational_state" in PROMISE
    assert "promise.ship_by_at" in TEMPLATE
    assert "promise.latest_delivery_at" in TEMPLATE
    assert "get_amazon_delivery_promise" not in CLARITY
    assert "requests." not in CLARITY


def test_tracking_number_remains_clickable_without_underlining_the_id():
    assert "bt38FbmTrackingLinkAlignment" in CLARITY
    assert "a:has(code){text-decoration:none!important}" in CLARITY
    assert "a:has(code) code{text-decoration:none!important}" in CLARITY
    assert "target=\"_blank\"" in TEMPLATE


def test_existing_marketplace_badges_are_enlarged_without_replacing_assets():
    assert "bt38FbmMarketplaceBadgeAlignment" in CLARITY
    assert "min-width:110px!important" in CLARITY
    assert "max-width:82px!important" in CLARITY
    assert "max-height:36px!important" in CLARITY
    assert "width:auto!important;height:auto!important" in CLARITY
    assert "img/marketplaces/amazon.png" in TEMPLATE
    assert "img/marketplaces/ebay.png" in TEMPLATE
    assert "img/marketplaces/shopify.png" in TEMPLATE
    assert "img/marketplaces/tiktok.png" in TEMPLATE


def test_alignment_is_presentation_only_after_render():
    response_section = CLARITY.split("def bt38_order_clarity_response", 1)[1]
    assert "db.session" not in response_section
    assert "requests." not in response_section
    assert "response.set_data" in response_section
