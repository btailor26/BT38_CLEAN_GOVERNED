// FBM session-view alignment for the existing shared BT38 marketplace event.
// Warehouse is the reference model: no poller, no extra SSE and no marketplace read.
// Persisted events refresh the visible browser session once; hidden pages sleep and
// coalesce pending work until they become visible again.
(function () {
  'use strict';
  if (window.bt38FbmEventSessionRefreshInstalled) return;
  window.bt38FbmEventSessionRefreshInstalled = true;

  let pending = false;
  let lastSequence = '';
  let reloadTimer = null;

  function onFbm() {
    return String(window.location.pathname || '').replace(/\/$/, '') === '/fbm';
  }

  function sequenceOf(event) {
    return String(event && event.detail && event.detail.sequence || '').trim();
  }

  function setSessionState(values) {
    if (window.BT38 && typeof window.BT38.setPageSession === 'function') {
      window.BT38.setPageSession('fbm', values || {});
    }
  }

  function markDirty(sequence) {
    pending = true;
    setSessionState({
      dirty: true,
      pendingSequence: sequence || lastSequence || ''
    });
  }

  function clearDirty() {
    pending = false;
    setSessionState({dirty: false, pendingSequence: ''});
  }

  function scheduleCommittedRefresh() {
    if (!onFbm() || !pending || document.visibilityState === 'hidden' || reloadTimer) return;
    reloadTimer = window.setTimeout(function () {
      reloadTimer = null;
      if (!onFbm() || !pending || document.visibilityState === 'hidden') return;
      clearDirty();
      window.location.reload();
    }, 750);
  }

  // Some FBM table styling can override the browser's default [hidden] rendering.
  // The session controller remains the owner of row visibility; this only makes its
  // hidden flag authoritative in the rendered table without another DB read.
  function alignRowVisibility(row) {
    if (!row || !row.classList || !row.classList.contains('fbm-order-row')) return;
    row.style.display = row.hidden ? 'none' : '';
  }

  function alignAllRowVisibility() {
    if (!onFbm()) return;
    document.querySelectorAll('tr.fbm-order-row').forEach(alignRowVisibility);
  }

  function watchSessionRows() {
    if (!onFbm()) return;
    const body = document.querySelector('.fbm-orders-table tbody');
    if (!body || body.dataset.bt38FbmVisibilityWatch === '1') return;
    body.dataset.bt38FbmVisibilityWatch = '1';
    alignAllRowVisibility();
    const observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (mutation) {
        if (mutation.type === 'attributes' && mutation.attributeName === 'hidden') {
          alignRowVisibility(mutation.target);
        }
      });
    });
    observer.observe(body, {subtree: true, attributes: true, attributeFilter: ['hidden']});
  }

  // The lifecycle tab script can run before the shared PageController finishes its
  // DOMContentLoaded registration. PageController then renders its unfiltered cache
  // and can overwrite the lifecycle row visibility while leaving the remembered tab
  // highlighted. Re-apply the already-selected lifecycle tab once after all startup
  // handlers complete so the existing lifecycle controller remains the single owner.
  function reconcileLifecycleViewAfterPageController() {
    if (!onFbm()) return;
    window.setTimeout(function () {
      if (!onFbm()) return;
      const pages = window.BT38 && window.BT38.pages;
      const page = pages && (pages.fbm || pages.FBM);
      const activeTab = document.querySelector('.fbm-lifecycle-tab[data-fbm-tab].active');
      if (!page || page.ready !== true || !activeTab) return;
      activeTab.click();
      alignAllRowVisibility();
    }, 0);
  }

  window.addEventListener('bt38-marketplace-event', function (event) {
    if (!onFbm()) return;
    const sequence = sequenceOf(event);
    if (sequence && sequence === lastSequence) return;
    if (sequence) lastSequence = sequence;
    markDirty(sequence);
    scheduleCommittedRefresh();
  });

  document.addEventListener('visibilitychange', function () {
    if (!onFbm() || document.visibilityState !== 'visible') return;
    alignAllRowVisibility();
    if (pending) scheduleCommittedRefresh();
  });

  function initialise() {
    if (!onFbm()) return;
    watchSessionRows();
    reconcileLifecycleViewAfterPageController();
    if (window.BT38 && typeof window.BT38.getPageSession === 'function') {
      const state = window.BT38.getPageSession('fbm', {dirty: false});
      pending = Boolean(state && state.dirty);
      if (pending && document.visibilityState === 'visible') scheduleCommittedRefresh();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, {once: true});
  } else {
    initialise();
  }
})();
