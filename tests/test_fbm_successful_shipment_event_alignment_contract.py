from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_shipment_event_alignment.py").read_text(encoding="utf-8")
SERVICES_INIT = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")
WORKFLOW = (ROOT / "docs" / "EVENT_DRIVEN_SESSION_WORKFLOW.md").read_text(encoding="utf-8")


def test_successful_terminal_event_uses_one_exact_existing_order_only():
    assert "process_marketplace_notification" in ALIGNMENT
    assert 'lifecycle.get("handled") is True' in ALIGNMENT
    assert 'lifecycle.get("terminal") is True' in ALIGNMENT
    assert 'MarketplaceOrder.marketplace_order_id == order_id' in ALIGNMENT
    assert "exact_store_identity_ambiguous" in ALIGNMENT
    assert "recover_bounded" not in ALIGNMENT
    assert "limit_per_store" not in ALIGNMENT
    assert "max_days" not in ALIGNMENT


def test_existing_purchased_authority_short_circuits_marketplace_readback():
    assert "_already_has_purchased_authority" in ALIGNMENT
    assert 'FBMShipment.purchase_status == "purchased"' in ALIGNMENT
    assert "FBMShipment.label_purchased_at.isnot(None)" in ALIGNMENT
    assert "purchased_shipment_authority_already_persisted" in ALIGNMENT


def test_amazon_event_reuses_existing_exact_readbacks_and_excludes_fba_mcf():
    assert "hydrate_amazon_tracking_for_order" in ALIGNMENT
    assert "hydrate_amazon_purchased_label_for_order" in ALIGNMENT
    assert '{"FBA", "AFN", "MCF", "AMAZON"}' in ALIGNMENT
    assert 'source="amazon_successful_lifecycle_event"' in ALIGNMENT


def test_ebay_event_reuses_existing_exact_fulfillment_finance_path():
    assert "hydrate_exact_ebay_order" in ALIGNMENT
    assert 'source="ebay_successful_lifecycle_event"' in ALIGNMENT
    assert "governed_ebay_shipping_label_finance_alignment" in SERVICES_INIT
    assert SERVICES_INIT.index("governed_ebay_shipping_label_finance_alignment") < SERVICES_INIT.index("governed_fbm_shipment_event_alignment")


def test_event_alignment_cannot_become_page_polling_or_background_recovery():
    for forbidden in (
        "before_request",
        "setInterval(",
        "while True",
        "Thread(",
        "scheduler",
        "startup recovery",
    ):
        assert forbidden.lower() not in ALIGNMENT.lower()

    assert "broad_scan_started" in ALIGNMENT
    assert "marketplace_write_started" in ALIGNMENT
    assert "No event = no work" in WORKFLOW
    assert "governed_fbm_shipment_event_alignment" in SERVICES_INIT
