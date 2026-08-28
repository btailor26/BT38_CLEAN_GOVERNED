from pathlib import Path


def test_fbm_uses_one_governed_page_path():
    template = Path('templates/fbm.html').read_text(encoding='utf-8')
    compat = Path('services/governed_mcf_compat.py').read_text(encoding='utf-8')
    autosave = Path('services/fbm_operational_autosave.py').read_text(encoding='utf-8')
    state = Path('services/fbm_operational_state.py').read_text(encoding='utf-8')
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

    assert 'get_or_refresh_amazon_profile' in state
    assert 'hydrate_exact_ebay_order' in state
    assert 'prime_locked' in state
    assert 'promise_state' in state

    assert '/fbm/shipping-options' in client
    assert '/amazon/rates' in client
    assert '/packlink/rates' in client
    assert '/manual/dispatch' in client
    assert '/parcel' in client

    assert not Path('services/fbm_live_alignment.py').exists()
    assert not Path('static/js/fbm_live_alignment.js').exists()
    assert not Path('static/js/fbm_operational_alignment.js').exists()
