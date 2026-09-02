// FBM session-view alignment for the existing shared BT38 marketplace event.
// Warehouse is the reference model: no poller, no extra SSE, no marketplace read
// and no automatic full-page DB refresh. Persisted events only mark this browser
// session stale. Hidden pages sleep and keep the pending marker until visible.
(function () {
  'use strict';
  if (window.bt38FbmEventSessionRefreshInstalled) return;
  window.bt38FbmEventSessionRefreshInstalled = true;

  let pending = false;
  let lastSequence = '';

  function onFbm() {
    return String(window.location.pathname || '').replace(/\/$/, '') === '/fbm';
  }

  function sequenceOf(event) {
    return String(event && event.detail && event.detail.sequence || '').trim();
  }

  function setSessionDirty(sequence) {
    if (window.BT38 && typeof window.BT38.setPageSession === 'function') {
      window.BT38.setPageSession('fbm', {
        dirty: true,
        pendingSequence: sequence || lastSequence || ''
      });
    }
  }

  function ensureRefreshNotice() {
    if (!onFbm() || document.getElementById('bt38FbmSessionRefreshNotice')) return;
    const table = document.querySelector('.fbm-orders-table');
    if (!table) return;
    const host = table.closest('.card') || table.parentElement;
    if (!host) return;

    const notice = document.createElement('div');
    notice.id = 'bt38FbmSessionRefreshNotice';
    notice.className = 'alert alert-info py-2 px-3 m-2 d-flex align-items-center justify-content-between gap-2';
    notice.innerHTML = '<span class="small">New marketplace information is ready.</span><button type="button" class="btn btn-sm btn-outline-primary">Refresh FBM</button>';
    const button = notice.querySelector('button');
    button.addEventListener('click', function () {
      if (window.BT38 && typeof window.BT38.setPageSession === 'function') {
        window.BT38.setPageSession('fbm', {dirty: false, pendingSequence: ''});
      }
      window.location.reload();
    });
    host.insertBefore(notice, host.firstChild);
  }

  function markPending(sequence) {
    pending = true;
    setSessionDirty(sequence);
    if (document.visibilityState === 'hidden') return;
    ensureRefreshNotice();
  }

  window.addEventListener('bt38-marketplace-event', function (event) {
    if (!onFbm()) return;
    const sequence = sequenceOf(event);
    if (sequence && sequence === lastSequence) return;
    if (sequence) lastSequence = sequence;
    markPending(sequence);
  });

  document.addEventListener('visibilitychange', function () {
    if (!onFbm() || document.visibilityState !== 'visible' || !pending) return;
    ensureRefreshNotice();
  });

  if (onFbm() && window.BT38 && typeof window.BT38.getPageSession === 'function') {
    const state = window.BT38.getPageSession('fbm', {dirty: false});
    pending = Boolean(state && state.dirty);
    if (pending && document.visibilityState === 'visible') ensureRefreshNotice();
  }
})();
