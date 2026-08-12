import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
PORT = 5000
URL = f"http://{HOST}:{PORT}/"
ROOT = Path(__file__).resolve().parent
INSTANCE_DIR = ROOT / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)


def port_is_open():
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def python_executable():
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console_python = exe.with_name("python.exe")
        if console_python.exists():
            return str(console_python)
    return str(exe)


if not port_is_open():
    env = os.environ.copy()
    env.update({
        "APP_ENV": "dev",
        "ALLOW_SQLITE_DEV": "true",
        "LOCAL_SQLITE_DB": "sqlite:///instance/bt38_ims_local.db",
        "SESSION_SECRET": "bt38-local-pnl-only",
        "PUSH_ENABLED": "false",
        "EXECUTION_MODE": "read-only",
        "ENABLE_SYNC_WORKERS": "false",
        "ENABLE_PUSH_JOBS": "false",
        "ENABLE_SCHEDULERS": "false",
        "ENABLE_GOVERNED_RUNTIME_ENGINE": "false",
        "PYTHONUNBUFFERED": "1",
    })

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(
        [
            python_executable(),
            "-m", "flask",
            "--app", "app.py",
            "run",
            "--host", HOST,
            "--port", str(PORT),
            "--no-debugger",
            "--no-reload",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        if port_is_open():
            break
        time.sleep(0.25)

webbrowser.open(URL)
