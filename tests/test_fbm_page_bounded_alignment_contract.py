from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_page_alignment.py").read_text(encoding="utf-8")
DISPATCH_QUEUE = (ROOT / "services" / "governed_fbm_dispatch_queue_alignment.py").read_text(encoding="utf-8")
SEARCH = (ROOT / "services" / "governed_fbm_global_search_alignment.py").read_text(encoding="utf-8")
CLARITY = (ROOT / "services" / "governed_order_clarity_alignment.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "services" / "governed_notification_read_alignment.py").read_text(encoding="utf-8")
LEGACY_ROUTE = (ROOT / "governed_fbm_routes.py").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
EVENT_REFRESH = (ROOT / "static" / "js" / "fbm_event_session_refresh_alignment.js").read_text(encoding="utf-8")
GLOBAL_STATE = (ROOT / "static" / "js" / "bt38-global-state.js").read_text(encoding="utf-8")


def test_fbm_uses_one_bounded_session_snapshot_then_browser_local_paging():
    assert "_SESSION_MAX_ROWS = 300" in SEARCH
    assert "_SESSION_CANDIDATE_MULTIPLIER = 4" in SEARCH
    assert "def _session_snapshot_rows" in SEARCH
    assert "g._bt38_fbm_session_rows" in SEARCH
    assert "page_alignment._latest_distinct_fbm_rows = session_rows" in SEARCH
    assert "page_alignment._expand_control = no_server_expand" in SEARCH
    assert "perPage:15" in DISPATCH_QUEUE
    assert "[15,25,50,100]" in DISPATCH_QUEUE
    assert "window.BT38.getPageSession('fbm'" in DISPATCH_QUEUE
    assert "window.BT38.setPageSession('fbm'" in DISPATCH_QUEUE
    assert "sessionStorage" in GLOBAL_STATE


def test_fbm_session_discovery_is_bounded_before_business_truth_identity_selection():
    assert "candidate_limit = (_SESSION_MAX_ROWS * _SESSION_CANDIDATE_MULTIPLIER) + 1" in SEARCH
    assert ".order_by(MarketplaceOrder.id.desc())" in SEARCH
    assert ".limit(candidate_limit)" in SEARCH
    assert "def _canonical_order_rank" in SEARCH
    assert "def _canonical_order_rows" in SEARCH
    assert "func.max(MarketplaceOrder.id)" not in SEARCH
    assert "group_by(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id)" not in SEARCH


def test_fbm_search_and_workflow_controls_are_browser_local_not_new_db_requests():
    assert "install_governed_fbm_global_search_alignment" in CLARITY
    assert 'id="bt38FbmGlobalSearch"' in SEARCH
    assert "Search stays in this browser session." in SEARCH
    assert 'onsubmit="event.preventDefault();return false;"' in SEARCH
    assert "searchInput.addEventListener('input'" in DISPATCH_QUEUE
    assert "addWorkflowButton" in DISPATCH_QUEUE
    assert "button.addEventListener('click'" in DISPATCH_QUEUE
    assert "workflowHref" not in DISPATCH_QUEUE
    assert "addWorkflowLink" not in DISPATCH_QUEUE
    assert "query = query.filter(or_(" not in SEARCH
    assert "MarketplaceOrder.marketplace_order_id.ilike" not in SEARCH
    assert "requests." not in SEARCH
    assert "db.session.add" not in SEARCH
    assert "db.session.commit" not in SEARCH


def test_fbm_page_batches_profile_and_shipment_reads_instead_of_n_plus_one():
    assert "def _profile_map" in ALIGNMENT
    assert "tuple_(FBMOrderProfile.store_id, FBMOrderProfile.marketplace_order_id).in_(identities)" in ALIGNMENT
    assert "profiles = _profile_map(rows)" in ALIGNMENT
    assert "shipments = _shipment_map(rows)" in ALIGNMENT
    assert "def request_cached_profile_map" in SEARCH
    assert "def request_cached_shipment_map" in SEARCH
    assert "g._bt38_fbm_profile_cache" in SEARCH
    assert "g._bt38_fbm_shipment_cache" in SEARCH
    bounded_handler = ALIGNMENT.split("def bounded_fbm_page", 1)[1]
    assert "_profile_for(" not in bounded_handler


def test_fbm_alignment_keeps_existing_endpoint_template_and_action_routes():
    assert 'page_endpoint = "governed_fbm.fbm_page"' in ALIGNMENT
    assert 'shipping_options_endpoint = "governed_fbm.fbm_shipping_options"' in ALIGNMENT
    assert 'render_template(\n            "fbm.html"' in ALIGNMENT
    assert "app.view_functions[page_endpoint] = bounded_fbm_page" in ALIGNMENT
    assert "app.view_functions[shipping_options_endpoint] = bounded_shipping_options" in ALIGNMENT
    assert "install_governed_fbm_page_alignment(app)" in INSTALLER
    assert '@governed_fbm_bp.get("/fbm/shipping-options")' in LEGACY_ROUTE
    assert '@governed_fbm_bp.post("/fbm/orders/<int:order_id>/packlink/rates")' in LEGACY_ROUTE


def test_ebay_shipping_stays_inside_fbm_and_does_not_claim_native_label_capability():
    assert "def _workspace_shipping_mode" in ALIGNMENT
    assert 'platform.strip().lower() == "ebay"' in ALIGNMENT
    assert '"marketplace_buy_shipping": False' in ALIGNMENT
    assert '"recommended": "Packlink / connected carrier"' in ALIGNMENT
    assert "def _workspace_provider_options" in ALIGNMENT
    assert '"available": True' in ALIGNMENT
    assert "Open this exact order in eBay Seller Hub" in ALIGNMENT
    assert "BT38 does not purchase native eBay labels through an API" in ALIGNMENT
    assert "def _neutralise_legacy_ebay_handoff" in ALIGNMENT
    assert "eBay postage unavailable" not in ALIGNMENT
    assert "Native eBay label purchase is not enabled" not in ALIGNMENT
    assert "window.location.assign" not in ALIGNMENT


def test_ebay_shipping_observer_is_disabled_before_tracking_journey_script_runs():
    neutraliser = ALIGNMENT.split("def _neutralise_legacy_ebay_handoff", 1)[1].split("def _selected_row_parcel", 1)[0]
    assert 'marker = \'id="fbmShippingOrders"\'' in neutraliser
    assert 'data-ebay-shipping-handoff-installed="1"' in neutraliser
    assert "html.replace(" in neutraliser
    assert "return html" in neutraliser


def test_ebay_shipping_options_open_without_amazon_profile_or_warehouse_prefetch():
    shipping_handler = ALIGNMENT.split("def bounded_shipping_options", 1)[1]
    assert ".options(joinedload(MarketplaceOrder.store))" in shipping_handler
    assert "joinedload(MarketplaceOrder.warehouse_stock)" not in shipping_handler
    assert "amazon_rows = [" in shipping_handler
    assert 'if _platform(row).strip().lower() == "amazon"' in shipping_handler
    assert "profiles = _profile_map(amazon_rows)" in shipping_handler
    assert 'profile = profiles.get(key) if platform == "amazon" else None' in shipping_handler
    assert '"parcel": _selected_row_parcel(row)' in shipping_handler
    assert "def _persisted_pack_mapping_parcel" in ALIGNMENT
    assert '"source": "pack_mapping"' in ALIGNMENT


def test_fbm_workspace_requires_positive_merchant_fulfilled_truth_for_amazon():
    assert "def _workspace_fbm_eligible" in ALIGNMENT
    helper = ALIGNMENT.split("def _workspace_fbm_eligible", 1)[1].split("def _latest_distinct_fbm_rows", 1)[0]
    assert 'profile_channel in {"AFN", "FBA", "MCF"}' in helper
    assert 'profile_channel in {"MFN", "FBM"}' in helper
    assert 'return fulfillment in {"MFN", "FBM"}' in helper
    page_handler = ALIGNMENT.split("def bounded_fbm_page", 1)[1].split("def bounded_shipping_options", 1)[0]
    shipping_handler = ALIGNMENT.split("def bounded_shipping_options", 1)[1]
    assert "if not _workspace_fbm_eligible(row, profile):" in page_handler
    assert "if _workspace_fbm_eligible(row, profile):" in shipping_handler


def test_bounded_page_read_is_persisted_read_only_and_does_not_touch_mcf_execution():
    assert "db.session.add" not in ALIGNMENT
    assert "db.session.commit" not in ALIGNMENT
    assert "requests." not in ALIGNMENT
    assert "get_or_refresh_amazon_profile" not in ALIGNMENT
    assert "process_marketplace_notification" not in ALIGNMENT
    assert "MCFOrder" not in ALIGNMENT
    assert '"FBA", "AFN", "MCF"' in ALIGNMENT


def test_fbm_lifecycle_tabs_preserve_one_workspace_and_use_browser_session_scope():
    assert "cloneNode" not in DISPATCH_QUEUE
    assert "card.remove()" not in DISPATCH_QUEUE
    assert "fbm-dispatch-history" not in DISPATCH_QUEUE
    assert "var table=document.querySelector('.fbm-orders-table')" in DISPATCH_QUEUE
    assert "Ready to dispatch" in DISPATCH_QUEUE
    assert "Dispatched" in DISPATCH_QUEUE
    assert "FBA" in DISPATCH_QUEUE
    assert "Replacement" in DISPATCH_QUEUE
    assert "Refunds" in DISPATCH_QUEUE
    assert "addTruthLink(tabBar,'MCF'" not in DISPATCH_QUEUE
    assert "Carrier overdue" not in DISPATCH_QUEUE
    assert "Mapping review" not in DISPATCH_QUEUE
    assert "workflow_queue_for" in DISPATCH_QUEUE
    assert "addWorkflowButton" in DISPATCH_QUEUE
    assert "readyToShipSelected" in DISPATCH_QUEUE
    assert "fbm-shipping-options" in DISPATCH_QUEUE
    assert '"ready_dispatch": "Ready to dispatch"' in DISPATCH_QUEUE
    assert "Cofi" in DISPATCH_QUEUE


def test_fbm_workflow_scope_reuses_one_canonical_session_truth():
    assert "def _persisted_workflow_snapshot" in SEARCH
    assert "def _canonical_order_rank" in SEARCH
    assert "def _canonical_order_rows" in SEARCH
    assert "rows, truncated = _session_snapshot_rows()" in SEARCH
    assert "page_alignment._workspace_fbm_eligible" in SEARCH
    assert "shipments = page_alignment._shipment_map(rows)" in SEARCH
    assert "def workflow_queue_for" in SEARCH
    assert 'return "dispatched" if dispatched else "ready_dispatch"' in SEARCH
    assert 'return "sds"' in SEARCH
    assert "def workflow_counts" in SEARCH
    assert "_WORKFLOW_MAX_ROWS" not in SEARCH
    assert "20001" not in SEARCH
    assert "requests." not in SEARCH
    assert "db.session.add" not in SEARCH
    assert "db.session.commit" not in SEARCH


def test_sds_tab_does_not_treat_selection_alone_as_committed_dispatch():
    assert 'purchase_status in {"confirmed", "purchased", "committed"}' in SEARCH
    assert 'purchase_status in {"selected"}' not in SEARCH
    assert "label_purchased_at" in SEARCH
    assert "carrier_accepted_at" in SEARCH
    assert "first_movement_at" in SEARCH
    assert "delivered_at" in SEARCH


def test_fbm_lifecycle_tabs_reuse_confirmed_shipping_spend_and_never_invent_zero_cost():
    assert "ShippingSpendLedger.confirmed.is_(True)" in DISPATCH_QUEUE
    assert "ShippingSpendLedger.shipment_id.in_(shipment_ids)" in DISPATCH_QUEUE
    assert '"shipping_cost": float(spend.amount) if spend is not None else None' in DISPATCH_QUEUE
    assert "Pending / unavailable" in DISPATCH_QUEUE
    assert "£0.00" not in DISPATCH_QUEUE
    assert "db.session.add" not in DISPATCH_QUEUE
    assert "db.session.commit" not in DISPATCH_QUEUE


def test_fbm_marketplace_event_marks_session_dirty_and_hidden_page_sleeps_without_full_get():
    assert "bt38-marketplace-event" in EVENT_REFRESH
    assert "document.visibilityState === 'hidden'" in EVENT_REFRESH
    assert "BT38.setPageSession('fbm'" in EVENT_REFRESH
    assert "dirty: true" in EVENT_REFRESH
    assert "fetch(window.location.href" not in EVENT_REFRESH
    assert "setInterval" not in EVENT_REFRESH
    assert "EventSource" not in EVENT_REFRESH
    assert "window.location.reload()" in EVENT_REFRESH


def test_dispatch_split_is_registered_after_persisted_fbm_scope_and_health():
    assert "install_governed_notification_read_alignment(app)" in MAIN
    assert "install_governed_fbm_page_alignment(app)" in MAIN
    assert "install_governed_fbm_global_search_alignment(app)" in MAIN
    assert "install_governed_fbm_all_orders_health_alignment(app)" in MAIN
    assert "install_governed_fbm_dispatch_queue_alignment(app)" in MAIN
    assert MAIN.index("install_governed_fbm_page_alignment(app)") < MAIN.index("install_governed_fbm_global_search_alignment(app)")
    assert MAIN.index("install_governed_fbm_global_search_alignment(app)") < MAIN.index("install_governed_fbm_all_orders_health_alignment(app)")
    assert MAIN.index("install_governed_fbm_all_orders_health_alignment(app)") < MAIN.index("install_governed_fbm_dispatch_queue_alignment(app)")
