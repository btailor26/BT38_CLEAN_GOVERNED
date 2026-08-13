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
    return SimpleNamespace(api_key=json.dumps(credentials or {}))


def test_authorization_scope_is_complete_and_legacy_refresh_stays_safe(monkeypatch):
    monkeypatch.delenv("EBAY_SCOPES", raising=False)

    authorization = governed_ebay_oauth_scopes().split()
    legacy_refresh = governed_ebay_refresh_scopes({}).split()

    assert "https://api.ebay.com/oauth/api_scope/sell.listing.read" in authorization
    assert "https://api.ebay.com/oauth/api_scope/commerce.notification.subscription" in authorization
    assert "https://api.ebay.com/oauth/api_scope/sell.listing.read" not in legacy_refresh
    assert governed_ebay_refresh_scopes({"oauth_granted_scope": "scope-a scope-b"}) == "scope-a scope-b"


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

    result = registration.ensure_ebay_order_notification_registration(
        store=_store(),
        access_token="token",
    )

    assert calls == [("ORDER_CONFIRMATION", "1.1"), ("LISTING", "1.0")]
    assert result["ok"] is True
    assert result["registration_status"] == "SUCCESS"
    assert result["listing_subscription"]["status"] == "ENABLED"
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

    assert first["ok"] is True
    assert first["registration_status"] == "PARTIAL"
    assert first["subscription_id"] == "order-subscription"
    assert first["listing_subscription"]["status"] == "AUTHORIZATION_REQUIRED"
    assert persisted["ebay_notification_order_subscription_status"] == "ENABLED"
    assert persisted["ebay_notification_listing_subscription_status"] == "AUTHORIZATION_REQUIRED"

    calls.clear()
    second = registration.ensure_ebay_order_notification_registration(
        store=store,
        access_token="token",
    )

    assert calls == [("ORDER_CONFIRMATION", "1.1")]
    assert second["listing_subscription"]["skipped"] is True
    assert fake_db.commits == 2
