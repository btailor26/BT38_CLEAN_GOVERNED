
from pathlib import Path


IMPORTER = Path("services/governed_marketplace_order_import.py").read_text(
    encoding="utf-8"
)

MUTATION = Path("services/governed_order_stock_mutation.py").read_text(
    encoding="utf-8"
)

ROUTES = Path("governed_routes.py").read_text(
    encoding="utf-8"
)


def _function_block(source: str, function_name: str) -> str:
    marker = f"def {function_name}("
    start = source.index(marker)

    next_function = source.find("\ndef ", start + len(marker))
    next_route = source.find("\n@", start + len(marker))

    candidates = [
        position
        for position in (next_function, next_route)
        if position != -1
    ]

    end = min(candidates) if candidates else len(source)
    return source[start:end]


def test_importer_processes_only_exact_upserted_order():
    assert '"_order_row": order' in IMPORTER
    assert "def _process_exact_imported_order(" in IMPORTER
    assert "process_exact_marketplace_order_line(" in IMPORTER

    process_block = _function_block(
        IMPORTER,
        "_process_exact_imported_order",
    )

    assert "MarketplaceOrder.query" not in process_block
    assert "db.session.query(MarketplaceOrder)" not in process_block
    assert ".filter_by(status=" not in process_block


def test_exact_fbm_and_ebay_orders_use_warehouse_mutation():
    exact_block = _function_block(
        MUTATION,
        "process_exact_marketplace_order_line",
    )

    assert 'fulfillment in {"FBA", "AFN"}' in exact_block
    assert "mutate_warehouse_stock_from_order_line(" in exact_block

    assert "MCF" not in exact_block
    assert "mcf" not in exact_block


def test_warehouse_mutation_changes_database_stock():
    mutation_block = _function_block(
        MUTATION,
        "mutate_warehouse_stock_from_order_line",
    )

    assert "stock.available_quantity = after_available" in mutation_block
    assert "db.session.commit()" in mutation_block
    assert "processed_at" in mutation_block


def test_warehouse_page_expires_cached_session_state():
    warehouse_block = _function_block(
        ROUTES,
        "governed_warehouse_page",
    )

    assert "db.session.expire_all()" in warehouse_block
    assert ".populate_existing()" in warehouse_block


def test_linked_fbm_warehouse_ui_uses_sellable_quantity():
    warehouse_block = _function_block(
        ROUTES,
        "governed_warehouse_page",
    )

    assert "is_fbm = is_amazon and channel in" in warehouse_block
    assert "int(stock.sellable_quantity or 0)" in warehouse_block

    # Marketplace quantity may remain for unlinked rows and variation
    # diagnostics, but it must not replace warehouse truth for standard
    # linked FBM rows.
    assert "else int(stock.sellable_quantity or 0)" in warehouse_block


def test_warehouse_totals_use_database_stock_truth():
    warehouse_block = _function_block(
        ROUTES,
        "governed_warehouse_page",
    )

    assert (
        'total_available = sum(int(getattr(stock, "sellable_quantity", 0) or 0)'
        in warehouse_block
    )

    assert (
        'low_stock_count = sum(1 for stock in active_stock_rows '
        'if int(getattr(stock, "sellable_quantity", 0) or 0) <= 0)'
        in warehouse_block
    )


def test_product_linking_search_warehouse_is_read_only_wrapper():
    wrapper_block = _function_block(
        ROUTES,
        "governed_product_linking_search_warehouse_compat",
    )

    assert "governed_product_linking_data_compat()" in wrapper_block
    assert "return data_response" in wrapper_block

    forbidden_mutations = (
        "stock.sellable_quantity =",
        "stock.available_quantity =",
        "stock.quantity =",
        "listing.last_marketplace_qty =",
        "listing.last_push_quantity =",
    )

    for mutation in forbidden_mutations:
        assert mutation not in wrapper_block


def test_product_linking_is_relationship_only_and_warehouse_remains_truth():
    api_block = _function_block(
        ROUTES,
        "governed_product_linking_data_compat",
    )

    # Product Linking exposes mappings and relationship groups only.
    assert "listings_by_stock" in api_block
    assert '"warehouse_stock_id"' in api_block
    assert '"master_product_group_id"' in api_block
    assert '"linked_count"' in api_block
    assert '"listings"' in api_block

    # Non-FBA display quantity comes from WarehouseStock.
    assert 'else getattr(stock, "sellable_quantity", 0)' in api_block

    # Marketplace observations must not become warehouse quantity authority.
    assert "last_marketplace_qty" not in api_block
    assert "last_push_quantity" not in api_block

    # All returned warehouse quantity aliases use one resolved display value.
    assert '"quantity": display_quantity' in api_block
    assert '"available_quantity": display_quantity' in api_block
    assert '"sellable_quantity": display_quantity' in api_block


def test_product_linking_data_does_not_mutate_warehouse_quantity():
    api_block = _function_block(
        ROUTES,
        "governed_product_linking_data_compat",
    )

    forbidden_mutations = (
        "stock.sellable_quantity =",
        "stock.available_quantity =",
        "stock.quantity =",
        "listing.last_marketplace_qty =",
        "listing.last_push_quantity =",
    )

    for mutation in forbidden_mutations:
        assert mutation not in api_block


def test_warehouse_page_remains_non_fba_quantity_authority():
    warehouse_block = _function_block(
        ROUTES,
        "governed_warehouse_page",
    )

    assert "db.session.expire_all()" in warehouse_block
    assert "int(stock.sellable_quantity or 0)" in warehouse_block

    # Standard linked FBM and eBay warehouse rows must use WarehouseStock.
    assert "else int(stock.sellable_quantity or 0)" in warehouse_block


def test_alignment_change_does_not_touch_mcf_configuration():
    combined = IMPORTER + "\n" + MUTATION

    forbidden_configuration_terms = (
        "mcf_enabled =",
        "mcf_auto",
        "enable_mcf",
        "disable_mcf",
        "mcf_setting",
        "mcf_store",
    )

    for term in forbidden_configuration_terms:
        assert term not in combined
