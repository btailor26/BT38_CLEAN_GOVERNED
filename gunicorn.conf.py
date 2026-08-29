"""
Gunicorn configuration for the governed BT38 runtime.

The governed event queue is process-memory only, so HTTP requests and the
single runtime owner must share one process. Threaded concurrency keeps normal
page/API requests concurrent without creating a second process-local event
queue that the runtime cannot see.

Low-DB startup policy:
- app import must not start the historical broad runtime loop;
- after the worker/app is initialized, install the low-DB event loop and start
  the existing governed runtime owner;
- webhook/SQS events remain the immediate path;
- the only timed safety net is the bounded eBay missed-listing check: once at
  worker start and then at most every eight hours, newest Item IDs only, with
  existing MarketplaceListing Item IDs skipped before any import write.
"""

# One process is required while the governed event queue remains in memory.
# Multiple Gunicorn processes would create isolated queues, while only one
# process owns the governed runtime lock.
workers = 1

# Preserve the previous four-request concurrency using threads in the same
# process so every request can signal the same governed runtime queue.
threads = 4

# Worker timeout: allow explicit marketplace operations to complete.
timeout = 600  # 10 minutes

# Graceful timeout: allow the worker to finish current requests.
graceful_timeout = 60  # 1 minute

# Keep-alive for normal browser/API connections.
keepalive = 120  # 2 minutes

# Threaded worker class for concurrent I/O-bound marketplace operations.
worker_class = 'gthread'

# Logging
loglevel = 'info'
accesslog = '-'
errorlog = '-'
# Include total server request time in seconds on every access-log line.
# Example: request_time=2.431 means Gunicorn took 2.431 seconds to serve it.
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s request_time=%(L)s'

# Bind address (may be overridden by command line --bind).
bind = '0.0.0.0:5000'

# Prevent app.py from starting the historical broad loop while the application
# module is importing. The worker-init hook below starts the same governed
# runtime owner only after the low-DB loop policy has been installed.
raw_env = [
    'ENABLE_GOVERNED_RUNTIME_ENGINE=false',
    'ENABLE_GOVERNED_8H_HYDRATION=false',
]


def _align_notification_noop_labels(app):
    """Relabel notification feed no-op checks without changing push execution.

    A marketplace_push_noop SyncLog means the marketplace already matched the
    governed quantity and no write occurred. The notification panel must not
    describe that verification as a marketplace push.
    """
    from flask import request

    @app.after_request
    def _bt38_align_notification_noop_response(response):
        if request.path.rstrip('/') != '/governed/ui/notifications':
            return response
        if not response.is_json:
            return response

        payload = response.get_json(silent=True)
        if payload is None:
            return response

        def align(value):
            if isinstance(value, list):
                for item in value:
                    align(item)
                return
            if not isinstance(value, dict):
                return

            evidence = ' '.join(
                str(value.get(key) or '')
                for key in (
                    'event_type',
                    'log_type',
                    'message',
                    'reason',
                    'source',
                    'raw_message',
                )
            ).lower()

            if (
                'marketplace_push_noop' in evidence
                or 'marketplace_already_matches_warehouse' in evidence
            ):
                value['title'] = 'Quantity verified'
                value['message'] = (
                    'Already aligned — no marketplace write required.'
                )
                value['no_op'] = True
                value['marketplace_write_skipped'] = True

            for child in value.values():
                if isinstance(child, (dict, list)):
                    align(child)

        align(payload)
        response.set_data(app.json.dumps(payload))
        response.headers['Content-Type'] = 'application/json'
        response.headers['Content-Length'] = str(len(response.get_data()))
        return response


def post_worker_init(worker):
    """Start governed runtime without allowing it to take down the web worker.

    The WSGI application is the availability boundary. Governed runtime startup
    remains the normal path, but a runtime bootstrap failure is logged and left
    disabled instead of raising through Gunicorn's worker-init hook and causing
    a whole-site 502/restart loop.
    """
    from main import app

    _align_notification_noop_labels(app)

    try:
        from services.governed_event_runtime import start_event_only_runtime

        started = start_event_only_runtime(app)
    except Exception:
        app.logger.exception(
            'BT38 governed runtime bootstrap failed; web worker remains available and runtime stays disabled'
        )
        return

    app.logger.info(
        'BT38 low-DB governed runtime started=%s; broad hydration disabled; bounded eBay missed-listing recovery enabled',
        started,
    )
