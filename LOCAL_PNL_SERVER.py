import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INSTANCE_DIR = ROOT / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

# Local-only, read-only startup defaults. These are set before importing app.py.
os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("ALLOW_SQLITE_DEV", "true")
os.environ.setdefault("LOCAL_SQLITE_DB", "sqlite:///instance/bt38_ims_local.db")
os.environ.setdefault("SESSION_SECRET", "bt38-local-pnl-only")
os.environ.setdefault("PUSH_ENABLED", "false")
os.environ.setdefault("EXECUTION_MODE", "read-only")
os.environ.setdefault("ENABLE_SYNC_WORKERS", "false")
os.environ.setdefault("ENABLE_PUSH_JOBS", "false")
os.environ.setdefault("ENABLE_SCHEDULERS", "false")
os.environ.setdefault("ENABLE_GOVERNED_RUNTIME_ENGINE", "false")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from app import app

if __name__ == "__main__":
    # Plain localhost only. No debug reloader and no external binding.
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
