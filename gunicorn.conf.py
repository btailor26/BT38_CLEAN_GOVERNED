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


def post_worker_init(worker):
    """Start one governed event listener after the WSGI app is fully loaded."""
    from main import app
    from services.governed_event_runtime import start_event_only_runtime

    started = start_event_only_runtime(app)
    app.logger.info(
        'BT38 low-DB governed runtime started=%s; broad hydration disabled; bounded eBay missed-listing recovery enabled',
        started,
    )
