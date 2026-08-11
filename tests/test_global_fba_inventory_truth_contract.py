from pathlib import Path
import ast


ADAPTER_SOURCE = Path("backend/adapters/amazon_sp_api_adapter.py").read_text(
    encoding="utf-8"
)
IMPORT_SOURCE = Path("services/governed_amazon_inventory_import.py").read_text(
    encoding="utf-8"
)
RUNTIME_SOURCE = Path("services/governed_runtime_engine.py").read_text(
    encoding="utf-8"
)
ALIGNMENT_SOURCE = Path(
    "services/governed_fba_settlement_ui_alignment.py"
).read_text(encoding="utf-8")
RECOVERY_SOURCE = Path(
    "services/governed_webhook_rejection_recovery.py"
).read_text(encoding="utf-8")

ADAPTER_TREE = ast.parse(ADAPTER_SOURCE)
IMPORT_TREE = ast.parse(IMPORT_SOURCE)
RUNTIME_TREE = ast.parse(RUNTIME_SOURCE)
ALIGNMENT_TREE = ast.parse(ALIGNMENT_SOURCE)
RECOVERY_TREE = ast.parse(RECOVERY_SOURCE)


def _function_source(tree: ast.AST, source: str, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {name}")


def test_every_fba_listing_uses_amazon_available_reserved_and_inbound_truth():
    adapter = _function_source(
        ADAPTER_TREE,
        ADAPTER_SOURCE,
        "get_inventory",
    )
    apply_row = _function_source(
        IMPORT_TREE,
        IMPORT_SOURCE,
        "_apply_inventory_row",
    )

    assert '"available_quantity": quantity_value(fulfillable)' in adapter
    assert '"reserved_quantity": quantity_value(' in adapter
    assert '"totalReservedQuantity"' in adapter
    assert '"inbound_quantity": quantity_value(' in adapter

    assert "inv.available_quantity = qty" in apply_row
    assert "inv.reserved_quantity = reserved" in apply_row
    assert "inv.inbound_quantity = inbound" in apply_row


def test_fba_order_quantity_never_becomes_inventory_truth():
    verify = _function_source(
        RUNTIME_TREE,
        RUNTIME_SOURCE,
        "_verify_exact_fba",
    )

    assert "AmazonSPAPIAdapter(store).get_inventory" in verify
    assert "seller_skus=[seller_sku]" in verify
    assert "order_quantity" not in verify
    assert "quantity -" not in verify
    assert "expected_quantity -" not in verify


def test_all_delayed_fba_changes_publish_existing_targeted_ui_event():
    publisher = _function_source(
        ALIGNMENT_TREE,
        ALIGNMENT_SOURCE,
        "_publish_delayed_fba_change",
    )
    installer = _function_source(
        ALIGNMENT_TREE,
        ALIGNMENT_SOURCE,
        "install_fba_settlement_ui_alignment",
    )

    assert "AmazonFBAInventory" in ALIGNMENT_SOURCE
    assert "publish_webhook_ui_event" in publisher
    assert "seller_sku" in publisher
    assert "order_id" in publisher
    assert "_run_light_reconcile_cycle" in installer
    assert "_publish_delayed_fba_change" in installer
    assert "full" not in publisher.lower() or "full-page" in ALIGNMENT_SOURCE


def test_restart_settlement_recovery_is_exact_for_any_fba_order():
    selector = _function_source(
        RECOVERY_TREE,
        RECOVERY_SOURCE,
        "_queue_stranded_durable_notifications",
    )

    assert "amazon_fba_inventory" in selector
    assert "INTERVAL '90 seconds'" in selector
    assert "mo.fulfillment_type" in selector
    assert "('FBA', 'AFN', 'AMAZON')" in selector
    assert "run_governed_marketplace_order_import" not in selector
    assert "run_governed_warehouse_sync" not in selector


def test_global_alignment_is_loaded_for_services_runtime():
    services_init = Path("services/__init__.py").read_text(encoding="utf-8")
    assert "governed_fba_settlement_ui_alignment" in services_init
