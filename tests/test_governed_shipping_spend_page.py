from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTING = (ROOT / "services" / "governed_shipping_spend_reporting.py").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "templates" / "fbm_shipping_spend.html").read_text(encoding="utf-8")


def test_spend_page_uses_same_db_report_authority_as_json():
    assert 'data = _report_data(' in REPORTING
    assert 'render_template("fbm_shipping_spend.html", report=data)' in REPORTING
    assert '"/fbm/shipping-spend/view"' in REPORTING


def test_spend_page_calls_it_actual_confirmed_dispatch_spend():
    assert "Actual confirmed dispatch spend from the BT38 database." in TEMPLATE
    assert "Confirmed dispatches" in TEMPLATE
    assert "Actual cost" in TEMPLATE


def test_spend_page_does_not_claim_missing_cost_is_zero():
    assert "Missing provider cost is unavailable, never £0." in TEMPLATE


def test_spend_page_keeps_provider_and_fulfilment_family_visible():
    assert "Spend by provider" in TEMPLATE
    assert "entry.fulfillment_family" in TEMPLATE
    assert "entry.provider" in TEMPLATE
