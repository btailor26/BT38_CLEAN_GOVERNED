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
        if (!value) return null;
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    function formatDate(value) {
        const parsed = parseDate(value);
        if (!parsed) return value ? String(value) : 'Time not supplied';
        return parsed.toLocaleString('en-GB', {
            day: '2-digit', month: 'short', year: 'numeric',
            hour: '2-digit', minute: '2-digit'
        });
    }

    function deliveredEvent(history) {
        for (const event of history) {
            const sourceText = [
                firstValue(event, ['status', 'state', 'event', 'type', 'code']),
                firstValue(event, ['description', 'message', 'label'])
            ].filter(Boolean).join(' ').toLowerCase();
            if (/\bdelivered\b|delivery complete|successfully delivered/.test(sourceText)) {
                return event;
            }
        }
        return null;
    }

    function performanceBlock(payload, history) {
        const promise = payload.marketplace_promise || null;
        if (!promise || !promise.latest_delivery_at) {
            return '<span class="badge bg-secondary">Marketplace promise unavailable</span>';
        }

        const latest = parseDate(promise.latest_delivery_at);
        if (!latest) return '<span class="badge bg-secondary">Marketplace promise unavailable</span>';

        const delivered = deliveredEvent(history);
        const deliveredAt = delivered ? parseDate(eventTime(delivered)) : null;
        const providerStatus = String(payload.provider_status || '').toLowerCase();
        const providerSaysDelivered = /\bdelivered\b|delivery complete|successfully delivered/.test(providerStatus);

        if (deliveredAt) {
            if (deliveredAt.getTime() <= latest.getTime()) {
                return '<span class="badge bg-success">Delivered on time</span>';
            }
            const days = Math.max(1, Math.ceil((deliveredAt.getTime() - latest.getTime()) / 86400000));
            return `<span class="badge bg-danger">Delivered late · ${days} day${days === 1 ? '' : 's'}</span>`;
        }

        if (providerSaysDelivered) {
            return '<span class="badge bg-success">Delivered · provider delivery time unavailable</span>';
        }

        if (Date.now() > latest.getTime()) {
            return '<span class="badge bg-danger">Late · not delivered</span>';
        }
        return '<span class="badge bg-success">On track</span>';
    }

    function promiseHtml(promise) {
        if (!promise) return '<div class="text-muted small">Marketplace delivery promise is not available for this marketplace.</div>';
        if (!promise.earliest_delivery_at && !promise.latest_delivery_at) {
            return `<div class="text-muted small">${esc(promise.unavailable_reason || 'Marketplace delivery promise unavailable.')}</div>`;
        }
        const start = promise.earliest_delivery_at ? formatDate(promise.earliest_delivery_at) : '—';
        const end = promise.latest_delivery_at ? formatDate(promise.latest_delivery_at) : '—';
        return `<div class="small"><strong>Marketplace promise:</strong> ${esc(start)} → ${esc(end)}</div>`;
    }

    function historyHtml(history) {
        if (!history.length) {
            return '<div class="alert alert-light border mb-0">Packlink/carrier returned no journey events yet.</div>';
        }
        return history.map(function (event) {
            const detail = eventDetail(event);
            const location = eventLocation(event);
            return `<div class="border-start border-3 ps-3 py-2 mb-2">
                <div class="fw-semibold">${esc(eventTitle(event))}</div>
                ${detail ? `<div class="small text-muted">${esc(detail)}</div>` : ''}
                ${location ? `<div class="small text-muted">${esc(location)}</div>` : ''}
                <div class="small text-muted">${esc(formatDate(eventTime(event)))}</div>
            </div>`;
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
            const response = await fetch(`/fbm/shipments/${encodeURIComponent(button.dataset.shipmentId)}/packlink/status`, {
                credentials: 'same-origin',
                cache: 'no-store',
                headers: {'Accept': 'application/json'}
            });
            const payload = await response.json().catch(function () { return {}; });
            if (!response.ok || payload.success !== true) throw new Error(payload.message || `HTTP ${response.status}`);

            const history = Array.isArray(payload.tracking_history) ? payload.tracking_history : [];
            const tracking = payload.tracking_number || payload.tracking || button.dataset.trackingNumber || '—';
            body.innerHTML = `
                <div class="d-flex justify-content-between align-items-start flex-wrap gap-2 mb-3">
                    <div>
                        <div class="fw-semibold">${esc(payload.carrier || 'Packlink/carrier')} · ${esc(payload.service || '')}</div>
                        <div class="small">Tracking: <code>${esc(tracking)}</code></div>
                        <div class="small text-muted">Journey source: Packlink / carrier platform</div>
                    </div>
                    <div>${performanceBlock(payload, history)}</div>
                </div>
                <div class="border rounded p-3 mb-3">${promiseHtml(payload.marketplace_promise)}</div>
                <div class="fw-semibold mb-2">Platform journey</div>
                ${historyHtml(history)}
            `;
        } catch (error) {
            body.innerHTML = `<div class="alert alert-danger mb-0">${esc(error.message)}</div>`;
        }
    }

    function installManualShippingButton() {
        const readyButton = document.getElementById('readyToShipSelected');
        if (!readyButton || document.getElementById('manualShippingButton')) return;

        const manualButton = document.createElement('a');
        manualButton.id = 'manualShippingButton';
        manualButton.href = '/fbm/manual';
        manualButton.className = 'btn btn-sm btn-outline-primary';
        manualButton.innerHTML = '<i data-feather="plus-circle" class="me-1"></i>Manual Shipping';
        readyButton.parentNode.insertBefore(manualButton, readyButton);
        if (window.feather) window.feather.replace();
    }

    document.addEventListener('click', function (event) {
        const button = event.target.closest('.fbm-tracking-journey');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        openJourney(button);
    });

    installManualShippingButton();
})();
