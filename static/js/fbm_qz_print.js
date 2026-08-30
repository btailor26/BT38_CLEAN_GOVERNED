/* FBM QZ Tray local printing bridge.
 * Printing is deliberately separate from postage purchase. A failed print
 * must never retry or duplicate a marketplace/provider shipment purchase.
 */
(function (global) {
    'use strict';

    const STORAGE_KEY = 'bt38_fbm_qz_printer';
    const PACKLINK_PRO_URL = 'https://pro.packlink.com/';
    const AMAZON_UNSHIPPED_REPORT_URL = 'https://sellercentral.amazon.co.uk/order-reports-and-feeds/reports/fbmUnshippedOrders#';
    const BT38_AMAZON_REPORT_UPLOAD_URL = '/fbm/amazon-unshipped-report';
    const AMAZON_BUY_SHIPPING_PREFERENCES_URL = 'https://sellercentral.amazon.co.uk/sbr/buyShippingPreferences';
    const FBM_FETCH_TIMEOUT_MS = 15000;

    function installFbmFetchTimeout() {
        if (!global.fetch || global.fetch.__bt38FbmTimeoutWrapped) return;
        const nativeFetch = global.fetch.bind(global);
        const wrappedFetch = async function (input, init = {}) {
            const rawUrl = typeof input === 'string' ? input : String(input && input.url || '');
            let isFbmRequest = false;
            try {
                const parsed = new URL(rawUrl, global.location.href);
                isFbmRequest = parsed.origin === global.location.origin && (
                    parsed.pathname.startsWith('/fbm/') || parsed.pathname.startsWith('/governed/fbm/')
                );
            } catch (_) {
                isFbmRequest = false;
            }
            if (!isFbmRequest || init.signal) return nativeFetch(input, init);

            const controller = new AbortController();
            const timeoutId = global.setTimeout(function () { controller.abort(); }, FBM_FETCH_TIMEOUT_MS);
            try {
                return await nativeFetch(input, {...init, signal: controller.signal});
            } catch (error) {
                if (error && error.name === 'AbortError') {
                    throw new Error('Shipping request timed out after 15 seconds. Please try again.');
                }
                throw error;
            } finally {
                global.clearTimeout(timeoutId);
            }
        };
        wrappedFetch.__bt38FbmTimeoutWrapped = true;
        global.fetch = wrappedFetch;
    }

    installFbmFetchTimeout();

    function requireQz() {
        if (!global.qz) throw new Error('QZ Tray browser library is not loaded.');
        return global.qz;
    }

    async function connect() {
        const qz = requireQz();
        if (!qz.websocket.isActive()) {
            await qz.websocket.connect({retries: 2, delay: 1});
        }
        return true;
    }

    async function printers() {
        await connect();
        const found = await global.qz.printers.find();
        return Array.isArray(found) ? found : [found].filter(Boolean);
    }

    function savedPrinter() {
        try { return global.localStorage.getItem(STORAGE_KEY) || ''; }
        catch (_) { return ''; }
    }

    function savePrinter(name) {
        const value = String(name || '').trim();
        if (!value) throw new Error('Choose a printer first.');
        global.localStorage.setItem(STORAGE_KEY, value);
        return value;
    }

    async function resolvePrinter() {
        await connect();
        const saved = savedPrinter();
        if (saved) {
            const match = await global.qz.printers.find(saved);
            if (match) return Array.isArray(match) ? match[0] : match;
        }
        return global.qz.printers.getDefault();
    }

    function labelData(label) {
        const format = String(label && label.format || '').toUpperCase();
        const data = label && (label.data || label.base64 || label.url);
        if (!data) throw new Error('The purchased shipment has no printable label document.');

        if (format === 'ZPL' || format === 'ZPLII') {
            return [{type:'raw', format:'command', flavor: label.base64 ? 'base64' : 'plain', data:data}];
        }
        if (format === 'PDF') {
            return [{type:'pixel', format:'pdf', flavor: label.base64 ? 'base64' : 'file', data:data}];
        }
        if (format === 'PNG' || format === 'JPG' || format === 'JPEG') {
            return [{type:'pixel', format:'image', flavor: label.base64 ? 'base64' : 'file', data:data}];
        }
        throw new Error('Unsupported label format: ' + (format || 'unknown'));
    }

    async function printLabel(label) {
        const printer = await resolvePrinter();
        if (!printer) throw new Error('No QZ printer is available.');
        const options = {};
        if (label && label.width && label.height) {
            options.units = label.units || 'in';
            options.size = {width:Number(label.width), height:Number(label.height)};
        }
        const config = global.qz.configs.create(printer, options);
        await global.qz.print(config, labelData(label));
        return {printer: printer, sent: true};
    }

    function ensureDownloadFallback(root) {
        const scope = root && root.querySelectorAll ? root : document;
        const boxes = [];
        if (root && root.matches && root.matches('.rate-results')) boxes.push(root);
        scope.querySelectorAll('.rate-results').forEach(box => boxes.push(box));
        boxes.forEach(box => {
            if (!box.dataset || !box.dataset.label || box.querySelector('.label-download')) return;
            let label = null;
            try { label = JSON.parse(box.dataset.label); }
            catch (_) { return; }
            if (!label || !(label.url || label.base64)) return;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-primary label-download mt-2';
            button.textContent = 'Download label';
            box.appendChild(button);
        });
    }

    function alignPacklinkPaymentHandoff(root) {
        const scope = root && root.querySelectorAll ? root : document;
        const drafts = [];
        const statuses = [];
        if (root && root.matches && root.matches('.packlink-draft')) drafts.push(root);
        if (root && root.matches && root.matches('.packlink-status')) statuses.push(root);
        scope.querySelectorAll('.packlink-draft').forEach(button => drafts.push(button));
        scope.querySelectorAll('.packlink-status').forEach(button => statuses.push(button));

        drafts.forEach(button => {
            if (button.textContent !== 'Prepare Packlink') button.textContent = 'Prepare Packlink';
        });

        statuses.forEach(button => {
            if (button.textContent !== 'Check label') button.textContent = 'Check label';
            const box = button.closest('.rate-results');
            if (!box || box.querySelector('.packlink-pay-link')) return;
            const pay = document.createElement('a');
            pay.href = PACKLINK_PRO_URL;
            pay.target = '_blank';
            pay.rel = 'noopener';
            pay.className = 'btn btn-sm btn-success me-2 packlink-pay-link';
            pay.textContent = 'Pay in Packlink';
            button.parentNode.insertBefore(pay, button);
        });
    }

    function alignAmazonManualMappingControls(root) {
        const scope = root && root.querySelectorAll ? root : document;
        const editors = [];
        if (root && root.matches && root.matches('.mapping-editor')) editors.push(root);
        scope.querySelectorAll('.mapping-editor').forEach(editor => editors.push(editor));

        editors.forEach(editor => {
            if (!editor || editor.dataset.amazonSellerAligned === '1') return;
            const text = String(editor.textContent || '').toLowerCase();
            if (!text.includes('evri') && !text.includes('hermes')) return;

            const carrierInput = editor.querySelector('.mapping-carrier-code');
            const serviceInput = editor.querySelector('.mapping-service-code');
            if (!carrierInput || !serviceInput) return;

            const carrierSelect = document.createElement('select');
            carrierSelect.className = carrierInput.className;
            carrierSelect.innerHTML = '<option value="Hermes" selected>Hermes</option>';
            carrierInput.replaceWith(carrierSelect);

            const serviceSelect = document.createElement('select');
            serviceSelect.className = serviceInput.className;
            serviceSelect.innerHTML = [
                '<option value="">Select delivery service</option>',
                '<option value="Next Day">Next Day</option>',
                '<option value="Standard Courier collection">Standard Courier collection</option>',
                '<option value="Standard drop off">Standard drop off</option>',
                '<option value="Standard Two Day drop off">Standard Two Day drop off</option>'
            ].join('');
            serviceInput.replaceWith(serviceSelect);

            const carrierLabel = document.createElement('div');
            carrierLabel.className = 'small text-muted mt-2 mb-1';
            carrierLabel.textContent = 'Carrier';
            carrierSelect.parentNode.insertBefore(carrierLabel, carrierSelect);

            const serviceLabel = document.createElement('div');
            serviceLabel.className = 'small text-muted mt-2 mb-1';
            serviceLabel.textContent = 'Delivery Service';
            serviceSelect.parentNode.insertBefore(serviceLabel, serviceSelect);

            editor.dataset.amazonSellerAligned = '1';
        });
    }

    function amazonParcelSummary(rateBox) {
        const card = rateBox.closest('.card[data-order-id]');
        if (!card) return {weight:'Not entered', dimensions:'Not entered', store:'BT 38'};
        const kg = Number(card.querySelector('.parcel-weight-kg')?.value || 0);
        const grams = Number(card.querySelector('.parcel-weight-g')?.value || 0);
        const length = card.querySelector('.parcel-field[data-field="length_cm"]')?.value || '';
        const width = card.querySelector('.parcel-field[data-field="width_cm"]')?.value || '';
        const height = card.querySelector('.parcel-field[data-field="height_cm"]')?.value || '';
        const totalGrams = Math.round((kg * 1000) + grams);
        const weight = totalGrams > 0 ? `${Math.floor(totalGrams / 1000)} kg ${totalGrams % 1000} g` : 'Not entered';
        const dimensions = length && width && height ? `${length} cm × ${width} cm × ${height} cm` : 'Not entered';
        const header = card.querySelector('.card-header .small.text-muted');
        const store = String(header?.textContent || 'BT 38').trim() || 'BT 38';
        return {weight, dimensions, store};
    }

    function todayLabel() {
        try {
            return new Intl.DateTimeFormat('en-GB', {weekday:'short', day:'numeric', month:'short'}).format(new Date());
        } catch (_) {
            return new Date().toLocaleDateString('en-GB');
        }
    }

    function alignAmazonBuyShippingPanel(rateBox) {
        if (!rateBox || rateBox.dataset.amazonPanel !== '1') return;
        if (rateBox.querySelector('.amazon-buy-shipping-shell')) return;

        const parcel = amazonParcelSummary(rateBox);
        const serviceContent = document.createElement('div');
        serviceContent.className = 'amazon-service-content';
        while (rateBox.firstChild) serviceContent.appendChild(rateBox.firstChild);

        const shell = document.createElement('div');
        shell.className = 'amazon-buy-shipping-shell border rounded p-3';
        shell.innerHTML = `
            <div class="d-flex justify-content-between align-items-start gap-3 flex-wrap mb-3">
                <div>
                    <div class="fw-bold">Amazon Buy Shipping</div>
                    <div class="small text-muted">Add delivery information to see Amazon-controlled options for purchasing a label. Accurate parcel weight and dimensions are required.</div>
                </div>
                <a class="btn btn-sm btn-outline-secondary" href="${AMAZON_BUY_SHIPPING_PREFERENCES_URL}" target="_blank" rel="noopener">Buy Shipping preferences</a>
            </div>
            <div class="row g-2 mb-3 small">
                <div class="col-md-4"><span class="text-muted">Dispatch from:</span> <strong>${parcel.store.replace(/[&<>"']/g, '')}</strong></div>
                <div class="col-md-4"><span class="text-muted">Dispatch date:</span> <strong>${todayLabel()}</strong></div>
                <div class="col-md-4"><span class="text-muted">Label:</span> <strong>Amazon-supported format · QZ printing</strong></div>
            </div>
            <div class="border-top pt-3 mb-3">
                <div class="fw-semibold">Shipping service requirements</div>
                <div class="small text-muted">Amazon controls eligible carrier/service. If a service requires additional seller inputs, Amazon must return those requirements before label purchase.</div>
            </div>
            <div class="row g-3 mb-3">
                <div class="col-md-6"><div class="text-muted small">Packaging</div><strong>${parcel.dimensions}</strong><div class="small text-muted">Change the packed parcel dimensions above if required.</div></div>
                <div class="col-md-6"><div class="text-muted small">Weight</div><strong>${parcel.weight}</strong><div class="small text-muted">Weight is sent to Amazon exactly as entered above.</div></div>
            </div>
            <div class="border-top pt-3">
                <div class="fw-semibold mb-2">Select a shipping service</div>
            </div>`;
        shell.appendChild(serviceContent);

        const footer = document.createElement('div');
        footer.className = 'border-top pt-3 mt-3 small';
        const printer = savedPrinter() || 'Saved/default printer';
        footer.innerHTML = `<div><span class="text-muted">Confirmation:</span> Amazon service requirements apply.</div><div><span class="text-muted">One-Click Printer:</span> <strong>${printer.replace(/[&<>"']/g, '')}</strong></div>`;
        shell.appendChild(footer);
        rateBox.appendChild(shell);
    }

    function markAmazonBuyShippingClick(event) {
        const button = event.target && event.target.closest ? event.target.closest('.provider-action[data-provider="amazon_buy_shipping"]') : null;
        if (!button) return;
        const id = button.dataset.orderId;
        const rateBox = document.querySelector(`.rate-results[data-order-id="${id}"]`);
        if (rateBox) rateBox.dataset.amazonPanel = '1';
    }

    function ensureAmazonReportShortcuts() {
        const modal = document.getElementById('fbmShippingModal');
        if (!modal) return;
        const footer = modal.querySelector('.modal-footer');
        if (!footer || footer.querySelector('.amazon-report-shortcuts')) return;

        const shortcuts = document.createElement('div');
        shortcuts.className = 'amazon-report-shortcuts d-flex flex-wrap gap-2 me-auto';

        const amazonReport = document.createElement('a');
        amazonReport.href = AMAZON_UNSHIPPED_REPORT_URL;
        amazonReport.target = '_blank';
        amazonReport.rel = 'noopener';
        amazonReport.className = 'btn btn-sm btn-outline-warning';
        amazonReport.textContent = 'Amazon Unshipped Report';

        const uploadReport = document.createElement('a');
        uploadReport.href = BT38_AMAZON_REPORT_UPLOAD_URL;
        uploadReport.target = '_blank';
        uploadReport.rel = 'noopener';
        uploadReport.className = 'btn btn-sm btn-outline-primary';
        uploadReport.textContent = 'Upload Amazon Report';

        shortcuts.appendChild(amazonReport);
        shortcuts.appendChild(uploadReport);
        footer.insertBefore(shortcuts, footer.firstChild);
    }

    function processMutationTarget(target) {
        if (!target || target.nodeType !== 1) return;
        alignAmazonManualMappingControls(target);
        const rateBox = target.matches && target.matches('.rate-results')
            ? target
            : (target.closest ? target.closest('.rate-results') : null);
        if (!rateBox) return;
        ensureDownloadFallback(rateBox);
        alignPacklinkPaymentHandoff(rateBox);
        alignAmazonBuyShippingPanel(rateBox);
    }

    if (global.MutationObserver && global.document) {
        let observer = null;
        let scheduled = false;
        const pending = new Set();

        const flush = () => {
            scheduled = false;
            const targets = Array.from(pending);
            pending.clear();
            targets.forEach(processMutationTarget);
        };

        const schedule = target => {
            if (!target || target.nodeType !== 1) return;
            pending.add(target);
            if (scheduled) return;
            scheduled = true;
            global.requestAnimationFrame ? global.requestAnimationFrame(flush) : global.setTimeout(flush, 0);
        };

        observer = new MutationObserver(mutations => {
            mutations.forEach(mutation => {
                if (mutation.target) schedule(mutation.target);
                mutation.addedNodes.forEach(node => {
                    if (node && node.nodeType === 1) schedule(node);
                });
            });
        });

        const start = () => {
            ensureDownloadFallback(document);
            alignPacklinkPaymentHandoff(document);
            alignAmazonManualMappingControls(document);
            ensureAmazonReportShortcuts();
            document.addEventListener('click', markAmazonBuyShippingClick, true);
            const modal = document.getElementById('fbmShippingModal');
            if (modal) modal.addEventListener('shown.bs.modal', ensureAmazonReportShortcuts);
            const ordersRoot = document.getElementById('fbmShippingOrders');
            if (ordersRoot) {
                observer.observe(ordersRoot, {
                    subtree: true,
                    childList: true,
                    attributes: true,
                    attributeFilter: ['data-label']
                });
            }
        };
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true});
        else start();
    }

    global.BT38FBMQZ = {connect, printers, savedPrinter, savePrinter, resolvePrinter, printLabel};
})(window);