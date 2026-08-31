// FBM session-view alignment for the existing shared BT38 marketplace event.
// This opens no SSE connection, creates no poller/interval, performs no marketplace
// read and writes nothing. The event has already persisted marketplace truth; this
// refreshes the current session's normal DB-backed /fbm representation only.
(function () {
  'use strict';
  if (window.bt38FbmEventSessionRefreshInstalled) return;
  window.bt38FbmEventSessionRefreshInstalled = true;

  let running = false;
  let pending = false;
  let lastSequence = '';

  function onFbm() {
    return String(window.location.pathname || '').replace(/\/$/, '') === '/fbm';
  }

  function sequenceOf(event) {
    return String(event && event.detail && event.detail.sequence || '').trim();
  }

  async function refreshFromPersistedFbm() {
    if (!onFbm() || running) {
      if (running) pending = true;
      return;
    }
    if (document.visibilityState === 'hidden') {
      pending = true;
      return;
    }

    running = true;
    try {
      const response = await fetch(window.location.href, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: {'Accept': 'text/html', 'X-BT38-Event-Refresh': '1'}
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const html = await response.text();
      const next = new DOMParser().parseFromString(html, 'text/html');

      // Replace the canonical FBM content region as rendered by the normal route,
      // preserving the current URL/session filters while taking fresh DB truth.
      const currentTable = document.querySelector('.fbm-orders-table');
      const nextTable = next.querySelector('.fbm-orders-table');
      if (currentTable && nextTable) currentTable.replaceWith(nextTable);

      const currentHealth = document.querySelector('.fbm-period-grid');
      const nextHealth = next.querySelector('.fbm-period-grid');
      if (currentHealth && nextHealth) currentHealth.replaceWith(nextHealth);

      const currentAlert = document.getElementById('bt38FbmOverdueAlert');
      const nextAlert = next.getElementById('bt38FbmOverdueAlert');
      if (currentAlert && nextAlert) currentAlert.replaceWith(nextAlert);
      else if (currentAlert && !nextAlert) currentAlert.remove();
      else if (!currentAlert && nextAlert && document.querySelector('.fbm-orders-table')) {
        document.querySelector('.fbm-orders-table').insertAdjacentElement('beforebegin', nextAlert);
      }

      if (window.feather) window.feather.replace();
    } catch (error) {
      console.warn('[BT38 UI] FBM persisted event refresh unavailable', error);
    } finally {
      running = false;
      if (pending && document.visibilityState === 'visible') {
        pending = false;
        void refreshFromPersistedFbm();
      }
    }
  }

  window.addEventListener('bt38-marketplace-event', function (event) {
    if (!onFbm()) return;
    const sequence = sequenceOf(event);
    if (sequence && sequence === lastSequence) return;
    if (sequence) lastSequence = sequence;
    void refreshFromPersistedFbm();
  });

  document.addEventListener('visibilitychange', function () {
    if (!onFbm() || document.visibilityState !== 'visible' || !pending) return;
    pending = false;
    void refreshFromPersistedFbm();
  });
})();
