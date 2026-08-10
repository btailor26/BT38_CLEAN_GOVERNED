from pathlib import Path


MAIN = Path("main.py").read_text(encoding="utf-8")
MCF = Path("services/governed_mcf_execution.py").read_text(encoding="utf-8")


def test_amazon_webhook_wakes_existing_mcf_refresh_only():
    assert "original_webhook_execution(" in MAIN
    assert "refresh_mcf_from_amazon_signal" in MAIN
    assert 'str(marketplace or "").strip().lower() != "amazon"' in MAIN
    assert 'result["mcf_signal_refresh"] = mcf_result' in MAIN


def test_signal_resolves_one_exact_existing_mcf_identity():
    assert "def _mcf_signal_identifiers" in MCF
    assert 'key_name == "sellerfulfillmentorderid"' in MCF
    assert 'key_name == "sellerfulfillmentorderitemid"' in MCF
    assert 'key_name == "amazonorderid"' in MCF
    assert "MCFOrder.seller_fulfillment_order_id.in_(seller_ids)" in MCF
    assert "MCFOrder.amazon_order_id.in_(amazon_order_ids)" in MCF
    assert "MCFOrder.query.all" not in MCF


def test_webhook_is_signal_not_tracking_authority():
    assert "refreshed, refresh_result = refresh_mcf_status(mcf)" in MCF
    assert ".get_fulfillment_order(" in MCF
    assert 'first_package.get("trackingNumber")' in MCF
    assert 'first_package.get("carrierCode")' in MCF


def test_tracking_uses_existing_governed_ebay_write_path():
    assert "from services.governed_ebay_dispatch import complete_sale" in MCF
    assert "from services.runtime_action_guard import is_runtime_action_allowed" in MCF
    assert '"mcf_tracking_amazon_webhook_enrichment"' in MCF
    assert "dispatch = complete_sale(" in MCF
    assert 'line.status = "mcf_tracking_updated"' in MCF


def test_tracking_signal_does_not_bypass_one_hour_window():
    assert "accepted_at = (" in MCF
    assert "release_at = (" in MCF
    assert "accepted_at + timedelta(hours=1)" in MCF
    assert "datetime.utcnow() < release_at" in MCF
    assert '"tracking_received_inside_one_hour_cancellation_window"' in MCF


def test_failed_exact_mcf_continuation_is_not_false_success():
    assert "amazon_mcf_signal_continuation_failed" in MAIN
    assert "not mcf_result.get(\"success\", False)" in MAIN
    assert "not mcf_result.get(\"skipped\", False)" in MAIN
