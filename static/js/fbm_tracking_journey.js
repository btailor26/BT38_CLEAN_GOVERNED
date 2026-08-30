/* BT38 FBM asset alignment bootstrap.
 * Load native eBay shipping before the preserved tracking journey so the
 * capture-phase native handler owns eBay Shipping and the legacy Seller Hub
 * handler cannot run first.
 */
(function (document) {
    'use strict';

    function loadLegacy() {
        if (document.querySelector('script[data-bt38-fbm-tracking-legacy="1"]')) return;
        const legacy = document.createElement('script');
        legacy.src = '/static/js/fbm_tracking_journey_legacy.js';
        legacy.dataset.bt38FbmTrackingLegacy = '1';
        document.head.appendChild(legacy);
    }

    if (document.querySelector('script[data-bt38-ebay-native-bootstrap="1"]')) {
        loadLegacy();
        return;
    }

    const nativeScript = document.createElement('script');
    nativeScript.src = '/static/js/fbm_ebay_shipping_alignment.js';
    nativeScript.dataset.bt38EbayNativeBootstrap = '1';
    nativeScript.onload = loadLegacy;
    nativeScript.onerror = loadLegacy;
    document.head.appendChild(nativeScript);
})(document);
