from pathlib import Path


def test_fbm_live_alignment_is_installed_and_prime_locked():
    compat = Path('services/governed_mcf_compat.py').read_text(encoding='utf-8')
    service = Path('services/fbm_live_alignment.py').read_text(encoding='utf-8')
    client = Path('static/js/fbm_live_alignment.js').read_text(encoding='utf-8')

    assert 'import services.fbm_live_alignment' in compat
    assert '@login_required' in service
    assert 'get_or_refresh_amazon_profile' in service
    assert 'hydrate_exact_ebay_order' in service
    assert 'promise_state(order, shipment)' in service
    assert "if(o.is_prime)" in client
    assert "btn.dataset.provider!=='amazon_buy_shipping'" in client
    assert "badge('Picked up'" in client
    assert "badge('In transit'" in client
    assert "deliveredText='Delivered'" in client
    assert "deliveredText='Delayed'" in client
    assert '/parcel' in client
    assert '1 · Picked up' not in client
    assert '2 · In transit' not in client
    assert '3 · Delivered' not in client
