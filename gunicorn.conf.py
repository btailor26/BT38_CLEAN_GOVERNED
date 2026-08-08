"""
Gunicorn configuration for the governed BT38 runtime.

The governed event queue is process-memory only, so HTTP requests and the
single runtime owner must share one process. Threaded concurrency keeps normal
page/API requests concurrent without creating a second process-local event
queue that the runtime cannot see.
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

# Bind address (may be overridden by command line --bind).
bind = '0.0.0.0:5000'
