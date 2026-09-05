from pathlib import Path


def test_event_projection_is_final_zero_query_bell_authority():
    source = Path("services/governed_bell_event_projection_alignment.py").read_text()
    assert "_PRESENTATION_SCOPE_KEYS" in source
    assert "db.session" not in source
    assert ".query(" not in source
    assert "requests." not in source
    assert "setInterval" not in source
    assert "_event_only_bell_reader" in source
    assert "zero DB/API bell reads" in source

    main = Path("main.py").read_text()
    assert "install_governed_bell_event_projection_alignment" in main
    exact = main.index("install_governed_exact_record_event_alignment(app)")
    bell = main.index("install_governed_bell_event_projection_alignment(app)")
    assert exact < bell


def test_older_db_backed_bell_wrappers_cannot_remain_final_authority():
    main = Path("main.py").read_text()
    small = main.index("install_governed_fbm_small_alignment(app)")
    ready = main.index("install_governed_fbm_ready_landing_alignment(app)")
    exact = main.index("install_governed_exact_record_event_alignment(app)")
    bell = main.index("install_governed_bell_event_projection_alignment(app)")

    assert small < ready < exact < bell

    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()
    assert 'app.view_functions[endpoint] = login_required(ready._event_only_bell_reader)' in projection


def test_bell_keeps_bounded_browser_observed_history_without_db_hydration():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()
    ready = Path("services/governed_fbm_ready_landing_alignment.py").read_text()

    assert "bt38.notifications.exactEventRecords.v1" in projection
    assert "rows.slice(0,50)" in projection
    assert "window.addEventListener('bt38-marketplace-event'" in projection
    assert 'html = html.replace("hydrateBellAfterWake();", "stale = true;")' in ready


def test_exact_transport_and_bell_remain_db_blind():
    exact = Path("services/governed_exact_record_event_alignment.py").read_text()
    ready = Path("services/governed_fbm_ready_landing_alignment.py").read_text()

    assert "zero polling / zero idle DB work" in exact
    assert "ui._condition.wait(timeout=25.0)" in exact
    assert "db.session" not in ready.split("def _event_only_bell_reader():", 1)[1].split("def _restore_pending_fbm_visibility", 1)[0]
    assert '"db_query": False' in ready


def test_every_bell_movement_is_clear_and_contextual():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()

    for label in (
        "Sale",
        "Get ready to dispatch",
        "Ship by",
        "Late",
        "Shipped",
        "Picked up",
        "In transit",
        "Out for delivery",
        "Delivered",
        "Return requested",
        "Returned",
        "Refund requested",
        "Refunded",
        "Cancellation requested",
        "Cancelled",
        "Replacement requested",
        "Replacement",
        "Chargeback",
        "Dispute",
        "Issue / case",
    ):
        assert f'"{label}"' in projection or f"'{label}'" in projection

    assert "_platform_for_event" in projection
    assert 'return "Amazon"' in projection
    assert 'return "eBay"' in projection
    assert 'return "Marketplace"' in projection
    assert 'details.append(f"Order {order_id}")' in projection
    assert 'details.append(f"Qty {quantity}")' in projection
    assert 'details.append(f"Carrier {carrier}")' in projection
    assert 'details.append(f"Tracking {tracking}")' in projection
    assert 'details.append(f"SKU {sku}")' not in projection
    assert 'subject = product_title or (f"Order {order_id}" if order_id else "Order")' in projection
    assert 'title = f"{label} · {platform} · {subject}"' in projection


def test_bell_never_promotes_carrier_or_provider_to_marketplace_identity():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()
    platform = projection.split("def _platform_for_event(event: dict) -> str:", 1)[1].split("def _event_to_bell_record", 1)[0]

    assert 'event.get("provider")' not in platform
    assert 'event.get("carrier")' not in platform
    assert 'return "Marketplace"' in platform


def test_browser_projection_matches_server_product_title_and_no_visible_sku():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()

    assert "productTitle||(orderId?'Order '+orderId:'Order')" in projection
    assert "parts.push('SKU '+sku)" not in projection
    assert "carrier=String(detail.carrier||detail.provider||'').trim()" in projection
    assert "return 'Marketplace';" in projection


def test_generic_commits_are_transport_not_notifications_and_fbm_page_owns_lifecycle_projection():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()

    assert "_is_generic_transport_event" in projection
    assert 'event_type in {"order_committed", "shipment_committed"}' in projection
    assert "if _is_generic_transport_event(event):\n        return None" in projection
    assert "function isGenericTransport(detail)" in projection
    assert "function fbmRowFor(orderId)" in projection
    assert "function fbmLabel(row)" in projection
    assert "function fbmProjection(detail)" in projection
    assert "notification_source:'fbm_page'" in projection
    assert "notification_label:label" in projection
    assert "td:nth-child(2)" in projection
    assert "td:nth-child(4) strong" in projection
    assert "td:nth-child(8) strong" in projection
    assert "td:nth-child(8) code" in projection
    assert "td:nth-child(9) .badge.bg-success" in projection
    assert "if(isGenericTransport(detail)){" in projection
    assert "var projected=fbmProjection(detail)" in projection
    assert "showFbmToast(saved.record)" in projection


def test_fbm_page_prime_and_small_box_share_the_same_projection():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()

    assert 'img[alt="Prime"]' in projection
    assert "is_prime:prime" in projection
    assert "platform+' Prime'" in projection
    assert "function showFbmToast(record)" in projection
    assert "bt38FbmMovementToasts" in projection
    assert "window.setTimeout" in projection
    assert "5000" in projection
    assert "fbmMovementState.v1" in projection
    assert "if(movement[key]===record.status_label)return" in projection
    assert "if(shipment.indexOf('Unshipped')>=0)return 'Get ready to dispatch';" in projection


def test_explicit_fbm_owned_sale_uses_same_bell_record_and_small_box():
    projection = Path("services/governed_bell_event_projection_alignment.py").read_text()

    assert "var saved=store(detail),owned=norm(detail.notification_source||detail.source);" in projection
    assert "if(saved&&saved.isNew&&owned==='fbm_page')showFbmToast(saved.record);" in projection
