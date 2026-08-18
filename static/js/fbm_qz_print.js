/* FBM QZ Tray local printing bridge.
 * Printing is deliberately separate from postage purchase. A failed print
 * must never retry or duplicate a marketplace/provider shipment purchase.
 */
(function (global) {
    'use strict';

    const STORAGE_KEY = 'bt38_fbm_qz_printer';
    const PACKLINK_PRO_URL = 'https://pro.packlink.com/';

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
            if (button.textContent !== 'Prepare Packlink') {
                button.textContent = 'Prepare Packlink';
            }
        });

        statuses.forEach(button => {
            if (button.textContent !== 'Check label') {
                button.textContent = 'Check label';
            }
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

    function processMutationTarget(target) {
        if (!target || target.nodeType !== 1) return;
        const rateBox = target.matches && target.matches('.rate-results')
            ? target
            : (target.closest ? target.closest('.rate-results') : null);
        if (!rateBox) return;
        ensureDownloadFallback(rateBox);
        alignPacklinkPaymentHandoff(rateBox);
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
