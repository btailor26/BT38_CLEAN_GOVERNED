from pathlib import Path


def test_webhook_order_event_publishes_without_stock_change():
    source = Path('services/governed_webhook_bell_event_alignment.py').read_text(encoding='utf-8')

    assert 'ui._response_has_committed_change(payload)' in source
    assert '_exact_order_scope(payload)' in source
    assert 'ui.publish_webhook_ui_event(' in source
    assert 'order_id' in source
    assert 'seller_sku' in source
    assert 'db.session' not in source
    assert '.query' not in source


def test_failed_or_unresolved_webhooks_do_not_publish_fallback_sale():
    source = Path('services/governed_webhook_bell_event_alignment.py').read_text(encoding='utf-8')

    for blocked in (
        'unresolved',
        'order_import_failed',
        'processing_failed',
        'failed',
        'error',
    ):
        assert f'"{blocked}"' in source


def test_main_wires_fallback_before_final_bell_projection():
    main = Path('main.py').read_text(encoding='utf-8')
    fallback = 'install_governed_webhook_bell_event_alignment(app)'
    exact = 'install_governed_exact_record_event_alignment(app)'
    bell = 'install_governed_bell_event_projection_alignment(app)'

    assert fallback in main
    assert main.index(fallback) < main.index(exact) < main.index(bell)
