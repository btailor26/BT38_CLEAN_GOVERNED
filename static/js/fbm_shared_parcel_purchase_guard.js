/* Govern the final purchase boundary for explicitly combined FBM orders.
 *
 * Amazon Buy Shipping is an exact-order native purchase and cannot be treated as
 * one label for several marketplace orders. When the Ready-to-Ship workspace is
 * in confirmed one-box mode, keep Amazon-native purchase unavailable and require
 * an eligible external/manual route instead. No provider call is made here.
 */
(function (window, document) {
    'use strict';

    function combinedModeActive() {
        const confirmation = document.getElementById('confirmOneParcel');
        if (!confirmation || confirmation.checked !== true) return false;
        const hidden = String(document.getElementById('packingSelectedIds')?.value || '').trim();
        const ids = hidden.split(',').map(v => v.trim()).filter(Boolean);
        const extras = Array.from(document.querySelectorAll('.pack-candidate:checked')).map(el => String(el.value || '').trim()).filter(Boolean);
        return new Set([...ids, ...extras]).size > 1;
    }

    function alignAmazonNativeButtons() {
        const combined = combinedModeActive();
        document.querySelectorAll('.provider-action[data-provider="amazon_buy_shipping"]').forEach(button => {
            if (!combined) {
                if (button.dataset.bt38SharedParcelBlocked === '1') {
                    button.disabled = button.dataset.bt38WasDisabled === '1';
                    button.textContent = button.dataset.bt38OriginalText || button.textContent;
                    button.title = button.dataset.bt38OriginalTitle || '';
                    delete button.dataset.bt38SharedParcelBlocked;
                }
                return;
            }
            if (button.dataset.bt38SharedParcelBlocked !== '1') {
                button.dataset.bt38WasDisabled = button.disabled ? '1' : '0';
                button.dataset.bt38OriginalText = button.textContent || '';
                button.dataset.bt38OriginalTitle = button.title || '';
            }
            button.dataset.bt38SharedParcelBlocked = '1';
            button.disabled = true;
            button.textContent = 'Amazon native label unavailable for 1 shared parcel';
            button.title = 'Amazon Buy Shipping belongs to one exact Amazon order. Choose an eligible external/manual route for a confirmed multi-order parcel.';
        });
    }

    document.addEventListener('change', event => {
        if (event.target && (event.target.id === 'confirmOneParcel' || event.target.classList.contains('pack-candidate'))) {
            alignAmazonNativeButtons();
        }
    });

    document.addEventListener('click', event => {
        const button = event.target.closest('.provider-action[data-provider="amazon_buy_shipping"]');
        if (!button || !combinedModeActive()) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        window.alert('Amazon Buy Shipping is tied to one exact Amazon order and cannot be used as the single label for multiple packed-together orders. Choose an eligible external/manual route.');
    }, true);

    const host = document.getElementById('fbmShippingOrders');
    if (host) {
        new MutationObserver(alignAmazonNativeButtons).observe(host, {childList: true, subtree: true});
    }
    window.setTimeout(alignAmazonNativeButtons, 100);
})(window, document);
