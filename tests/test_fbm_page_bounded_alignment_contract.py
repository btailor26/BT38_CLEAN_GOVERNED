from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALIGNMENT = (ROOT / "services" / "governed_fbm_page_alignment.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "services" / "governed_notification_read_alignment.py").read_text(encoding="utf-8")
LEGACY_ROUTE = (ROOT / "governed_fbm_routes.py").read_text(encoding="utf-8")


def test_fbm_default_page_is_exactly_fifteen_orders_and_expands_in_fifteens():
    assert "_FBM_PAGE_SIZE = 15" in ALIGNMENT
    assert 'request.args.get("limit") or _FBM_PAGE_SIZE' in ALIGNMENT
    assert "limit + 1" in ALIGNMENT
    assert 'id="fbmExpandOrders"' in ALIGNMENT
    assert "visible_limit + _FBM_PAGE_SIZE" in ALIGNMENT
    assert "Show 15 more" in ALIGNMENT
    assert "Show latest 15" in ALIGNMENT


def test_fbm_latest_order_discovery_is_bounded_before_distinct_identity_selection():
    assert "_FBM_DISCOVERY_MULTIPLIER = 4" in ALIGNMENT
    assert "candidate_limit = min(" in ALIGNMENT
    assert ".order_by(MarketplaceOrder.id.desc())" in ALIGNMENT
    assert ".limit(candidate_limit)" in ALIGNMENT
    assert "seen: set[tuple[int, str]] = set()" in ALIGNMENT
    assert "if key in seen:" in ALIGNMENT
    assert "if len(rows) >= limit + 1:" in ALIGNMENT
    assert "group_by(MarketplaceOrder.store_id, MarketplaceOrder.marketplace_order_id)" not in ALIGNMENT
    assert "func.max(MarketplaceOrder.id)" not in ALIGNMENT


def test_fbm_page_batches_profile_and_shipment_reads_instead_of_n_plus_one():
    assert "def _profile_map" in ALIGNMENT
    assert "tuple_(FBMOrderProfile.store_id, FBMOrderProfile.marketplace_order_id).in_(identities)" in ALIGNMENT
    assert "profiles = _profile_map(rows)" in ALIGNMENT
    assert "shipments = _shipment_map(rows)" in ALIGNMENT
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
    assert '"parcel": _selected_row_parcel(row) if platform == "amazon" else _empty_selected_parcel()' in shipping_handler
    assert '"source": "selected_row_deferred_parcel"' in ALIGNMENT


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
