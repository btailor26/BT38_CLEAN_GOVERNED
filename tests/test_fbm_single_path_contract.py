from pathlib import Path


def test_fbm_uses_one_governed_page_path():
    template = Path('templates/fbm.html').read_text(encoding='utf-8')
    compat = Path('services/governed_mcf_compat.py').read_text(encoding='utf-8')
    autosave = Path('services/fbm_operational_autosave.py').read_text(encoding='utf-8')
    state = Path('services/fbm_operational_state.py').read_text(encoding='utf-8')
    routes = Path('governed_fbm_routes.py').read_text(encoding='utf-8')
    client = Path('static/js/fbm_shipping_desk.js').read_text(encoding='utf-8')

    assert 'order.fbm_view_state' in template
    assert 'bt38-prime-badge' in template
    assert '>Picked up<' in template
    assert '>In transit<' in template
    assert '1 · Picked up' not in template
    assert '2 · In transit' not in template
    assert '3 · Delivered' not in template
    assert 'fbm_shipping_desk.js' in template
    assert 'fbm_live_alignment.js' not in template
    assert 'fbm_operational_alignment.js' not in template

    assert 'fbm_live_alignment' not in compat
    assert '@app.after_request' not in autosave
    assert '/fbm/orders/operational-status' not in autosave
    assert '/fbm/alignment-snapshot' not in autosave

    # Initial /fbm rendering must be DB-only: no synchronous marketplace reads
    # and no per-row DDL/table-existence work may occur in the view-state module.
    assert 'get_or_refresh_amazon_profile' not in state
    assert 'hydrate_exact_ebay_order' not in state
    assert '__table__.create' not in state
    assert 'ensure_operational_table' not in state
    assert 'fbm_view_state' in state
    assert '_promise_from_loaded_state' in state
    assert 'prime_locked' in state

    # Exact marketplace verification remains on the existing governed
    # interaction path rather than page rendering.
    assert 'get_or_refresh_amazon_profile' in routes
    assert '@governed_fbm_bp.get("/fbm/shipping-options")' in routes
    assert '_amazon_profile(row)' in routes

    assert '/fbm/shipping-options' in client
    assert '/amazon/rates' in client
    assert '/packlink/rates' in client
    assert '/manual/dispatch' in client
    assert '/parcel' in client

    assert not Path('services/fbm_live_alignment.py').exists()
    assert not Path('static/js/fbm_live_alignment.js').exists()
    assert not Path('static/js/fbm_operational_alignment.js').exists()
