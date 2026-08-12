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
LOG_PATH = INSTANCE_DIR / "bt38_pnl_startup.log"


def port_is_open():
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def show_error(message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("BT P&L could not start", message)
        root.destroy()
    except Exception:
        pass


def candidate_python_commands():
    commands = []
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console_python = exe.with_name("python.exe")
        if console_python.exists():
            commands.append([str(console_python)])
    elif exe.exists():
        commands.append([str(exe)])
    if os.name == "nt":
        commands.extend([["py", "-3.11"], ["py", "-3"], ["python"]])
    return commands


def start_server():
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

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    LOG_PATH.write_text("BT P&L local startup\n", encoding="utf-8")
    server_script = ROOT / "LOCAL_PNL_SERVER.py"

    if not server_script.exists():
        show_error(f"Missing local server file:\n{server_script}")
        return False

    last_error = None
    for python_cmd in candidate_python_commands():
        try:
            with open(LOG_PATH, "a", encoding="utf-8", buffering=1) as log:
                command = python_cmd + [str(server_script)]
                log.write("\nStarting: " + " ".join(command) + "\n")
                process = subprocess.Popen(
                    command,
                    cwd=str(ROOT),
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                )

                deadline = time.time() + 45
                while time.time() < deadline:
                    if port_is_open():
                        return True
                    if process.poll() is not None:
                        break
                    time.sleep(0.25)
                last_error = f"Process exited with code {process.poll()}"
        except Exception as exc:
            last_error = str(exc)

    try:
        log_text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
        tail = log_text[-3500:]
    except Exception:
        tail = last_error or "Unknown startup error"

    show_error(
        "BT P&L did not start on http://127.0.0.1:5000/.\n\n"
        "You do not need to open a command window.\n\n"
        "Startup details were saved to:\n"
        f"{LOG_PATH}\n\n"
        "Last startup details:\n" + tail
    )
    return False


if port_is_open() or start_server():
    webbrowser.open(URL)
