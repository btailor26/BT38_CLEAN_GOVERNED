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

  function rowMatchesSession(row) {
    if (!row || !row.classList || !row.classList.contains('fbm-order-row')) return false;
    const session = getSessionState({tab: 'pending', search: ''});
    const activeTab = String(session && session.tab || 'pending');
    const search = String(session && session.search || '').trim().toLowerCase();
    const queue = String(row.dataset.fbmQueue || '');
    const searchText = String(row.dataset.fbmSearch || row.textContent || '').toLowerCase();
    return queue === activeTab && (!search || searchText.indexOf(search) >= 0);
  }

  function alignRowVisibility(row) {
    if (!row || !row.classList || !row.classList.contains('fbm-order-row')) return;
    if (!row.dataset.fbmQueue) {
      row.style.display = row.hidden ? 'none' : '';
      return;
    }
    const shouldShow = rowMatchesSession(row);
    if (!shouldShow) {
      row.hidden = true;
      row.style.display = 'none';
    }
  }

  function alignAllRowVisibility() {
    if (!onFbm()) return;
    document.querySelectorAll('tr.fbm-order-row').forEach(alignRowVisibility);
  }

  function applySessionTabAfterPageController() {
    if (!onFbm()) return false;
    const page = pageState();
    const controller = window.BT38 && window.BT38.PageController;
    if (!page || page.ready !== true || !controller || typeof controller.renderPage !== 'function') {
      alignAllRowVisibility();
      return false;
    }

    const session = getSessionState({tab: 'pending'});
    const activeTab = String(session && session.tab || 'pending');
    const selectedTab = document.querySelector('.fbm-lifecycle-tab[data-fbm-tab="' + activeTab + '"]')
      || document.querySelector('.fbm-lifecycle-tab[data-fbm-tab="pending"]');
    if (selectedTab) {
      selectedTab.click();
    } else {
      page.currentPage = 1;
      controller.renderPage(page.name);
    }
    alignAllRowVisibility();
    return true;
  }

  function reconcileSessionAfterPageController() {
    if (!onFbm()) return;
    applySessionTabAfterPageController();
    window.addEventListener('load', function () {
      applySessionTabAfterPageController();
      alignAllRowVisibility();
    }, {once: true});
  }

  function initialise() {
    if (!onFbm()) return;
    reconcileSessionAfterPageController();
    alignAllRowVisibility();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, {once: true});
  } else {
    initialise();
  }
})();
