/* BT38 governed replacement-label control for dispatched FBM orders.
 *
 * Uses the existing Shipping options / Packlink draft path. A replacement is
 * never treated as another original shipment and cannot proceed until the user
 * states why the extra label is being purchased.
 */
(function (window, document) {
    'use strict';

    const dispatchedStates = new Set([
        'partially_shipped', 'shipped', 'accepted', 'carrier_accepted',
        'collected', 'picked_up', 'in_transit', 'out_for_delivery', 'delivered'
    ]);

    const state = {
        active: false,
        orderId: null,
        reasonCode: '',
        reason: ''
    };

    function reasonValues() {
        const code = String(document.getElementById('bt38ReplacementReasonCode')?.value || '').trim();
        const reason = String(document.getElementById('bt38ReplacementReason')?.value || '').trim();
        state.reasonCode = code;
        state.reason = reason;
        return {code, reason};
    }

    function reasonReady(showAlert) {
        const values = reasonValues();
        if (!values.code || values.reason.length < 3) {
            if (showAlert) window.alert('Choose the replacement reason and state why this extra label is being purchased.');
            return false;
        }
        return true;
    }

    function replacementPanel() {
        return '<div id="bt38ReplacementReasonPanel" class="alert alert-warning border mb-3">' +
            '<div class="fw-semibold">Replacement label</div>' +
            '<div class="small mb-2">This order is already dispatched. The new label is an additional replacement shipment and the purchase reason is required.</div>' +
            '<div class="row g-2">' +
                '<div class="col-md-4"><label class="form-label small">Reason</label>' +
                    '<select id="bt38ReplacementReasonCode" class="form-select form-select-sm">' +
                        '<option value="">Choose reason…</option>' +
                        '<option value="label_damaged">Label damaged / unusable</option>' +
                        '<option value="parcel_damaged">Parcel damaged</option>' +
                        '<option value="lost">Lost / missing parcel</option>' +
                        '<option value="customer_replacement">Customer replacement / resend</option>' +
                        '<option value="wrong_item">Wrong item / resend</option>' +
                        '<option value="other">Other</option>' +
                    '</select></div>' +
                '<div class="col-md-8"><label class="form-label small">Why is another label being purchased?</label>' +
                    '<input id="bt38ReplacementReason" class="form-control form-control-sm" maxlength="500" placeholder="Required – e.g. original parcel damaged, replacement being sent"></div>' +
            '</div>' +
            '<div class="small text-muted mt-2">The reason is saved against the replacement FBM shipment. The original dispatched shipment remains unchanged.</div>' +
        '</div>';
    }

    function applyReplacementWorkspace() {
        if (!state.active) return;
        const host = document.getElementById('fbmShippingOrders');
        if (!host || !host.children.length) return;
        if (!document.getElementById('bt38ReplacementReasonPanel')) {
            host.insertAdjacentHTML('afterbegin', replacementPanel());
        }

        host.querySelectorAll('.provider-action').forEach(button => {
            const provider = String(button.dataset.provider || '').toLowerCase();
            const orderId = String(button.dataset.orderId || '');
            if (orderId !== String(state.orderId)) {
                button.disabled = true;
                button.title = 'Replacement purchase applies to the selected dispatched order only.';
                return;
            }
            if (provider === 'amazon_buy_shipping') {
                button.disabled = true;
                button.title = 'Amazon Buy Shipping is tied to the original marketplace order. Use an eligible external replacement label.';
                button.textContent = 'Amazon native replacement unavailable';
            }
        });
    }

    function openReplacement(row) {
        const orderId = String(row.dataset.orderId || '').trim();
        if (!orderId) return;
        const shippingButton = row.querySelector('.fbm-shipping-options');
        if (!shippingButton) return;
        state.active = true;
        state.orderId = orderId;
        state.reasonCode = '';
        state.reason = '';
        shippingButton.click();
        window.setTimeout(applyReplacementWorkspace, 50);
    }

    function installButtons() {
        document.querySelectorAll('.fbm-order-row').forEach(row => {
            const lifecycle = String(row.dataset.lifecycleStatus || '').trim().toLowerCase();
            if (!dispatchedStates.has(lifecycle)) return;
            const actionCell = row.children && row.children[9];
            if (!actionCell || actionCell.querySelector('.bt38-replacement-label')) return;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-warning mt-1 bt38-replacement-label';
            button.textContent = 'Replacement label';
            button.title = 'Buy another label for a dispatched order. A purchase reason is required.';
            button.addEventListener('click', event => {
                event.preventDefault();
                event.stopPropagation();
                openReplacement(row);
            });
            actionCell.appendChild(button);
        });
    }

    const ordersBox = document.getElementById('fbmShippingOrders');
    if (ordersBox) {
        const observer = new MutationObserver(() => applyReplacementWorkspace());
        observer.observe(ordersBox, {childList: true, subtree: true});
    }

    document.addEventListener('click', event => {
        if (!state.active) return;
        const provider = event.target.closest('.provider-action');
        if (!provider) return;
        if (String(provider.dataset.orderId || '') !== String(state.orderId)) {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        const providerName = String(provider.dataset.provider || '').toLowerCase();
        if (providerName === 'amazon_buy_shipping') {
            event.preventDefault();
            event.stopImmediatePropagation();
            window.alert('Amazon Buy Shipping cannot be reused as a shared/replacement native label for an already dispatched order. Choose an eligible external carrier.');
            return;
        }
        if (!reasonReady(true)) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);

    const originalFetch = window.fetch.bind(window);
    window.fetch = function replacementAwareFetch(input, init) {
        if (!state.active) return originalFetch(input, init);
        const url = typeof input === 'string' ? input : String(input && input.url || '');
        const draftMatch = url.match(/\/fbm\/orders\/(\d+)\/packlink\/draft(?:\?|$)/);
        if (!draftMatch || String(draftMatch[1]) !== String(state.orderId)) {
            return originalFetch(input, init);
        }
        if (!reasonReady(true)) {
            return Promise.reject(new Error('Replacement reason is required before another label can be purchased.'));
        }

        const next = Object.assign({}, init || {});
        let body = {};
        try { body = JSON.parse(String(next.body || '{}')); } catch (_) { body = {}; }
        body.shipment_purpose = 'replacement';
        body.confirm_additional_shipment = 'CONFIRM_REPLACEMENT';
        body.replacement_reason_code = state.reasonCode;
        body.replacement_reason = state.reason;
        next.body = JSON.stringify(body);
        return originalFetch(input, next);
    };

    const modal = document.getElementById('fbmShippingModal');
    if (modal) {
        modal.addEventListener('hidden.bs.modal', () => {
            state.active = false;
            state.orderId = null;
            state.reasonCode = '';
            state.reason = '';
        });
    }

    installButtons();
    document.addEventListener('bt38:fbm-snapshot-applied', installButtons);
    window.setTimeout(installButtons, 250);
})(window, document);
