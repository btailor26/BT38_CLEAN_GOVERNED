from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = (ROOT / "services" / "governed_shipping_spend_reporting.py").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def test_shipping_spend_report_reads_only_persisted_confirmed_ledger():
    assert "ShippingSpendLedger.query.filter_by(confirmed=True)" in REPORT
    assert '"authority": "shipping_spend_ledger"' in REPORT
    assert '"actual_recorded_spend_only": True' in REPORT


def test_shipping_spend_report_is_dispatch_based_not_unit_based():
    assert '"dispatch_count": len(rows)' in REPORT
    assert "quantity" not in REPORT


def test_shipping_spend_report_keeps_families_and_providers_separate():
    assert '"provider_totals"' in REPORT
    assert '"family_totals"' in REPORT
    assert "ShippingSpendLedger.fulfillment_family" in REPORT
    assert "ShippingSpendLedger.provider" in REPORT


def test_unavailable_costs_are_not_reported_as_zero_entries():
    assert '"unavailable_costs_are_zero": False' in REPORT


def test_reporting_is_installed_after_spend_authority():
    assert "install_governed_shipping_spend_alignment(app)" in MAIN
    assert "install_governed_shipping_spend_reporting(app)" in MAIN
    assert MAIN.index("install_governed_shipping_spend_alignment(app)") < MAIN.index("install_governed_shipping_spend_reporting(app)")
