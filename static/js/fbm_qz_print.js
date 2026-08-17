/* FBM QZ Tray local printing bridge.
 * Printing is deliberately separate from postage purchase. A failed print
 * must never retry or duplicate a marketplace/provider shipment purchase.
 */
(function (global) {
    'use strict';

    const STORAGE_KEY = 'bt38_fbm_qz_printer';

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

    global.BT38FBMQZ = {connect, printers, savedPrinter, savePrinter, resolvePrinter, printLabel};
})(window);
