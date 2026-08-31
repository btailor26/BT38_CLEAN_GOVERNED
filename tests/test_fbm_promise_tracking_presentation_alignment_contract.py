from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLARITY = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")
PROMISE = (ROOT / "services" / "fbm_db_delivery_promise_alignment.py").read_text(encoding="utf-8")
HEALTH = (ROOT / "services" / "fbm_current_queue_health_alignment.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "fbm.html").read_text(encoding="utf-8")


def test_fbm_clarity_installs_existing_db_delivery_promise_alignment():
    assert "install_fbm_db_delivery_promise_alignment" in CLARITY
    assert "fbm_order_operational_state" in PROMISE
    assert "FBMOrderProfile" in PROMISE
    assert "shipment_service_level" in PROMISE
    assert "latest_ship_at" in PROMISE
    assert "information_schema.columns" in PROMISE
    assert "NULL AS" in PROMISE
    assert "promise.ship_by_at" in TEMPLATE
    assert "promise.latest_delivery_at" in TEMPLATE
    assert "get_amazon_delivery_promise" not in CLARITY
    assert "requests." not in CLARITY


def test_persisted_promise_alignment_is_backward_compatible_and_db_only():
    assert "_profile_promises" in PROMISE
    assert "_operational_promises" in PROMISE
    assert "_merge_promise" in PROMISE
    assert '"source": "fbm_order_profiles"' in PROMISE
    assert '"source": "fbm_order_operational_state"' in PROMISE
    assert "requests." not in PROMISE
    assert "get_amazon_delivery_promise" not in PROMISE


def test_current_fbm_health_uses_unresolved_persisted_queue_not_created_today_only():
    assert "install_fbm_current_queue_health_alignment" in CLARITY
    assert "original_health_summary" in HEALTH
    assert "_TERMINAL_STATUSES" in HEALTH
    assert "current_rows.append(row)" in HEALTH
    assert 'route_state in {"Dispatched", "Tracking recorded"}' in HEALTH
    assert '"total": len(current_rows)' in HEALTH
    assert '"dispatched": dispatched' in HEALTH
    assert '"awaiting_acceptance": awaiting' in HEALTH
    assert '"overdue": overdue' in HEALTH
    assert "MarketplaceOrder.created_at >=" not in HEALTH
    assert "requests." not in HEALTH
    assert "db.session.add" not in HEALTH
    assert "db.session.commit" not in HEALTH


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


def test_mapping_review_card_is_replaced_by_buyer_messages_without_fake_count():
    assert "_align_fbm_buyer_messages_card" in CLARITY
    assert "Buyer messages" in CLARITY
    assert "No buyer messages are currently ingested into BT38." in CLARITY
    assert "<div class=\"fbm-period-value\">0</div>" in CLARITY
    assert "html = _align_fbm_buyer_messages_card(html)" in CLARITY
    assert '"buyer_messages": 0' in HEALTH
    assert '"mapping_review": 0' in HEALTH


def test_alignment_is_presentation_only_after_render():
    response_section = CLARITY.split("def bt38_order_clarity_response", 1)[1]
    assert "db.session" not in response_section
    assert "requests." not in response_section
    assert "response.set_data" in response_section
