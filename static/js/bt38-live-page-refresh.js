// Shared BT38 event-driven page refresh controller.
// One server SSE connection is owned by base.html; this file never opens one.
// No polling, no intervals, no marketplace reads, and no full-page reloads.
(function () {
  'use strict';

  if (window.bt38LivePageRefreshInstalled) return;
  window.bt38LivePageRefreshInstalled = true;

  let pendingWhileHidden = false;
  let lastSequence = '';
  let productLinkingRefreshRunning = false;

  function sequenceOf(event) {
    return String(event?.detail?.sequence || '').trim();
  }

  function currentProductLinkingSearch() {
    if (!document.querySelector('[data-bt38-page="productLinking"]')) return '';
    const form = document.getElementById('bt38ProductLinkingFilterForm');
    return String(form?.querySelector('[name="search"]')?.value || '').trim();
  }

  async function refreshProductLinkingSilently() {
    const search = currentProductLinkingSearch();
    if (!search || productLinkingRefreshRunning) return false;
    if (typeof window.bt38RefreshProductLinkingRecord !== 'function') return false;

    productLinkingRefreshRunning = true;
    try {
      // Product Linking already owns the exact targeted DB-backed refresh.
      // Reuse the user's current search identity rather than hydrate the full
      // working set. One event -> one targeted read; no polling or broad scan.
      await window.bt38RefreshProductLinkingRecord({
        listingSku: search,
        warehouseSku: search
      });
      return true;
    } catch (error) {
      console.warn('[BT38 UI] Product Linking silent refresh failed', error);
      return false;
    } finally {
      productLinkingRefreshRunning = false;
    }
  }

  function pageOwnsCommittedRefresh() {
    // Orders / MCF already performs one narrow DB-only table refresh from the
    // same shared event. Do not add a second current-page request there.
    return Boolean(document.getElementById('mcf-orders-body'));
  }

  async function refreshCurrentPage() {
    if (pageOwnsCommittedRefresh()) return;

    if (document.visibilityState === 'hidden') {
      pendingWhileHidden = true;
      return;
    }

    if (document.querySelector('[data-bt38-page="productLinking"]')) {
      await refreshProductLinkingSilently();
      return;
    }

    // Pages without a targeted live updater must remain visually stable.
    // Never reload or rerender the whole page on a marketplace event because
    // that can interrupt active edits, searches, modals, or quantity changes.
  }

  window.addEventListener('bt38-marketplace-event', function (event) {
    const sequence = sequenceOf(event);
    if (sequence && sequence === lastSequence) return;
    if (sequence) lastSequence = sequence;
    void refreshCurrentPage();
  });

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible' || !pendingWhileHidden) return;
    pendingWhileHidden = false;
    void refreshCurrentPage();
  });
})();
