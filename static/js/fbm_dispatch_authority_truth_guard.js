/* Keep dispatched FBM presentation on persisted shipment truth only.
 *
 * The legacy journey asset used rendered carrier text to rewrite the Shipping
 * cell as "Dispatch authority / Persisted shipment evidence". Carrier text alone
 * does not prove purchase/physical shipment authority. Remove that synthetic
 * presentation whenever it appears. Actual shipment/carrier/tracking remains in
 * the Shipment/Journey columns, where it is backed by persisted FBMShipment data.
 */
(function (document) {
    'use strict';

    function removeSyntheticAuthority(row) {
        if (!row || !row.children) return;
        const shippingCell = row.children[5];
        if (!shippingCell) return;
        const note = Array.from(shippingCell.querySelectorAll('.fbm-row-note')).find(node =>
            String(node.textContent || '').trim() === 'Persisted shipment evidence'
        );
        if (!note) return;
        const label = Array.from(shippingCell.querySelectorAll('.small.text-muted')).find(node =>
            String(node.textContent || '').trim() === 'Dispatch authority'
        );
        if (!label) return;

        shippingCell.replaceChildren();
        const neutral = document.createElement('span');
        neutral.className = 'text-muted';
        neutral.textContent = '—';
        neutral.title = 'Physical carrier authority is shown only from persisted shipment evidence in the Shipment/Journey columns.';
        shippingCell.appendChild(neutral);
        shippingCell.dataset.bt38SyntheticAuthorityRemoved = '1';
    }

    function align() {
        document.querySelectorAll('.fbm-order-row').forEach(removeSyntheticAuthority);
    }

    align();
    const table = document.querySelector('.fbm-orders-table');
    if (table) new MutationObserver(align).observe(table, {childList: true, subtree: true});
    document.addEventListener('bt38:fbm-snapshot-applied', align);
    window.setTimeout(align, 100);
})(document);
