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

  function refreshCurrentPage() {
    if (scheduled) return;
    scheduled = true;

    // Event coalescing only. This is not polling and performs no DB read itself.
    window.setTimeout(function () {
      if (document.visibilityState === 'hidden') {
        scheduled = false;
        pendingWhileHidden = true;
        return;
      }

      // A normal navigation gives every page its existing server-rendered DB
      // contract and re-runs its own controller safely. No partial-DOM script
      // re-execution, no duplicate page-specific fetch path.
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
