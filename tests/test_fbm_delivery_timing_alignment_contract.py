from datetime import date, datetime

import services.governed_order_clarity_alignment as alignment


def _row(body: str) -> str:
    return f'<tr class="fbm-order-row" data-order-id="1">{body}</tr>'


def test_delivery_timing_contract_marks_delivered_late(monkeypatch):
    html = _row(
        '<td><div class="d-flex flex-column gap-1" style="min-width:118px">'
        '<span class="badge bg-success">Delivered</span></div>'
        '<div class="small text-muted mt-1">Deliver by: 01 Sep</div></td>'
    )
    monkeypatch.setattr(
        alignment,
        "_delivery_evidence_by_order_row",
        lambda _ids: {1: {"created_at": datetime(2026, 8, 27), "delivered_at": datetime(2026, 9, 3, 12, 0)}},
    )
    result = alignment._enrich_fbm_delivery_timing_html(html, today=date(2026, 9, 3))
    assert 'badge bg-danger">Delivered late<' in result
    assert 'badge bg-success">Delivered<' not in result


def test_delivery_timing_contract_keeps_on_time_delivery_green(monkeypatch):
    html = _row(
        '<td><div class="d-flex flex-column gap-1" style="min-width:118px">'
        '<span class="badge bg-success">Delivered</span></div>'
        '<div class="small text-muted mt-1">Deliver by: 01 Sep</div></td>'
    )
    monkeypatch.setattr(
        alignment,
        "_delivery_evidence_by_order_row",
        lambda _ids: {1: {"created_at": datetime(2026, 8, 27), "delivered_at": datetime(2026, 9, 1, 18, 0)}},
    )
    result = alignment._enrich_fbm_delivery_timing_html(html, today=date(2026, 9, 1))
    assert 'badge bg-success">Delivered<' in result
    assert "Delivered late" not in result


def test_delivery_timing_contract_adds_delayed_badge_only_after_promise(monkeypatch):
    html = _row(
        '<td><div class="d-flex flex-column gap-1" style="min-width:118px">'
        '<span class="badge bg-light text-muted border">Delivered</span></div>'
        '<div class="small text-muted mt-1">Deliver by: 01 Sep</div></td>'
    )
    monkeypatch.setattr(
        alignment,
        "_delivery_evidence_by_order_row",
        lambda _ids: {1: {"created_at": datetime(2026, 8, 27), "delivered_at": None}},
    )
    delayed = alignment._enrich_fbm_delivery_timing_html(html, today=date(2026, 9, 2))
    assert 'bt38-delayed-badge">Delayed<' in delayed
    on_time_window = alignment._enrich_fbm_delivery_timing_html(html, today=date(2026, 9, 1))
    assert "Delayed" not in on_time_window


def test_delivery_timing_contract_does_not_infer_delivery_without_courier_timestamp(monkeypatch):
    html = _row(
        '<td><div class="d-flex flex-column gap-1" style="min-width:118px">'
        '<span class="badge bg-light text-muted border">Delivered</span></div>'
        '<div class="small text-muted mt-1">Deliver by: 01 Sep</div></td>'
    )
    monkeypatch.setattr(
        alignment,
        "_delivery_evidence_by_order_row",
        lambda _ids: {1: {"created_at": datetime(2026, 8, 27), "delivered_at": None}},
    )
    result = alignment._enrich_fbm_delivery_timing_html(html, today=date(2026, 8, 28))
    assert 'badge bg-success">Delivered<' not in result
    assert "Delivered late" not in result


def test_delivery_timing_alignment_is_read_only_and_marketplace_neutral():
    source = open(alignment.__file__, encoding="utf-8").read()
    timing = source[source.index("def _enrich_fbm_delivery_timing_html"):source.index("def _sale_identity")]
    assert "db.session.commit" not in timing
    assert "Amazon" not in timing
    assert "eBay" not in timing
    assert "marketplace API" not in timing
