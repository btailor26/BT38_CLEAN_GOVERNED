"""Event-driven UI freshness signal for completed marketplace webhooks.

Contract:
- No webhook means no database polling and no marketplace polling.
- Open governed pages keep one sleeping Server-Sent Events connection.
- A completed Amazon/eBay webhook publishes one in-process UI signal.
- The browser rereads BT38 truth only after that signal.

The governed Gunicorn runtime intentionally uses one process, so this small
in-memory condition is shared by all request threads without creating a second
runtime or database authority.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime

from flask import Response, g, request, stream_with_context
from flask_login import login_required

from app import app


_condition = threading.Condition()
_revision = 0
_latest_event: dict | None = None

_LIVE_UI_PATHS = {
    "/warehouse",
    "/product-linking",
    "/amazon-fba-stock",
    "/listings",
    "/orders-mcf",
}

_WEBHOOK_PATHS = {
    "/governed/webhooks/amazon": "amazon",
    "/governed/webhooks/ebay": "ebay",
}


def publish_webhook_ui_event(*, platform: str, notification_record_id: int) -> int:
    """Wake open UI listeners after governed webhook processing completes."""
    global _revision, _latest_event

    with _condition:
        _revision += 1
        _latest_event = {
            "revision": _revision,
            "platform": str(platform or "").strip().lower(),
            "notification_record_id": int(notification_record_id),
            "published_at": datetime.utcnow().isoformat() + "Z",
        }
        _condition.notify_all()
        return _revision


def _event_stream():
    """Sleep until a webhook event exists; keepalive never touches Neon."""
    seen_revision = 0

    # Tell EventSource to reconnect quickly if Fly/proxy closes the socket.
    yield "retry: 2000\n\n"

    while True:
        with _condition:
            if _revision <= seen_revision:
                # Condition.wait releases the lock and sleeps the thread. The
                # timeout is only an HTTP keepalive; it performs no DB read.
                _condition.wait(timeout=60.0)

            current_revision = _revision
            event = dict(_latest_event or {})

        if current_revision > seen_revision and event:
            seen_revision = current_revision
            yield "event: bt38-update\n"
            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        else:
            yield ": sleep\n\n"


@app.get("/governed/ui/events")
@login_required
def governed_ui_events():
    response = Response(
        stream_with_context(_event_stream()),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.after_request
def publish_completed_webhook_and_attach_live_ui(response):
    """Publish after webhook completion and attach a sleeping browser listener."""
    path = request.path.rstrip("/") or "/"

    if request.method == "POST" and path in _WEBHOOK_PATHS:
        record_id = getattr(g, "bt38_notification_record_id", None)
        payload = response.get_json(silent=True)
        failed_after_capture = (
            isinstance(payload, dict)
            and payload.get("status") == "processing_failed"
        )

        if (
            record_id is not None
            and response.status_code < 400
            and not failed_after_capture
        ):
            publish_webhook_ui_event(
                platform=_WEBHOOK_PATHS[path],
                notification_record_id=int(record_id),
            )

        return response

    if request.method != "GET" or path not in _LIVE_UI_PATHS:
        return response

    content_type = str(response.content_type or "").lower()
    if "text/html" not in content_type:
        return response

    body = response.get_data(as_text=True)
    if "bt38WebhookLiveEvents" in body or "</body>" not in body:
        return response

    script = r'''
<script id="bt38WebhookLiveEvents">
(function(){
  if (!window.EventSource || window.bt38WebhookLiveEventsInstalled) return;
  window.bt38WebhookLiveEventsInstalled = true;

  let pendingRefresh = false;
  const source = new EventSource("/governed/ui/events", {withCredentials: true});

  source.addEventListener("bt38-update", function(){
    if (document.hidden) {
      pendingRefresh = true;
      return;
    }
    window.location.reload();
  });

  document.addEventListener("visibilitychange", function(){
    if (!document.hidden && pendingRefresh) {
      pendingRefresh = false;
      window.location.reload();
    }
  });

  window.addEventListener("beforeunload", function(){
    source.close();
  }, {once: true});
})();
</script>
'''

    response.set_data(body.replace("</body>", script + "\n</body>", 1))
    return response
