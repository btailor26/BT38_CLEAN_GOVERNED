import json
import sys
from types import SimpleNamespace

import services.governed_ebay_notification_registration as registration
from services.governed_ebay_oauth_scopes import (
    governed_ebay_oauth_scopes,
    governed_ebay_refresh_scopes,
)


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.session = self

    def commit(self):
        self.commits += 1


def _store(credentials=None):
    return SimpleNamespace(
        api_key=json.dumps(credentials or {}),
        auth_status="ok",
        auth_error_code=None,
        auth_error_message=None,
        auth_error_at=None,
    )


def test_authorization_scope_is_complete_and_refresh_reuses_granted_scope(monkeypatch):
    monkeypatch.delenv("EBAY_SCOPES", raising=False)

    authorization = governed_ebay_oauth_scopes().split()

    assert "https://api.ebay.com/oauth/api_scope/sell.listing.read" in authorization
    assert "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription" in authorization
    assert "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription.readonly" in authorization
    assert governed_ebay_refresh_scopes({"oauth_granted_scope": "scope-a scope-b"}) == "scope-a scope-b"


def test_destination_permission_failure_persists_commercial_reauthorization_state(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setitem(sys.modules, "app", SimpleNamespace(db=fake_db))
    monkeypatch.setenv("EBAY_NOTIFICATION_VERIFICATION_TOKEN", "a" * 32)

    def fail_destination(**kwargs):
        raise registration.EbayNotificationRegistrationError(
            'get eBay notification destinations failed: HTTP 403: '
            '{"errors": [{"message": "Access denied", '
            '"longMessage": "Insufficient permissions to fulfill the request."}]}'
        )

    monkeypatch.setattr(registration, "_ensure_destination", fail_destination)
    store = _store()

    result = registration.ensure_ebay_order_notification_registration(
        store=store,
        access_token="old-token",
    )
    persisted = json.loads(store.api_key)

    assert result["ok"] is False
    assert result["success"] is False
    assert result["authorization_required"] is True
    assert result["registration_status"] == "AUTHORIZATION_REQUIRED"
    assert "HTTP 403" in result["error"]
    assert persisted["ebay_reauthorization_required"] is True
    assert persisted["ebay_notification_registration_status"] == "AUTHORIZATION_REQUIRED"
    assert store.auth_status == "auth_error"
    assert store.auth_error_code == "ebay_notification_reauthorization_required"
    assert "one-time approval" in store.auth_error_message
    assert store.auth_error_at is not None
    assert fake_db.commits == 1


def test_registration_uses_each_topics_supported_schema(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setitem(sys.modules, "app", SimpleNamespace(db=fake_db))
    monkeypatch.setenv("EBAY_NOTIFICATION_VERIFICATION_TOKEN", "a" * 32)
    monkeypatch.setattr(registration, "_ensure_destination", lambda **kwargs: ("destination", False))
    calls = []

    def ensure_subscription(**kwargs):
        calls.append((kwargs["topic_id"], kwargs["schema_version"]))
        return f'{kwargs["topic_id"]}-subscription', False

    monkeypatch.setattr(registration, "_ensure_subscription", ensure_subscription)

    store = _store({
        "oauth_granted_scope": (
            "https://api.ebay.com/oauth/api_scope/sell.listing.read "
            "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription"
        )
    })
    result = registration.ensure_ebay_order_notification_registration(
        store=store,
        access_token="token",
    )
    persisted = json.loads(store.api_key)

    assert calls == [("ORDER_CONFIRMATION", "1.1"), ("LISTING", "1.0")]
    assert result["ok"] is True
    assert result["success"] is True
    assert result["registration_status"] == "SUCCESS"
    assert result["authorization_required"] is False
    assert result["listing_subscription"]["status"] == "ENABLED"
    assert persisted["ebay_reauthorization_required"] is False
    assert store.auth_status == "ok"
    assert store.auth_error_code is None
    assert fake_db.commits == 1


def test_listing_authorization_failure_preserves_order_and_is_not_retried(monkeypatch):
    fake_db = _FakeDB()
    monkeypatch.setitem(sys.modules, "app", SimpleNamespace(db=fake_db))
    monkeypatch.setenv("EBAY_NOTIFICATION_VERIFICATION_TOKEN", "a" * 32)
    monkeypatch.setattr(registration, "_ensure_destination", lambda **kwargs: ("destination", False))
    calls = []

    def ensure_subscription(**kwargs):
        calls.append((kwargs["topic_id"], kwargs["schema_version"]))
        if kwargs["topic_id"] == "LISTING":
            raise registration.EbayNotificationRegistrationError(
                'HTTP 403: {"errorId": 195011, "message": "Not authorized for this topic."}'
            )
        return "order-subscription", False

    monkeypatch.setattr(registration, "_ensure_subscription", ensure_subscription)
    store = _store()

    first = registration.ensure_ebay_order_notification_registration(
        store=store,
        access_token="token",
    )
    persisted = json.loads(store.api_key)

    assert first["ok"] is False
    assert first["success"] is False
    assert first["authorization_required"] is True
    assert first["registration_status"] == "AUTHORIZATION_REQUIRED"
    assert first["subscription_id"] == "order-subscription"
    assert first["listing_subscription"]["status"] == "AUTHORIZATION_REQUIRED"
    assert persisted["ebay_notification_order_subscription_status"] == "ENABLED"
    assert persisted["ebay_notification_listing_subscription_status"] == "AUTHORIZATION_REQUIRED"
    assert persisted["ebay_reauthorization_required"] is True
    assert store.auth_status == "auth_error"
    assert store.auth_error_code == "ebay_notification_reauthorization_required"

    calls.clear()
    second = registration.ensure_ebay_order_notification_registration(
        store=store,
        access_token="token",
    )

    assert calls == [("ORDER_CONFIRMATION", "1.1")]
    assert second["ok"] is False
    assert second["authorization_required"] is True
    assert second["listing_subscription"]["skipped"] is True
    assert fake_db.commits == 2
