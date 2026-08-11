from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = (ROOT / "static/js/bt38-operational-table-contract.js").read_text(encoding="utf-8")
PAGE_CONTROLLER = (ROOT / "static/js/bt38-page-controller.js").read_text(encoding="utf-8")
BASE = (ROOT / "templates/base.html").read_text(encoding="utf-8")


def test_shared_operational_table_contract_is_global_and_session_controlled():
    assert "bt38-operational-table-contract.js" in BASE
    assert "sessionStorage" in SHARED
    assert "[15, 25, 50, 100]" in SHARED
    assert 'serverPagedExpansion: true' in SHARED
    assert 'businessActionsChanged: false' in SHARED


def test_generic_page_controller_does_not_own_server_pagination():
    assert "function wirePagination" not in PAGE_CONTROLLER
    assert "wirePagination(page)" not in PAGE_CONTROLLER
    assert "hasServerPagination()" in PAGE_CONTROLLER
    assert "Server-rendered pages own the authoritative page/total count" in PAGE_CONTROLLER


def test_product_linking_keeps_its_own_exact_paging_owner():
    assert 'root.dataset.bt38Page === "productLinking"' in PAGE_CONTROLLER
    assert 'owner: "product-linking-session.js"' in PAGE_CONTROLLER
    assert "if (pageKey() === \"productLinking\") return;" in SHARED
