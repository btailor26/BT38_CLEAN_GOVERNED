from pathlib import Path


CONTROLLER = Path("static/js/bt38-page-controller.js")
PRODUCT_LINKING_SESSION = Path("static/js/product-linking-session.js")
PRODUCT_LINKING = Path("templates/product_linking.html")
WAREHOUSE = Path("templates/warehouse.html")
WAREHOUSE_ROUTE = Path("governed_routes.py")
WAREHOUSE_GOVERNED_JS = Path("static/js/warehouse-governed.js")
WAREHOUSE_RUNTIME_VISIBILITY = Path("governed_runtime_visibility_routes.py")
STORES = Path("templates/stores.html")


def _source(path):
    return path.read_text(encoding="utf-8")


def test_product_linking_has_one_authoritative_session_controller():
    shared = _source(CONTROLLER)
    session = _source(PRODUCT_LINKING_SESSION)

    assert 'owner: "product-linking-session.js"' in shared
    assert "installLocalProductLinkingSearch" not in shared
    assert "configureCompleteWorkingSets" not in shared
    assert "wireAsyncProductLinking" not in shared
    assert "window.searchWarehouseForLinking = function" in session
    assert "window.filterFlatListings = function" in session


def test_product_linking_session_persists_until_an_exact_change_then_paginates_locally():
    source = _source(PRODUCT_LINKING_SESSION)

    assert "FULL_DATASET_LIMIT = 5000" in source
    assert "CACHE_TTL_MS" not in source
    assert "fetchFullSnapshotOnceDaily" not in source
    assert "fetchInitialSnapshotOnce" in source
    assert "snapshotExists" in source
    assert "window.indexedDB.open" in source
    assert "state.filtered = state.products.filter" in source
    assert "state.filtered.slice(start, start + state.perPage)" in source
    assert "perPage: 15" in source


def test_product_linking_changes_merge_only_affected_server_state():
    source = _source(PRODUCT_LINKING_SESSION)

    assert "window.linkListingToWarehouse = async function" in source
    assert "window.unlinkListing = function" in source
    assert "async function confirmExplicitUnlink()" in source

    link_start = source.index("window.linkListingToWarehouse = async function")
    unlink_prepare_start = source.index("window.unlinkListing = function", link_start)
    unlink_confirm_start = source.index("async function confirmExplicitUnlink()", unlink_prepare_start)
    wire_start = source.index("function wire()", unlink_confirm_start)

    link_block = source[link_start:unlink_prepare_start]
    unlink_prepare_block = source[unlink_prepare_start:unlink_confirm_start]
    unlink_confirm_block = source[unlink_confirm_start:wire_start]

    assert "fetch(" not in unlink_prepare_block
    assert "pendingExplicitUnlink = {" in unlink_prepare_block

    assert "/unlink" in unlink_confirm_block
    assert "user_confirmed: true" in unlink_confirm_block

    assert "applyCommittedRelationshipLocally(" in link_block
    assert "applyCommittedRelationshipLocally(" in unlink_confirm_block
    assert "await applyMutationContract(relationshipEvent, {" not in link_block
    assert "await applyMutationContract(data, {" not in unlink_confirm_block

    assert "await refreshAffectedRecord({" in link_block
    assert "Never retry the write automatically" in link_block

    assert "await clearSnapshot();" not in link_block
    assert "window.location.reload();" not in link_block
    assert "await clearSnapshot();" not in unlink_confirm_block
    assert "window.location.reload();" not in unlink_confirm_block

    assert "await hydrate(true)" not in source


def test_product_linking_has_no_timer_or_focus_refresh_path():
    source = _source(PRODUCT_LINKING_SESSION)
    boot = source[source.index("function boot()") :]

    assert "setTimeout" not in source
    assert "setInterval" not in source
    assert "visibilitychange" not in boot
    assert 'addEventListener("focus"' not in boot
    assert "refreshVisibleProductLinkingOnce" not in source


def test_shared_page_controller_searches_cached_rows_then_paginates_locally():
    source = _source(CONTROLLER)

    assert "page.filteredRows = page.rows.filter" in source
    assert "page.filteredRows.slice(start, end)" in source
    assert "page.currentPage = 1" in source
    assert "event.preventDefault()" in source


def test_default_page_size_is_15_and_can_expand_without_server_request():
    source = _source(CONTROLLER)

    assert "const allowedPageSizes = [15, 25, 50, 100]" in source
    assert "perPage: 15" in source
    assert "allowedPageSizes.includes(parsed) ? parsed : 15" in source
    assert 'select.addEventListener("change"' in source


def test_warehouse_route_loads_complete_relevant_listing_working_set_once():
    source = _source(WAREHOUSE_ROUTE)
    start = source.index("# Design B:")
    end = source.index("rows = []", start)
    block = source[start:end]

    assert "Load the complete relevant Warehouse dataset once" in block
    assert ".all()" in block
    assert ".limit(" not in block
    assert ".offset(" not in block


def test_warehouse_kpis_use_distinct_truth_sources():
    source = _source(CONTROLLER)

    assert "Active linked listings" in source
    assert "/governed/warehouse/economics-batch?stock_ids=" in source
    assert "stock × COGS" in source
    assert "missing COGS" in source
    assert "/governed/warehouse/runtime-state" in source
    assert "Runtime healthy" in source
    assert "Loaded stock × price" not in source
    assert "Loaded marketplace listings" not in source


def test_warehouse_navigation_is_wired_to_existing_pages():
    source = _source(CONTROLLER)

    assert '"master stock": "/warehouse"' in source
    assert '"fba read only": "/fba-inventory"' in source
    assert '"group view": "/product-linking"' in source
    assert '"orders": "/orders"' in source


def test_templates_keep_required_controller_scripts():
    product_linking = _source(PRODUCT_LINKING)
    warehouse = _source(WAREHOUSE)

    assert "bt38-global-state.js" in product_linking
    assert "bt38-page-controller.js" in product_linking
    assert "bt38-page-controller.js" in warehouse


def test_warehouse_profit_replaces_duplicate_row_action_and_keeps_compact_selection_bar():
    warehouse = _source(WAREHOUSE)

    assert "<th>Profit</th>" in warehouse
    assert 'class="bt38-profit-action"' in warehouse
    assert '>Action<' not in warehouse
    assert '<span id="bt38SelectedCount">0</span> selected' in warehouse
    assert '<option value="">Action…</option>' in warehouse
    assert 'aria-label="Clear selection"' in warehouse


def test_warehouse_profitability_has_separate_auto_and_what_if_sections():
    warehouse = _source(WAREHOUSE)
    controller = _source(WAREHOUSE_GOVERNED_JS)

    assert "AUTO · MARKETPLACE" in warehouse
    assert "CALCULATOR · WHAT IF?" in warehouse
    assert "Calculator only. It does not save or update a marketplace listing." in warehouse
    assert "bt38AutoDefaultsSave" in controller
    assert "Save warehouse defaults" in controller
    assert "recalcWhatIf" in controller
    assert "warehouse-economics" in controller


def test_warehouse_profit_cells_batch_hydrate_from_local_economics_only():
    controller = _source(WAREHOUSE_GOVERNED_JS)
    runtime = _source(WAREHOUSE_RUNTIME_VISIBILITY)

    assert "loadVisibleProfitability" in controller
    assert "/governed/warehouse/economics-batch?stock_ids=" in controller
    assert "renderRowProfit" in controller
    assert "loadVisibleProfitability();" in controller
    assert ".filter(row => !row.hidden)" in controller
    assert "scheduleVisibleProfitabilityRefresh" in controller
    assert "e.stopImmediatePropagation();" in controller
    assert '"marketplace_calls": False' in runtime


def test_warehouse_economics_save_refreshes_only_the_saved_row():
    controller = _source(WAREHOUSE_GOVERNED_JS)
    start = controller.index("const save = e.target")
    end = controller.index("document.addEventListener('input'", start)
    block = controller[start:end]

    assert "if (row) await loadEconomics(row);" in block
    assert "await loadVisibleProfitability();" not in block


def test_warehouse_runtime_heartbeat_reads_fuse_config_in_one_query():
    runtime = _source(WAREHOUSE_RUNTIME_VISIBILITY)
    start = runtime.index("def governed_warehouse_runtime_state")
    end = runtime.index("def governed_warehouse_economics_batch", start)
    block = runtime[start:end]

    assert "SystemConfig.key.in_(fuse_keys)" in block
    assert ".all()" in block
    assert "filter_by(key=key).first()" not in block


def test_warehouse_profit_batch_never_becomes_a_marketplace_write_path():
    controller = _source(WAREHOUSE_GOVERNED_JS)
    runtime = _source(WAREHOUSE_RUNTIME_VISIBILITY)

    batch_start = runtime.index('def governed_warehouse_economics_batch')
    single_start = runtime.index('def governed_warehouse_economics(stock_id', batch_start)
    batch_block = runtime[batch_start:single_start]

    assert "MarketplaceListing" not in batch_block
    assert "push_marketplace_listing" not in batch_block
    assert "amazon" not in batch_block.lower()
    assert "ebay" not in batch_block.lower()
    assert "postJson(`/governed/warehouse/economics-batch" not in controller


def test_warehouse_economics_save_is_local_only_and_reuses_existing_cost_fields():
    source = _source(WAREHOUSE_RUNTIME_VISIBILITY)

    assert '/governed/warehouse/<int:stock_id>/economics' in source
    assert '/governed/warehouse/economics-batch' in source
    assert '"unit_cost": "unit_cost"' in source
    assert '"product_weight_kg": "product_weight_kg"' in source
    assert '"shipping_cost_per_kg": "shipping_cost_per_kg"' in source
    assert '"commission_rate": "commission_rate"' in source
    assert '"marketplace_write": False' in source
    assert '"marketplace_calls": False' in source
    assert "push_marketplace_listing" not in source


def test_ebay_commercial_oauth_uses_one_store_aware_authorization_code_flow():
    routes = _source(WAREHOUSE_ROUTE)
    authorize = routes.split("def governed_ebay_oauth_authorize():", 1)[1]
    authorize = authorize.split("def governed_ebay_oauth_callback():", 1)[0]
    callback = routes.split("def governed_ebay_oauth_callback():", 1)[1]
    callback = callback.split("def governed_ebay_oauth_refresh_token():", 1)[0]

    assert "governed_ebay_oauth_scopes()" in authorize
    assert '"response_type": "code"' in authorize
    assert '"redirect_uri": runame' in authorize
    assert 'session["governed_ebay_oauth_store_id"] = store.id' in authorize
    assert "auth.ebay.com/oauth2/authorize" in authorize
    assert "signin.ebay.com/ws/eBayISAPI.dll" not in authorize

    assert '"grant_type": "authorization_code"' in callback
    assert 'selected_store_id = session.get("governed_ebay_oauth_store_id")' in callback
    assert "_resolve_governed_ebay_oauth_store(selected_store_id)" in callback
    assert '"refresh_token": token.get("refresh_token") or existing.get("refresh_token")' in callback
    assert '"oauth_requested_scope": scopes' in callback
    assert "ensure_ebay_order_notification_registration(" in callback
    assert 'return redirect(f"/stores?ebay_oauth=success&store_id={store.id}")' in callback


def test_ebay_store_ui_never_uses_developer_portal_or_legacy_authnauth():
    stores = _source(STORES)

    assert "developer.ebay.com" not in stores
    assert "signin.ebay.com/ws/eBayISAPI.dll" not in stores
    assert "/ebay-oauth/authorize?store_id={{ store.id }}" in stores


def test_ebay_store_ui_uses_persisted_auth_state_not_credentials():
    stores = _source(STORES)

    assert "store.auth_status == 'auth_error'" in stores
    assert "store.auth_error_code == 'ebay_notification_reauthorization_required'" in stores
    assert "'AUTHORIZATION_REQUIRED' in store.api_key" not in stores
    assert "'Insufficient permissions' in store.api_key" not in stores
    assert "Permission approval required" in stores
    assert "Approve eBay" in stores