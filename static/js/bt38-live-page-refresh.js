// Shared BT38 event-driven page refresh controller.
// One server SSE connection is owned by base.html; this file never opens one.
// No polling, no intervals, no marketplace reads. A committed live event causes
// at most one browser refresh per distinct sequence, with rapid duplicate events
// coalesced before navigation.
(function () {
  'use strict';

  if (window.bt38LivePageRefreshInstalled) return;
  window.bt38LivePageRefreshInstalled = true;

  let scheduled = false;
  let pendingWhileHidden = false;
  let lastSequence = '';

  function sequenceOf(event) {
    return String(event?.detail?.sequence || '').trim();
  }

  function pageOwnsCommittedRefresh() {
    // Orders / MCF already performs one narrow DB-only table refresh from the
    // same shared event. Do not add a second current-page request there.
    return Boolean(document.getElementById('mcf-orders-body'));
  }

  function refreshCurrentPage() {
    if (pageOwnsCommittedRefresh()) return;
    if (scheduled) return;
    scheduled = true;

    // Event coalescing only. This timer never queries the DB and is not polling.
    window.setTimeout(function () {
      if (document.visibilityState === 'hidden') {
        scheduled = false;
        pendingWhileHidden = true;
        return;
      }

      // Reuse each page's existing server-rendered DB contract. One committed
      // event causes one navigation; there is no periodic refresh loop.
      window.location.reload();
    }, 250);
  }

  window.addEventListener('bt38-marketplace-event', function (event) {
    const sequence = sequenceOf(event);
    if (sequence && sequence === lastSequence) return;
    if (sequence) lastSequence = sequence;
    refreshCurrentPage();
  });

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible' || !pendingWhileHidden) return;
    pendingWhileHidden = false;
    refreshCurrentPage();
  });
})();
