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

  function getSessionState(defaults) {
    if (window.BT38 && typeof window.BT38.getPageSession === 'function') {
      return window.BT38.getPageSession('fbm', defaults || {});
    }
    return Object.assign({}, defaults || {});
  }

  function setSessionState(values) {
    if (window.BT38 && typeof window.BT38.setPageSession === 'function') {
      return window.BT38.setPageSession('fbm', values || {});
    }
    return values || {};
  }

  function pageState() {
    const pages = window.BT38 && window.BT38.pages;
    return pages && (pages.fbm || pages.FBM);
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
  // The existing lifecycle/PageController handoff remains the owner of row visibility.
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

  // The operational entry rule is deliberate: every normal FBM landing or refresh
  // starts at Ready to dispatch so the user sees work that still needs action first.
  // The lifecycle script can run before the shared PageController registers and the
  // PageController can then expose its unfiltered cache, so hand the Ready button back
  // through the existing controller once registration is complete. Clicking another
  // lifecycle tab afterwards remains browser-local and immediately shows that truth.
  function reconcileReadyAfterPageController() {
    if (!onFbm()) return;
    window.setTimeout(function () {
      if (!onFbm()) return;
      const page = pageState();
      const controller = window.BT38 && window.BT38.PageController;
      if (!page || page.ready !== true || !controller || typeof controller.renderPage !== 'function') return;

      const readyTab = document.querySelector('.fbm-lifecycle-tab[data-fbm-tab="ready_dispatch"]');
      if (readyTab) {
        readyTab.click();
      } else {
        page.currentPage = 1;
        controller.renderPage(page.name);
      }
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
    reconcileReadyAfterPageController();
    const state = getSessionState({dirty: false});
    pending = Boolean(state && state.dirty);
    if (pending && document.visibilityState === 'visible') scheduleCommittedRefresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, {once: true});
  } else {
    initialise();
  }
})();
