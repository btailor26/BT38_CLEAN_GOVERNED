from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_product_linking_group_push_is_only_a_warehouse_shortcut():
    route_source = _source("governed_group_propagation_routes.py")

    assert "Thin adapter into the single governed group push service" in route_source
    assert "from services.governed_push_execution import push_group_listings" in route_source
    assert "authority_warehouse_stock_id=requested_warehouse_stock_id" in route_source
    assert "quantity authority is resolved inside services.governed_push_execution" in route_source.lower()

    # The shortcut identifies relationship/Warehouse authority only. It must not
    # recreate a marketplace writer or accept request-body quantity authority.
    assert "submit_governed_marketplace_action" not in route_source
    assert 'body.get("quantity")' not in route_source
    assert "target_quantity = body" not in route_source


def test_shared_push_service_keeps_warehouse_quantity_and_fba_protection():
    push_source = _source("services/governed_push_execution.py")

    assert "request body quantity does not override warehouse truth" in push_source
    assert "one Warehouse row supplies one shared target quantity" in push_source
    assert "authority_warehouse_stock_id" in push_source
    assert "push_group_listings(" in push_source
    assert "FBA/AFN is read-only. Never call the marketplace writer for it." in push_source
    assert '"push_status": "read_only"' in push_source
    assert "submit_governed_marketplace_action(" in push_source


def test_webhook_uses_current_group_and_changed_warehouse_as_authority():
    webhook_source = _source("services/governed_webhook_execution.py")

    assert "group_id = listing_group_id or stock_group_id" in webhook_source
    assert "Current Product Linking relationship is authoritative for correction" in webhook_source
    assert "authority_warehouse_stock_id=stock.id" in webhook_source
    assert 'source=f"webhook_{marketplace}_group_notification"' in webhook_source
    assert "apply_governed_amazon_fba_event(" in webhook_source


def test_ui_handoff_preserves_all_affected_records_without_db_polling():
    signal_source = _source("services/governed_ui_event_signal.py")
    product_linking_source = _source("static/js/product-linking-session.js")

    for key in (
        "affected_listing_ids",
        "affected_warehouse_stock_ids",
        "affected_group_ids",
    ):
        assert key in signal_source

    assert "_latest_event" not in signal_source
    assert "db.session" not in signal_source
    assert "window.location.reload()" not in signal_source
    assert "setInterval(" not in signal_source
    assert "bt38-marketplace-event" in signal_source

    assert "bt38-marketplace-event" in product_linking_source
    assert "bt38ApplyProductLinkingMutation" in product_linking_source
    assert "affected_listing_ids" in product_linking_source
    assert "affected_warehouse_stock_ids" in product_linking_source
    assert "affected_group_ids" in product_linking_source


def test_workflow_document_freezes_one_clear_path_contract():
    workflow = _source("docs/PSS_GOVERNED_WORKFLOW.md")
    release_gate = _source("docs/PRODUCT_LINKING_RELEASE_GATE.md")

    assert "Non-Negotiable One-Clear-Path Contract" in workflow
    assert "Warehouse is the only authority for FBM/eBay sellable inventory quantity" in workflow
    assert "Product Linking Push / push settings are shortcuts" in workflow
    assert "No committed change means no UI wake" in workflow
    assert "within 2 seconds" in workflow

    assert "Permanent authority invariant" in release_gate
    assert "Warehouse remains the sole FBM/eBay quantity authority" in release_gate
    assert "A correct final number reached through a second path is still a release-gate FAIL" in release_gate
    assert "within 2 seconds" in release_gate
