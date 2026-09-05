from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = (ROOT / "extensions.py").read_text(encoding="utf-8")
PROFILE = (
    ROOT / "services" / "governed_amazon_fbm_profile_event_alignment.py"
).read_text(encoding="utf-8")


def test_existing_profile_event_alignment_is_installed_with_db_app_init():
    assert "class BT38SQLAlchemy(SQLAlchemy):" in EXTENSIONS
    assert "install_governed_amazon_fbm_profile_event_alignment(app)" in EXTENSIONS


def test_prime_and_premium_come_from_current_amazon_webhook():
    assert '"OrderPrograms"' in PROFILE
    assert 'if "prime" in _program_names(payload):' in PROFILE
    assert 'is_premium = True if "premium" in program_names else None' in PROFILE
    assert "profile.is_prime = is_prime" in PROFILE
    assert "profile.is_premium = is_premium" in PROFILE


def test_profile_alignment_remains_current_event_only():
    lowered = PROFILE.lower()
    assert 'request.path.rstrip("/") != "/governed/webhooks/amazon"' in PROFILE
    assert "setinterval(" not in lowered
    assert "threading.thread" not in lowered
    assert "90 days" not in lowered
    assert "backfill" in lowered  # only the module's explicit no-backfill contract text
    assert "get_or_refresh_amazon_profile" not in PROFILE
