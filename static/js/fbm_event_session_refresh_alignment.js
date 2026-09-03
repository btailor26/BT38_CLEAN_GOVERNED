// FBM browser-session presentation alignment.
// Warehouse is the reference model: the page is a bounded projection of persisted DB truth.
// Committed-event refresh ownership stays in fbm_tracking_journey.js, which reuses the
// application shell's single governed event and performs one DB-backed snapshot read.
// This helper never polls, never opens a second live transport, never reloads or fetches the page,
// and never subscribes to marketplace events. With no event, the FBM session sleeps.
(function () {
  'use strict';
  if (window.bt38FbmEventSessionRefreshInstalled) return;
  window.bt38FbmEventSessionRefreshInstalled = true;

  function onFbm() {
    return String(window.location.pathname || '').replace(/\/$/, '') === '/fbm';
  }

  function getSessionState(defaults) {
    if (window.BT38 && typeof window.BT38.getPageSession === 'function') {
      return window.BT38.getPageSession('fbm', defaults || {});
    }
    return Object.assign({}, defaults || {});
  }

  function pageState() {
    const pages = window.BT38 && window.BT38.pages;
    return pages && (pages.fbm || pages.FBM);
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

  function applySessionTabAfterPageController() {
    if (!onFbm()) return false;
    const page = pageState();
    const controller = window.BT38 && window.BT38.PageController;
    if (!page || page.ready !== true || !controller || typeof controller.renderPage !== 'function') return false;

    const session = getSessionState({tab: 'ready_dispatch'});
    const activeTab = String(session && session.tab || 'ready_dispatch');
    const selectedTab = document.querySelector('.fbm-lifecycle-tab[data-fbm-tab="' + activeTab + '"]')
      || document.querySelector('.fbm-lifecycle-tab[data-fbm-tab="ready_dispatch"]');
    if (selectedTab) {
      selectedTab.click();
    } else {
      page.currentPage = 1;
      controller.renderPage(page.name);
    }
    alignAllRowVisibility();
    return true;
  }

  // The lifecycle script and shared PageController initialise independently.
  // Use the browser load boundary once when needed; there is no retry or timer loop.
  function reconcileSessionAfterPageController() {
    if (!onFbm()) return;
    if (applySessionTabAfterPageController()) return;
    window.addEventListener('load', function () {
      applySessionTabAfterPageController();
    }, {once: true});
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

  function initialise() {
    if (!onFbm()) return;
    watchSessionRows();
    reconcileSessionAfterPageController();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, {once: true});
  } else {
    initialise();
  }
})();
