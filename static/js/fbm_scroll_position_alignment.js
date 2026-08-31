/* FBM refresh-position alignment.
 * Pager navigation may use #bt38FbmPager to intentionally bring the user to the
 * pager after a click. A browser reload must not replay that old anchor and dump
 * the session at the bottom of the FBM page.
 *
 * Presentation only: no polling, DB reads, marketplace reads or writes.
 */
(function (window, document) {
    'use strict';

    if (window.bt38FbmScrollPositionAlignmentInstalled) return;
    window.bt38FbmScrollPositionAlignmentInstalled = true;

    function navigationType() {
        try {
            const entry = window.performance && window.performance.getEntriesByType
                ? window.performance.getEntriesByType('navigation')[0]
                : null;
            return entry ? String(entry.type || '') : '';
        } catch (error) {
            return '';
        }
    }

    function clearStalePagerAnchorOnReload() {
        if (window.location.pathname.replace(/\/$/, '') !== '/fbm') return;
        if (window.location.hash !== '#bt38FbmPager') return;
        if (navigationType() !== 'reload') return;

        const cleanUrl = `${window.location.pathname}${window.location.search}`;
        window.history.replaceState(window.history.state, '', cleanUrl);
        window.requestAnimationFrame(function () {
            window.scrollTo({top: 0, left: 0, behavior: 'auto'});
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', clearStalePagerAnchorOnReload, {once: true});
    } else {
        clearStalePagerAnchorOnReload();
    }
})(window, document);
