(function () {
    'use strict';

    function esc(value) {
        return String(value ?? '').replace(/[&<>"']/g, function (char) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char];
        });
    }

    function firstValue(object, keys) {
        if (!object || typeof object !== 'object') return null;
        for (const key of keys) {
            const value = object[key];
            if (value !== undefined && value !== null && String(value).trim() !== '') return value;
        }
        return null;
    }

    function eventTime(event) {
        return firstValue(event, ['date', 'datetime', 'timestamp', 'created_at', 'event_date', 'time']);
    }

    function eventTitle(event) {
        return firstValue(event, ['description', 'message', 'label', 'status', 'state', 'event', 'type', 'code']) || 'Provider update';
    }

    function eventDetail(event) {
        const title = String(eventTitle(event));
        const value = firstValue(event, ['status', 'state', 'event', 'type', 'code']);
        return value && String(value) !== title ? String(value) : '';
    }

    function eventLocation(event) {
        const value = firstValue(event, ['location', 'city', 'address', 'place']);
        if (value && typeof value === 'object') {
            return firstValue(value, ['name', 'city', 'address', 'label']) || '';
        }
        return value || '';
    }

    function parseDate(value) {
        if (value === undefined || value === null || value === '') return null;
        let normalized = value;
        const text = String(value).trim();
        if (/^-?\d+(?:\.\d+)?$/.test(text)) {
            const numeric = Number(text);
            if (Number.isFinite(numeric)) normalized = Math.abs(numeric) < 1000000000000 ? numeric * 1000 : numeric;
        }
        const parsed = new Date(normalized);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    function formatDate(value) {
        const parsed = parseDate(value);
        if (!parsed) return value ? String(value) : 'Time not supplied';
        return parsed.toLocaleString('en-GB', {day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'});
    }

    function deliveredEvent(history) {
        for (const event of history) {
            const sourceText = [firstValue(event, ['status', 'state', 'event', 'type', 'code']), firstValue(event, ['description', 'message', 'label'])].filter(Boolean).join(' ').toLowerCase();
            if (/\bdelivered\b|delivery complete|successfully delivered/.test(sourceText)) return event;
        }
        return null;
    }

    function performanceBlock(payload, history) {
        const promise = payload.marketplace_promise || null;
        if (!promise || !promise.latest_delivery_at) return '<span class="badge bg-secondary">Marketplace promise unavailable</span>';
        const latest = parseDate(promise.latest_delivery_at);
        if (!latest) return '<span class="badge bg-secondary">Marketplace promise unavailable</span>';
        const delivered = deliveredEvent(history);
        const deliveredAt = delivered ? parseDate(eventTime(delivered)) : null;
        const providerStatus = String(payload.provider_status || '').toLowerCase();
        const providerSaysDelivered = /\bdelivered\b|delivery complete|successfully delivered/.test(providerStatus);
        if (deliveredAt) {
            if (deliveredAt.getTime() <= latest.getTime()) return '<span class="badge bg-success">Delivered on time</span>';
            const days = Math.max(1, Math.ceil((deliveredAt.getTime() - latest.getTime()) / 86400000));
            return `<span class="badge bg-danger">Delivered late · ${days} day${days === 1 ? '' : 's'}</span>`;
        }
        if (providerSaysDelivered) return '<span class="badge bg-success">Delivered · provider delivery time unavailable</span>';
        if (Date.now() > latest.getTime()) return '<span class="badge bg-danger">Late · not delivered</span>';
        return '<span class="badge bg-success">On track</span>';
    }

    function promiseHtml(promise) {
        if (!promise) return '<div class="text-muted small">Marketplace delivery promise is not available for this marketplace.</div>';
        if (!promise.earliest_delivery_at && !promise.latest_delivery_at) return `<div class="text-muted small">${esc(promise.unavailable_reason || 'Marketplace delivery promise unavailable.')}</div>`;
        const start = promise.earliest_delivery_at ? formatDate(promise.earliest_delivery_at) : '—';
        const end = promise.latest_delivery_at ? formatDate(promise.latest_delivery_at) : '—';
        return `<div class="small"><strong>Marketplace promise:</strong> ${esc(start)} → ${esc(end)}</div>`;
    }

    function historyHtml(history) {
        if (!history.length) return '<div class="alert alert-light border mb-0">Packlink/carrier returned no journey events yet.</div>';
        return history.map(function (event) {
            const detail = eventDetail(event), location = eventLocation(event);
            return `<div class="border-start border-3 ps-3 py-2 mb-2"><div class="fw-semibold">${esc(eventTitle(event))}</div>${detail ? `<div class="small text-muted">${esc(detail)}</div>` : ''}${location ? `<div class="small text-muted">${esc(location)}</div>` : ''}<div class="small text-muted">${esc(formatDate(eventTime(event)))}</div></div>`;
        }).join('');
    }

    async function openJourney(button) {
        const modalElement = document.getElementById('fbmTrackingJourneyModal');
        const body = document.getElementById('fbmTrackingJourneyBody');
        const subtitle = document.getElementById('fbmTrackingJourneySubtitle');
        if (!modalElement || !body) return;
        const modal = bootstrap.Modal.getOrCreateInstance(modalElement);
        modal.show();
        body.innerHTML = '<div class="text-center text-muted py-5"><div class="spinner-border spinner-border-sm me-2"></div>Reading Packlink/carrier journey…</div>';
        if (subtitle) subtitle.textContent = button.dataset.trackingNumber || 'Tracking journey';
        try {
            const response = await fetch(`/fbm/shipments/${encodeURIComponent(button.dataset.shipmentId)}/packlink/status`, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
            const payload = await response.json().catch(function () { return {}; });
            if (!response.ok || payload.success !== true) throw new Error(payload.message || `HTTP ${response.status}`);
            const history = Array.isArray(payload.tracking_history) ? payload.tracking_history : [];
            const tracking = payload.tracking_number || payload.tracking || button.dataset.trackingNumber || '—';
            body.innerHTML = `<div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-3"><div><div class="fw-semibold">${esc(payload.carrier || 'Packlink/carrier')} · ${esc(payload.service || '')}</div><div class="small">Tracking: <code>${esc(tracking)}</code></div><div class="small text-muted">Journey source: Packlink / carrier platform</div></div><div>${performanceBlock(payload, history)}</div></div><div class="border rounded p-3 mb-3">${promiseHtml(payload.marketplace_promise)}</div><div class="fw-semibold mb-2">Platform journey</div>${historyHtml(history)}`;
        } catch (error) {
            body.innerHTML = `<div class="alert alert-danger mb-0">${esc(error.message)}</div>`;
        }
    }

    function installManualShippingButton() {
        const readyButton = document.getElementById('readyToShipSelected');
        if (!readyButton || document.getElementById('manualShippingButton')) return;
        const manualButton = document.createElement('a');
        manualButton.id = 'manualShippingButton'; manualButton.href = '/fbm/manual'; manualButton.className = 'btn btn-sm btn-outline-primary';
        manualButton.innerHTML = '<i data-feather="plus-circle" class="me-1"></i>Manual Shipping';
        readyButton.parentNode.insertBefore(manualButton, readyButton);
        if (window.feather) window.feather.replace();
    }

    function installBulkActionBar() {
        const checkboxes = Array.from(document.querySelectorAll('.fbm-order-checkbox'));
        if (!checkboxes.length || document.getElementById('fbmBulkActionBar')) return;
        const style = document.createElement('style');
        style.id = 'fbmBulkActionBarStyle';
        style.textContent = `.fbm-bulk-action-bar{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:9999;display:flex;align-items:center;gap:10px;padding:10px 12px;background:#fff;border:1px solid #d1d5db;border-radius:10px;box-shadow:0 8px 22px rgba(0,0,0,.22)}.fbm-bulk-action-bar[hidden]{display:none!important}.fbm-bulk-selected-pill{background:#0ea5e9;color:#fff;border-radius:999px;padding:6px 14px;font-weight:700;font-size:12px;white-space:nowrap}.fbm-bulk-cancel{border:1px solid #d1d5db;background:#fff;color:#111827;border-radius:6px;padding:8px 18px;font-weight:600}.fbm-bulk-select{min-width:180px;border:1px solid #d1d5db;border-radius:6px;background:#fff;padding:8px 34px 8px 12px;font-weight:600;color:#111827}@media(max-width:640px){.fbm-bulk-action-bar{width:calc(100% - 24px);justify-content:space-between}.fbm-bulk-cancel{padding:8px 12px}.fbm-bulk-select{min-width:0;flex:1}}`;
        document.head.appendChild(style);
        const bar = document.createElement('div');
        bar.id = 'fbmBulkActionBar'; bar.className = 'fbm-bulk-action-bar'; bar.hidden = true;
        bar.innerHTML = `<div class="fbm-bulk-selected-pill"><span id="fbmBulkSelectedCount">0</span> order(s) selected</div><button type="button" class="fbm-bulk-cancel" id="fbmBulkCancel">Cancel</button><select class="fbm-bulk-select" id="fbmBulkActionSelect" aria-label="Bulk action"><option value="">Select action</option><option value="ready_to_ship">Ready to Ship</option><option value="print_labels">Print Labels</option><option value="check_shipments">Check Shipments</option><option value="delete">Delete</option></select>`;
        document.body.appendChild(bar);
        const count = document.getElementById('fbmBulkSelectedCount'), selectAll = document.getElementById('selectAllOrders'), actionSelect = document.getElementById('fbmBulkActionSelect');
        const selectedIds = function () { return checkboxes.filter(function (box) { return box.checked; }).map(function (box) { return Number(box.value); }).filter(Boolean); };
        const updateBar = function () { const selected = selectedIds().length; if (count) count.textContent = String(selected); bar.hidden = selected === 0; };
        checkboxes.forEach(function (box) { box.addEventListener('change', updateBar); });
        if (selectAll) selectAll.addEventListener('change', function () { setTimeout(updateBar, 0); });
        document.getElementById('fbmBulkCancel').addEventListener('click', function () {
            checkboxes.forEach(function (box) { if (!box.checked) return; box.checked = false; box.dispatchEvent(new Event('change', {bubbles: true})); });
            if (selectAll) { selectAll.checked = false; selectAll.indeterminate = false; }
            if (actionSelect) actionSelect.value = ''; updateBar();
        });
        actionSelect.addEventListener('change', async function () {
            const action = actionSelect.value;
            if (action !== 'delete') { actionSelect.value = ''; return; }
            const ids = selectedIds();
            if (!ids.length) { actionSelect.value = ''; return; }
            if (!window.confirm(`Delete ${ids.length} selected FBM record${ids.length === 1 ? '' : 's'} from BT38? This does not cancel the order on the marketplace or cancel/refund any carrier label.`)) { actionSelect.value = ''; return; }
            actionSelect.disabled = true;
            try {
                const response = await fetch('/governed/fbm/orders/delete', {method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json', 'Content-Type': 'application/json'}, body: JSON.stringify({order_ids: ids, confirm_delete: 'DELETE_SELECTED_FBM_RECORDS'})});
                const payload = await response.json().catch(function () { return {}; });
                if (!response.ok || payload.success !== true) throw new Error(payload.message || `HTTP ${response.status}`);
                window.location.reload();
            } catch (error) { window.alert(error.message); actionSelect.value = ''; actionSelect.disabled = false; }
        });
        updateBar();
    }

    function packlinkStatusKind(payload) {
        if (payload && payload.label_ready) return 'label';
        const status = String(payload && payload.provider_status || '').trim().toUpperCase().replace(/[\s-]+/g, '_');
        if (!status) return 'draft';
        if (status.includes('AWAITING_COMPLETION')) return 'blocked';
        if (/(CANCEL|ERROR|FAIL|REJECT)/.test(status)) return 'issue';
        if (status.includes('READY_TO_SHIP') || status.includes('READY_FOR_PAYMENT') || status.includes('READY_TO_PAY') || status.includes('PAYMENT_PENDING')) return 'ready';
        return 'draft';
    }

    function renderPacklinkStatus(box, payload, shipmentId, handoffUrl) {
        const reference = payload.provider_reference || '';
        const kind = packlinkStatusKind(payload);
        if (kind === 'label') {
            const tracking = payload.tracking_number || payload.tracking || 'pending';
            box.innerHTML = `<div class="alert alert-success"><strong>✓ Packlink label ready.</strong></div><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Open label details</button>`;
            return;
        }
        if (kind === 'blocked') {
            box.innerHTML = `<div class="alert alert-warning mb-2"><strong>Action required</strong><div class="small">Confirm country in Packlink, then click Save.</div></div><div class="d-flex gap-2 flex-wrap"><a class="btn btn-sm btn-primary packlink-pro-handoff" href="${handoffUrl}" target="_blank" rel="noopener noreferrer">Continue to Packlink</a><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Check status</button></div>`;
            return;
        }
        if (kind === 'ready') {
            box.innerHTML = `<div class="alert alert-success mb-2"><strong>✓ Ready to Ship</strong><div class="small">Packlink has accepted the saved shipment.</div></div><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Check label</button>`;
            return;
        }
        if (kind === 'issue') {
            box.innerHTML = `<div class="alert alert-warning mb-2"><strong>Packlink needs attention</strong><div class="small">Open Packlink and correct the shipment before continuing.</div></div><div class="d-flex gap-2 flex-wrap"><a class="btn btn-sm btn-primary packlink-pro-handoff" href="${handoffUrl}" target="_blank" rel="noopener noreferrer">Continue to Packlink</a><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Check status</button></div>`;
            return;
        }
        box.innerHTML = `<div class="alert alert-info mb-2"><strong>✓ Packlink draft created.</strong>${reference ? `<div class="small">Reference: <code>${esc(reference)}</code></div>` : ''}</div><div class="d-flex gap-2 flex-wrap"><a class="btn btn-sm btn-primary packlink-pro-handoff" href="${handoffUrl}" target="_blank" rel="noopener noreferrer">Continue to Packlink</a><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Check status</button></div>`;
    }

    async function refreshPacklinkBox(box, shipmentId, handoffUrl) {
        if (!box || !shipmentId || box.dataset.packlinkStatusLoading === '1') return;
        box.dataset.packlinkStatusLoading = '1';
        try {
            const response = await fetch(`/fbm/shipments/${encodeURIComponent(shipmentId)}/packlink/status`, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
            const payload = await response.json().catch(function () { return {}; });
            if (!response.ok || payload.success !== true) throw new Error(payload.message || `HTTP ${response.status}`);
            renderPacklinkStatus(box, payload, shipmentId, handoffUrl);
        } catch (error) {
            box.innerHTML = `<div class="alert alert-warning mb-2"><strong>Packlink status unavailable</strong><div class="small">${esc(error.message)}</div></div><button class="btn btn-sm btn-outline-primary packlink-status" data-shipment-id="${esc(shipmentId)}">Check again</button>`;
        } finally {
            delete box.dataset.packlinkStatusLoading;
        }
    }

    function installPacklinkHandoff() {
        const root = document.getElementById('fbmShippingOrders');
        if (!root || root.dataset.packlinkHandoffInstalled === '1') return;
        root.dataset.packlinkHandoffInstalled = '1';
        const handoffUrl = 'https://pro.packlink.com/private/shipments/draft';
        const addHandoff = function () {
            root.querySelectorAll('.rate-results').forEach(function (box) {
                const text = String(box.textContent || '');
                if (!text.includes('Packlink shipment prepared.') || !text.includes('Reference:')) return;
                const statusButton = box.querySelector('.packlink-status[data-shipment-id]');
                const shipmentId = statusButton ? statusButton.dataset.shipmentId : '';
                if (shipmentId) {
                    refreshPacklinkBox(box, shipmentId, handoffUrl);
                    return;
                }
                const alert = box.querySelector('.alert-info');
                if (alert) {
                    const heading = alert.querySelector('strong');
                    if (heading) heading.textContent = '✓ Packlink draft created.';
                }
            });
        };
        new MutationObserver(addHandoff).observe(root, {childList: true, subtree: true}); addHandoff();
    }

    function installExistingPacklinkDraftAlignment() {
        const root = document.getElementById('fbmShippingOrders');
        if (!root || root.dataset.packlinkDraftAlignmentInstalled === '1') return;
        root.dataset.packlinkDraftAlignmentInstalled = '1';
        const handoffUrl = 'https://pro.packlink.com/private/shipments/draft';

        const align = function () {
            root.querySelectorAll('.card[data-order-id]').forEach(function (card) {
                const orderId = card.dataset.orderId;
                const sourceRow = document.querySelector(`.fbm-order-row[data-order-id="${CSS.escape(String(orderId))}"]`);
                const existing = sourceRow ? sourceRow.querySelector('.packlink-existing-status[data-shipment-id]') : null;
                const box = card.querySelector(`.rate-results[data-order-id="${CSS.escape(String(orderId))}"]`);
                if (!existing || !box || box.dataset.existingPacklinkAligned === '1' || String(box.textContent || '').trim()) return;
                box.dataset.existingPacklinkAligned = '1';
                const shipmentId = existing.dataset.shipmentId;
                box.dataset.packlinkShipmentId = shipmentId;
                box.innerHTML = '<div class="text-muted">Checking Packlink status…</div>';
                refreshPacklinkBox(box, shipmentId, handoffUrl);
            });
        };
        new MutationObserver(align).observe(root, {childList: true, subtree: true}); align();

        const refreshVisiblePacklinkDrafts = function () {
            root.querySelectorAll('.rate-results[data-order-id]').forEach(function (box) {
                const shipmentId = box.dataset.packlinkShipmentId || (box.querySelector('.packlink-status[data-shipment-id]') || {}).dataset?.shipmentId;
                if (shipmentId) refreshPacklinkBox(box, shipmentId, handoffUrl);
            });
        };
        window.addEventListener('focus', refreshVisiblePacklinkDrafts);
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) refreshVisiblePacklinkDrafts();
        });
    }

    document.addEventListener('click', function (event) {
        const journeyButton = event.target.closest('.fbm-tracking-journey');
        if (journeyButton) {
            event.preventDefault(); event.stopPropagation(); openJourney(journeyButton); return;
        }
        const statusButton = event.target.closest('.packlink-status[data-shipment-id]');
        if (!statusButton) return;
        const box = statusButton.closest('.rate-results');
        if (!box) return;
        event.preventDefault(); event.stopPropagation();
        box.dataset.packlinkShipmentId = statusButton.dataset.shipmentId;
        refreshPacklinkBox(box, statusButton.dataset.shipmentId, 'https://pro.packlink.com/private/shipments/draft');
    });

    installManualShippingButton();
    installBulkActionBar();
    installPacklinkHandoff();
    installExistingPacklinkDraftAlignment();
})();
