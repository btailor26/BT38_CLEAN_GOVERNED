"""Centralized runtime status writer for governed execution.

Only dispatcher/smart push runtime paths should call these helpers for execution
status changes. Routes and legacy services should not write fake runtime status.
"""
from datetime import datetime
from extensions import db


def set_store_runtime_status(store, status, *, last_sync=False):
    """Set Store runtime sync status from the governed dispatcher path."""
    if not store:
        return
    store.sync_status = status
    if last_sync:
        store.last_sync = datetime.utcnow()


def mark_listing_push_success(listing, quantity=None):
    """Record successful marketplace push state for a listing."""
    if not listing:
        return
    listing.last_push_at = datetime.utcnow()
    if quantity is not None:
        listing.last_push_quantity = quantity
    listing.last_push_status = 'success'
    listing.last_push_error = None
    listing.consecutive_failures = 0
    listing.push_attempts = 0


def mark_listing_push_failure(listing, error_message, *, blocked=False):
    """Record failed marketplace push state for a listing."""
    if not listing:
        return
    listing.last_push_status = 'error'
    listing.consecutive_failures = (listing.consecutive_failures or 0) + 1
    listing.push_attempts = (listing.push_attempts or 0) + 1
    listing.last_push_error = error_message
    if blocked:
        listing.push_state = 'blocked'
        listing.consecutive_failures = 0
    elif listing.consecutive_failures >= 5:
        listing.push_state = 'needs_review'


def commit_runtime_status():
    """Commit runtime status changes from governed execution."""
    db.session.commit()
