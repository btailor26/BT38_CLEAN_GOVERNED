/* BT38 FBM asset alignment bootstrap.
 * Keep the existing tracking journey intact while ensuring the native eBay
 * shipping alignment is installed before legacy Seller Hub click handlers.
 */
(function (document) {
    'use strict';

    function load(src) {
        const script = document.createElement('script');
        script.src = src;
        script.async = false;
        document.head.appendChild(script);
    }

    load('/static/js/fbm_ebay_shipping_alignment.js');
    load('/static/js/fbm_tracking_journey_legacy.js');
})(document);
