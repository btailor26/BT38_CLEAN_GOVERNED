// FBM session-view alignment for the existing shared BT38 marketplace event.
// Warehouse is the reference model: no poller, no extra SSE and no marketplace read.
// Persisted events refresh the visible browser session once; hidden pages sleep and
// coalesce pending work until they become visible again.
(function () {
  'use strict';
  if (window.bt38FbmEventSessionRefreshInstalled) return;
  window.bt38FbmEventSessionRefreshInstalled = true;

  const lastViewKey = 'bt38:last-view:fbm';
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

  function readLastView() {
    try {
      const raw = window.localStorage.getItem(lastViewKey);
      return raw ? JSON.parse(raw) : {};
    } catch (error) {
      return {};
    }
  }

  function writeLastView(values) {
    try {
      const current = readLastView();
      window.localStorage.setItem(lastViewKey, JSON.stringify(Object.assign({}, current, values || {})));
    } catch (error) {
      // Browser-session state remains authoritative when localStorage is unavailable.
    }
  }

  function pageState() {
    const pages = window.BT38 && window.BT38.pages;
    return pages && (pages.fbm || pages.FBM);
  }

  function selectedTab() {
    const active = document.querySelector('.fbm-lifecycle-tab[data-fbm-tab].active');
    return active ? String(active.dataset.fbmTab || '') : '';
  }

  function currentSearch() {
    const input = document.getElementById('bt38FbmGlobalSearchInput');
    return String(input && input.value || '').trim().toLowerCase();
  }

  function currentPerPage(page) {
    const select = document.getElementById('bt38ResultsPerPageSelect');
    const value = Number.parseInt(select ? select.value : page && page.perPage, 10);
    return Number.isFinite(value) && value > 0 ? value : 15;
  }

  function rememberView() {
    if (!onFbm()) return;
    const page = pageState();
    const values = {
      tab: selectedTab() || 'ready_dispatch',
      search: currentSearch(),
      currentPage: page && Number(page.currentPage) > 0 ? Number(page.currentPage) : 1,
      perPage: currentPerPage(page)
    };
    setSessionState(values);
    writeLastView(values);
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
      rememberView();
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
  // DOMContentLoaded registration. PageController then renders its unfiltered cache.
  // Restore the same tab, search, page and page-size through the existing controllers;
  // do not create a second table or pager.
  function reconcileLifecycleViewAfterPageController() {
    if (!onFbm()) return;
    window.setTimeout(function () {
      if (!onFbm()) return;
      const page = pageState();
      const controller = window.BT38 && window.BT38.PageController;
      if (!page || page.ready !== true || !controller || typeof controller.renderPage !== 'function') return;

      const session = getSessionState({tab: 'ready_dispatch', search: '', currentPage: 1, perPage: 15});
      const crossTab = readLastView();
      const remembered = Object.assign({}, session, crossTab);
      const desiredTab = String(remembered.tab || 'ready_dispatch');
      const desiredSearch = String(remembered.search || '').trim().toLowerCase();
      const desiredPage = Math.max(1, Number.parseInt(remembered.currentPage, 10) || 1);
      const desiredPerPage = Math.max(1, Number.parseInt(remembered.perPage, 10) || 15);

      const searchInput = document.getElementById('bt38FbmGlobalSearchInput');
      if (searchInput) searchInput.value = desiredSearch;
      const tab = document.querySelector('.fbm-lifecycle-tab[data-fbm-tab="' + desiredTab + '"]')
        || document.querySelector('.fbm-lifecycle-tab[data-fbm-tab].active');
      if (tab) tab.click();

      const select = document.getElementById('bt38ResultsPerPageSelect');
      if (select && Array.from(select.options || []).some(function (option) { return Number.parseInt(option.value, 10) === desiredPerPage; })) {
        select.value = String(desiredPerPage);
      }
      page.currentPage = desiredPage;
      controller.renderPage(page.name);
      alignAllRowVisibility();
      rememberView();
    }, 0);
  }

  function wireViewMemory() {
    if (!onFbm() || document.documentElement.dataset.bt38FbmViewMemory === '1') return;
    document.documentElement.dataset.bt38FbmViewMemory = '1';

    document.addEventListener('click', function (event) {
      const target = event.target && event.target.closest
        ? event.target.closest('.fbm-lifecycle-tab[data-fbm-tab], .bt38-page-nav .bt38-page-link')
        : null;
      if (!target) return;
      window.setTimeout(rememberView, 0);
    });

    document.addEventListener('input', function (event) {
      if (event.target && event.target.id === 'bt38FbmGlobalSearchInput') {
        window.setTimeout(rememberView, 0);
      }
    });

    document.addEventListener('change', function (event) {
      if (event.target && event.target.id === 'bt38ResultsPerPageSelect') {
        window.setTimeout(rememberView, 0);
      }
    });
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
    wireViewMemory();
    reconcileLifecycleViewAfterPageController();
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
