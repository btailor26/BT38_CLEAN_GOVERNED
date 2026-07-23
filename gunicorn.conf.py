"""
Gunicorn configuration for multi-channel inventory management system.
Handles long-running sync operations that can take 5+ minutes.
"""

# Worker count: 2 workers for better concurrency
# Each Fly machine has 512MB, ~200MB per worker is safe
workers = 2

# Threads per worker: Allows handling concurrent requests within each worker
threads = 2

# Worker timeout: Allow sync operations to complete
# eBay sync with 641 items can take 5+ minutes
timeout = 600  # 10 minutes

# Graceful timeout: Allow workers to finish current requests
graceful_timeout = 60  # 1 minute

# Keep-alive: Prevent connection timeout during long operations
keepalive = 120  # 2 minutes

# Worker class: Use gthread for thread support
worker_class = 'gthread'

# Logging
loglevel = 'info'
accesslog = '-'  # Log to stdout
errorlog = '-'   # Log to stdout

# Bind address (will be overridden by command line --bind)
bind = '0.0.0.0:5000'
