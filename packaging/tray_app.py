"""System tray icon that starts and stops Argus.

The entry point for the packaged desktop build (see packaging/argus.spec).
Can also be run from source for development: python packaging/tray_app.py

Requires the extra deps in packaging/tray_requirements.txt (pystray, Pillow):
    .venv/Scripts/pip install -r packaging\tray_requirements.txt   (Windows)
    .venv/bin/pip install -r packaging/tray_requirements.txt       (macOS)

Windows and macOS only -- no Linux desktop tray.
"""
from __future__ import annotations

import json
import os
import platform
import queue
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = Path(getattr(sys, "_MEIPASS", REPO_ROOT))

if not FROZEN and str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from app import __version__  # noqa: E402


def _user_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "Argus"


# Frozen builds write user data (db, crops, models, logs) to the OS-standard
# per-user directory so it survives reinstalls; source builds use the repo's
# own data/ directory, same as `python -m app`.
_DATA_ROOT  = _user_data_dir() if FROZEN else REPO_ROOT

LOG_PATH    = _DATA_ROOT / "logs" / "server.log"
CONFIG_PATH = _DATA_ROOT / "tray_config.json"
LOCK_PATH   = _DATA_ROOT / "tray.lock"

DEFAULT_CONFIG = {
    "port": 8100,
    "host": "127.0.0.1",
    "launch_at_login": False,
    "data_path": "",    # empty = use default per-user dir (frozen) or repo/data/ (source)
    "db_path": "",      # empty = DATA_PATH/argus.db
    "models_path": "",  # empty = use default per-user dir (frozen) or repo/models/ (source)
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            if isinstance(data, dict):
                return {**DEFAULT_CONFIG, **data}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


# -- Icon -----------------------------------------------------------------
def _make_icon(size: int = 256) -> Image.Image:
    """Rasterise the Argus favicon: dark rounded square + blue radar arcs."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    radius = max(4, int(size * 0.18))
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=(13, 17, 23))
    blue = (91, 138, 245)
    cx = int(size * 0.50)
    cy = int(size * 0.54)
    dot_r = max(2, int(size * 0.035))
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=blue)
    rings = [(7/100, 2.2/100), (14/100, 2.1/100), (21/100, 2.0/100), (28/100, 1.9/100)]
    for r_frac, w_frac in rings:
        r = max(3, int(size * r_frac))
        w = max(1, int(size * w_frac))
        d.arc([cx - r, cy - r, cx + r, cy + r], start=180, end=360, fill=blue, width=w)
    return img


def _badge(base: Image.Image, color: str) -> Image.Image:
    img = base.copy()
    d = ImageDraw.Draw(img)
    w, h = img.size
    r = w // 4
    d.ellipse([w - r * 2, h - r * 2, w, h], fill=color, outline="white", width=2)
    return img


# -- Single-instance lock -------------------------------------------------
def _is_process_alive(pid: int) -> bool:
    if platform.system() == "Windows":
        try:
            result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=5)
            return str(pid) in result.stdout
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _acquire_single_instance_lock() -> bool:
    if LOCK_PATH.exists():
        try:
            existing_pid = int(LOCK_PATH.read_text().strip())
        except (ValueError, OSError):
            existing_pid = None
        if existing_pid is not None and _is_process_alive(existing_pid):
            return False
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def _release_single_instance_lock() -> None:
    try:
        if LOCK_PATH.exists() and int(LOCK_PATH.read_text().strip()) == os.getpid():
            LOCK_PATH.unlink()
    except (ValueError, OSError):
        pass


# -- Port probing ---------------------------------------------------------
def _port_is_occupied(host: str, port: int) -> bool:
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((check_host, port)) == 0


def _looks_like_our_own_server(host: str, port: int) -> bool:
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    try:
        with urllib.request.urlopen(f"http://{check_host}:{port}/api/health", timeout=2) as resp:
            return json.loads(resp.read()).get("status") == "ok"
    except (OSError, urllib.error.URLError, ValueError):
        return False


def _find_listening_pid(port: int) -> int | None:
    try:
        if platform.system() == "Windows":
            result = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    return int(line.split()[-1])
        else:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=5,
            )
            pids = result.stdout.split()
            if pids:
                return int(pids[0])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _kill_pid(pid: int, force: bool) -> None:
    if platform.system() == "Windows":
        args = ["taskkill", "/PID", str(pid)]
        if force:
            args.append("/F")
        subprocess.run(args, capture_output=True)
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        pass


# -- Launch at login ------------------------------------------------------
def _windows_startup_bat_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Argus.bat"


def _set_launch_at_login_windows(enabled: bool) -> None:
    path = _windows_startup_bat_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        if FROZEN:
            content = f'@echo off\r\nstart "" "{sys.executable}"\r\n'
        else:
            ps1 = f"{REPO_ROOT}\\packaging\\tray.ps1"
            content = (
                "@echo off\r\n"
                f'start "" powershell -NoProfile -WindowStyle Hidden'
                f' -ExecutionPolicy Bypass -File "{ps1}"\r\n'
            )
        path.write_text(content)
    else:
        path.unlink(missing_ok=True)


def _mac_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / "com.argus.tray.plist"


def _set_launch_at_login_mac(enabled: bool) -> None:
    path = _mac_launch_agent_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        exe = str(sys.executable) if FROZEN else f"{REPO_ROOT}/packaging/tray.sh"
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.argus.tray</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""
        path.write_text(plist)
        subprocess.run(["launchctl", "load", str(path)], check=False)
    else:
        subprocess.run(["launchctl", "unload", str(path)], check=False)
        path.unlink(missing_ok=True)


def _set_launch_at_login(enabled: bool) -> None:
    system = platform.system()
    if system == "Windows":
        _set_launch_at_login_windows(enabled)
    elif system == "Darwin":
        _set_launch_at_login_mac(enabled)


# -- Dialogs --------------------------------------------------------------
# Windows: tkinter marshaled onto the real main thread (pystray callbacks run
# on worker threads; confirmed live -- dialogs rendered but never responded
# to clicks when created off-thread).
# macOS: osascript instead of tkinter (Tk 9.0 hard-crashes on a real Mac in
# its own Cocoa color handling -- upstream bug, not fixable here).
def _ask_string(root, prompt: str, initial: str) -> str | None:
    if platform.system() == "Darwin":
        return _osascript_ask_string(prompt, initial)
    from tkinter import simpledialog
    return simpledialog.askstring("Argus", prompt, initialvalue=initial, parent=root)


def _ask_yes_no(root, prompt: str) -> bool:
    if platform.system() == "Darwin":
        return _osascript_ask_yes_no(prompt)
    from tkinter import messagebox
    return messagebox.askyesno("Argus", prompt, parent=root)


def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _osascript_ask_yes_no(prompt: str) -> bool:
    script = (
        f'display dialog "{_applescript_escape(prompt)}" with title "Argus" '
        f'buttons {{"Cancel", "Stop it"}} default button "Stop it" cancel button "Cancel"'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0 and "button returned:Stop it" in result.stdout


def _osascript_ask_string(prompt: str, initial: str) -> str | None:
    script = (
        f'display dialog "{_applescript_escape(prompt)}" '
        f'default answer "{_applescript_escape(initial)}" with title "Argus"'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    marker = "text returned:"
    if marker not in out:
        return None
    return out.split(marker, 1)[1].rsplit(", button returned:", 1)[0]


def _ask_directory(root, prompt: str) -> str | None:
    if platform.system() == "Darwin":
        script = f'POSIX path of (choose folder with prompt "{_applescript_escape(prompt)}")'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    from tkinter import filedialog
    path = filedialog.askdirectory(title=prompt, parent=root)
    return path or None


def _ask_open_file(root, prompt: str) -> str | None:
    if platform.system() == "Darwin":
        script = f'POSIX path of (choose file with prompt "{_applescript_escape(prompt)}")'
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    from tkinter import filedialog
    path = filedialog.askopenfilename(title=prompt, filetypes=[("SQLite DB", "*.db"), ("All files", "*")], parent=root)
    return path or None


def _ask_save_path(root, initial_name: str) -> str | None:
    if platform.system() == "Darwin":
        return _osascript_ask_save_path(initial_name)
    from tkinter import filedialog
    path = filedialog.asksaveasfilename(
        title="Save Argus logs", initialfile=initial_name, defaultextension=".log", parent=root
    )
    return path or None


def _osascript_ask_save_path(initial_name: str) -> str | None:
    script = (
        f'POSIX path of (choose file name with prompt "Save Argus logs" '
        f'default name "{_applescript_escape(initial_name)}")'
    )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    path = result.stdout.strip()
    return path or None


def _copy_to_clipboard(text: str, root) -> bool:
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.run(["clip"], input=text.encode(), check=True)
            return True
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
            return True
    except (OSError, subprocess.CalledProcessError):
        pass
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        return True
    except Exception:
        return False


# -- TrayApp --------------------------------------------------------------
class TrayApp:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.log_file = None
        self.config = load_config()
        self._tk_root = None

        base = _make_icon(256)
        self.icon_idle    = base.copy()
        self.icon_running = _badge(base, "#16a34a")
        self.icon_error   = _badge(base, "#dc2626")

        self.icon = pystray.Icon(
            "argus",
            icon=self.icon_idle,
            title="Argus (stopped)",
            menu=pystray.Menu(
                pystray.MenuItem(f"Argus v{__version__}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start server",   self.start,   enabled=lambda _: not self.is_running()),
                pystray.MenuItem("Stop server",    self.stop,    enabled=lambda _: self.is_running()),
                pystray.MenuItem("Restart server", self.restart, enabled=lambda _: self.is_running()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open in browser",       self.open_browser, enabled=lambda _: self.is_running()),
                pystray.MenuItem("Copy URL to clipboard", self.copy_url,     enabled=lambda _: self.is_running()),
                pystray.MenuItem("Save logs...",           self.save_logs),
                pystray.MenuItem("Open data folder",       self.open_data_folder),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: f"Port: {self.config['port']}", self.change_port),
                pystray.MenuItem(lambda _: f"Host: {self.config['host']}", self.change_host),
                pystray.MenuItem(
                    lambda _: "Data folder: " + (self.config["data_path"] or "default"),
                    self.change_data_path,
                ),
                pystray.MenuItem(
                    lambda _: "Models folder: " + (self.config["models_path"] or "default"),
                    self.change_models_path,
                ),
                pystray.MenuItem(
                    lambda _: "DB path: " + (self.config["db_path"] or "default"),
                    self.change_db_path,
                ),
                pystray.MenuItem(
                    "Launch at startup",
                    self.toggle_launch_at_login,
                    checked=lambda _: self.config["launch_at_login"],
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self.quit),
            ),
        )

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _run_on_main_thread(self, func):
        if self._tk_root is None:
            return func()
        result: queue.Queue = queue.Queue(maxsize=1)
        def wrapper():
            try:
                result.put((True, func()))
            except Exception as exc:  # noqa: BLE001
                result.put((False, exc))
        self._tk_root.after(0, wrapper)
        ok, value = result.get()
        if not ok:
            raise value
        return value

    def _set_status(self, image: Image.Image, title: str) -> None:
        self.icon.icon = image
        self.icon.title = title
        self.icon.update_menu()

    def _notify(self, message: str) -> None:
        if platform.system() == "Darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{_applescript_escape(message)}" with title "Argus"'],
                capture_output=True,
            )
            return
        try:
            self.icon.notify(message, "Argus")
        except NotImplementedError:
            pass

    def start(self, _icon=None, _item=None) -> None:
        if self.is_running():
            return
        port = self.config["port"]
        host = self.config["host"]

        if _port_is_occupied(host, port):
            if not _looks_like_our_own_server(host, port):
                self._set_status(self.icon_error, "Argus (port in use)")
                self._notify(f"{host}:{port} is already in use by another program -- pick a different port.")
                return
            proceed = self._run_on_main_thread(lambda: _ask_yes_no(
                self._tk_root,
                f"An Argus server is already running on {host}:{port} -- left over from a previous "
                f"session that didn't exit cleanly. Stop it and start fresh?",
            ))
            if not proceed:
                return
            pid = _find_listening_pid(port)
            if pid is not None:
                _kill_pid(pid, force=False)
                time.sleep(1.5)
                if _port_is_occupied(host, port):
                    _kill_pid(pid, force=True)
                    time.sleep(0.5)
            if _port_is_occupied(host, port):
                self._set_status(self.icon_error, "Argus (couldn't free the port)")
                self._notify(f"Couldn't stop the leftover server on {host}:{port} -- stop it manually, then try again.")
                return

        env = os.environ.copy()
        if FROZEN:
            # Point the server subprocess at the per-user data directory (overridable via config)
            env["DATA_PATH"]   = self.config["data_path"]   or str(_DATA_ROOT / "data")
            env["MODELS_PATH"] = self.config["models_path"] or str(_DATA_ROOT / "models")
            env["LOG_PATH"]    = str(_DATA_ROOT / "logs")
        else:
            env["PYTHONPATH"] = str(REPO_ROOT)
            if self.config["data_path"]:
                env["DATA_PATH"] = self.config["data_path"]
            if self.config["models_path"]:
                env["MODELS_PATH"] = self.config["models_path"]
        if self.config["db_path"]:
            env["DB_PATH"] = self.config["db_path"]

        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = open(LOG_PATH, "a", buffering=1)
        self.log_file.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} -- starting on {host}:{port} ===\n")

        if FROZEN:
            # Re-invoke this same executable with --run-server; it recognises
            # the flag and acts as the uvicorn process instead of the tray.
            cmd = [sys.executable, "--run-server", "--host", host, "--port", str(port)]
        else:
            cmd = [sys.executable, "-m", "app", "--host", host, "--port", str(port)]

        self.proc = subprocess.Popen(
            cmd,
            cwd=BASE_DIR if FROZEN else REPO_ROOT,
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )
        self._set_status(self.icon_running, f"Argus (running on {host}:{port})")
        self._notify(f"Argus running at http://{host}:{port}")

    def stop(self, _icon=None, _item=None) -> None:
        if not self.is_running():
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
        self._set_status(self.icon_idle, "Argus (stopped)")

    def restart(self, _icon=None, _item=None) -> None:
        if not self.is_running():
            return
        self.stop()
        self.start()

    def open_browser(self, _icon=None, _item=None) -> None:
        if self.is_running():
            webbrowser.open(f"http://{self.config['host']}:{self.config['port']}")

    def copy_url(self, _icon=None, _item=None) -> None:
        if not self.is_running():
            return
        url = f"http://{self.config['host']}:{self.config['port']}"
        if self._run_on_main_thread(lambda: _copy_to_clipboard(url, self._tk_root)):
            self._notify(f"Copied {url}")
        else:
            self._notify(f"Couldn't copy automatically -- the URL is {url}")

    def save_logs(self, _icon=None, _item=None) -> None:
        if not LOG_PATH.exists():
            self._notify("No log file found -- start the server first.")
            return
        import shutil
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self._run_on_main_thread(lambda: _ask_save_path(self._tk_root, f"argus-{ts}.log"))
        if not dest:
            return
        try:
            shutil.copy2(LOG_PATH, dest)
            self._notify(f"Logs saved to {Path(dest).name}")
        except OSError as exc:
            self._notify(f"Couldn't save logs: {exc}")

    def open_data_folder(self, _icon=None, _item=None) -> None:
        if self.config.get("data_path"):
            folder = Path(self.config["data_path"])
        elif FROZEN:
            folder = _DATA_ROOT / "data"
        else:
            folder = REPO_ROOT / "data"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if platform.system() == "Windows":
                os.startfile(folder)  # noqa: S606
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(folder)], check=True)
            else:
                subprocess.run(["xdg-open", str(folder)], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            self._notify(f"Couldn't open {folder}: {exc}")

    def _apply_setting_change(self) -> None:
        save_config(self.config)
        self.icon.update_menu()
        if self.is_running():
            self.restart()

    def change_port(self, _icon=None, _item=None) -> None:
        value = self._run_on_main_thread(lambda: _ask_string(self._tk_root, "Port number:", str(self.config["port"])))
        if value is None:
            return
        try:
            port = int(value)
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            self._notify(f"{value!r} isn't a valid port (1-65535) -- not changed.")
            return
        self.config["port"] = port
        self._apply_setting_change()

    def change_host(self, _icon=None, _item=None) -> None:
        value = self._run_on_main_thread(lambda: _ask_string(
            self._tk_root,
            "Host/IP (127.0.0.1 = this machine only, 0.0.0.0 = LAN):",
            self.config["host"],
        ))
        if not value:
            return
        self.config["host"] = value.strip()
        self._apply_setting_change()

    def change_data_path(self, _icon=None, _item=None) -> None:
        path = self._run_on_main_thread(lambda: _ask_directory(self._tk_root, "Choose Argus data folder"))
        if path is None:
            return
        self.config["data_path"] = path.rstrip("/\\")
        self._apply_setting_change()

    def change_models_path(self, _icon=None, _item=None) -> None:
        path = self._run_on_main_thread(lambda: _ask_directory(self._tk_root, "Choose Argus models folder"))
        if path is None:
            return
        self.config["models_path"] = path.rstrip("/\\")
        self._apply_setting_change()

    def change_db_path(self, _icon=None, _item=None) -> None:
        path = self._run_on_main_thread(lambda: _ask_open_file(self._tk_root, "Choose Argus database file"))
        if path is None:
            return
        self.config["db_path"] = path
        self._apply_setting_change()

    def toggle_launch_at_login(self, _icon=None, _item=None) -> None:
        new_value = not self.config["launch_at_login"]
        try:
            _set_launch_at_login(new_value)
        except OSError as exc:
            self._notify(f"Couldn't change login-launch setting: {exc}")
            return
        self.config["launch_at_login"] = new_value
        save_config(self.config)
        self.icon.update_menu()

    def quit(self, _icon=None, _item=None) -> None:
        self.stop()
        self.icon.stop()
        if self._tk_root is not None:
            self._tk_root.after(0, self._tk_root.quit)

    def run(self) -> None:
        if platform.system() == "Darwin":
            self.icon.run()
            return
        import tkinter as tk
        self._tk_root = tk.Tk()
        self._tk_root.withdraw()
        self.icon.run_detached()
        self._tk_root.mainloop()


def _run_server_entrypoint() -> None:
    """Re-entry point for a frozen build's server subprocess (see start() above).
    Runs as a second process; the tray icon is the first. Never reached from source."""
    if FROZEN:
        os.chdir(BASE_DIR)
    args = sys.argv[2:]
    host = "127.0.0.1"
    port = 8100
    if "--host" in args:
        host = args[args.index("--host") + 1]
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    # Delegate to app/__main__.py's own startup (env loading, CUDA preload,
    # warning suppression, argparse) -- avoids duplicating that logic here.
    sys.argv = ["app", "--host", host, "--port", str(port)]
    from app.__main__ import main
    main()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run-server":
        _run_server_entrypoint()
        sys.exit(0)
    if not _acquire_single_instance_lock():
        print("Argus tray is already running -- check your system tray.")
        sys.exit(0)
    try:
        TrayApp().run()
    finally:
        _release_single_instance_lock()
