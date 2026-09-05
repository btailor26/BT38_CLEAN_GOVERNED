from pathlib import Path


def test_prime_profile_comes_from_current_amazon_sale_webhook_only():
    source = Path("services/governed_fbm_sale_authority_alignment.py").read_text()

    assert 'request.path.rstrip("/")' in source
    assert '"/governed/webhooks/amazon"' in source
    assert '"OrderPrograms"' in source
    assert '"prime" in programs' in source
    assert '"premium" in programs' in source
    assert 'source="amazon_order_change"' in source
    assert "FBMOrderProfile" in source
    assert "MarketplaceOrder.marketplace_order_id == order_id" in source
    assert "get_or_refresh_amazon_profile" not in source
    assert "_fetch_order" not in source
    assert "sp_api" not in source
    assert "requests." not in source
    assert "recover" not in source.lower()


def test_existing_bell_installer_installs_sale_authority_without_second_transport():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()
    source = Path("services/governed_fbm_sale_authority_alignment.py").read_text()

    assert "install_governed_fbm_sale_authority_alignment(app)" in projection
    assert "EventSource" not in source
    assert "BroadcastChannel" not in source
    assert "setInterval" not in source
    assert "publish_governed_ui_event" not in source
    assert "publish_webhook_ui_event" not in source
