// Shared BT38 event-driven page refresh controller.
// One server SSE connection is owned by base.html; this file never opens one.
// No polling, no intervals, no marketplace reads. A committed live event causes
// an immediate browser refresh for each distinct sequence.
(function () {
  'use strict';

  if (window.bt38LivePageRefreshInstalled) return;
  window.bt38LivePageRefreshInstalled = true;

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

    if (document.visibilityState === 'hidden') {
      pendingWhileHidden = true;
      return;
    }

    // Immediate event-driven navigation. There is no timer, periodic refresh,
    // polling loop, marketplace read, or duplicate SSE connection here.
    window.location.reload();
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
