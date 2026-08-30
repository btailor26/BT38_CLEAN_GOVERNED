from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "services" / "governed_fbm_page_alignment.py").read_text(encoding="utf-8")


def test_fbm_health_defaults_to_today_and_supports_date_or_month():
    assert 'request.args.get("health_period") or "today"' in PAGE
    assert 'request.args.get("health_date")' in PAGE
    assert 'request.args.get("health_month")' in PAGE
    assert 'ZoneInfo("Europe/London")' in PAGE
    assert 'MarketplaceOrder.created_at >= start_at' in PAGE
    assert 'MarketplaceOrder.updated_at >= start_at' in PAGE


def test_health_is_db_backed_without_new_marketplace_or_provider_path():
    assert 'No marketplace/provider\n    request is performed here.' in PAGE
    assert 'db.session.query(MarketplaceOrder)' in PAGE
    assert 'health = _health_summary()' in PAGE
    assert 'app.view_functions[page_endpoint] = bounded_fbm_page' in PAGE
    assert 'fbm_health_endpoint' not in PAGE
    assert 'requests.get(' not in PAGE
    assert 'requests.post(' not in PAGE


def test_health_cards_cover_shipping_and_after_sale_lifecycle():
    for label in (
        '"Orders"',
        '"Ready to ship"',
        '"Dispatched"',
        '"Awaiting carrier"',
        '"Carrier overdue"',
        '"Returns"',
        '"Replacements"',
        '"Refunds / issues"',
        '"Mapping review"',
    ):
        assert label in PAGE
    for status in (
        '"return_requested"',
        '"returned"',
        '"replacement_requested"',
        '"replacement"',
        '"refund_requested"',
        '"refunded"',
        '"case_open"',
        '"dispute"',
        '"chargeback"',
    ):
        assert status in PAGE


def test_orders_card_breaks_down_marketplaces_on_hover_or_touch_focus():
    assert 'platform_counts' in PAGE
    assert 'role="tooltip"' in PAGE
    assert '.fbm-period-card:hover .fbm-period-tip' in PAGE
    assert '.fbm-period-card:focus .fbm-period-tip' in PAGE
    assert 'tabindex="0"' in PAGE


def test_shipping_motivation_uses_real_shipping_actions_only():
    assert 'shipping_actions = ready + overdue + mapping_review' in PAGE
    assert 'All shipping caught up' in PAGE
    assert 'Just 1 shipping action left' in PAGE
    assert 'shipping actions left' in PAGE
    assert 'You have {actions} shipping actions' in PAGE


def test_packlink_and_qz_setup_is_moved_beside_shipping_workspace():
    assert 'Shipping setup' in PAGE
    assert '<details class="fbm-shipping-setup mt-3">' in PAGE
    assert 'id="packlinkConnectionTest"' in PAGE
    assert 'id="qzConnect"' in PAGE
    assert 'shipping_orders_aligned_marker + _setup_html()' in PAGE
    assert '.fbm-top-grid{display:none!important}' in PAGE


def test_period_selection_survives_existing_bounded_paging():
    assert '("platform", "status", "health_period", "health_date", "health_month")' in PAGE
    assert '_FBM_HEALTH_MAX_ROWS = 5000' in PAGE
    assert '_FBM_MAX_EXPANDED = 300' in PAGE
