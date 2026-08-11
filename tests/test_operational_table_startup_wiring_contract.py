from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES_INIT = (ROOT / "services/__init__.py").read_text(encoding="utf-8")


def test_operational_table_read_alignment_waits_for_registered_routes():
    assert "before_request" in SERVICES_INIT
    assert "install_operational_table_read_alignment" in SERVICES_INIT
    assert "_bt38_operational_table_read_alignment_ready" in SERVICES_INIT


def test_operational_table_startup_wiring_does_not_add_worker_or_write_path():
    assert "first request" in SERVICES_INIT
    assert "replaces that existing reader in-place exactly once" in SERVICES_INIT
    assert "create a fallback execution path" in SERVICES_INIT
