"""Daily rotating log files written to logs/ at the repo root.

Two files per UTC day:
  logs/argus-YYYY-MM-DD.log      — application logs (all levels, one line per record)
  logs/activity-YYYY-MM-DD.log   — activity events (JSON lines)

install(log_dir) attaches a DailyFileHandler to the root logger and wires
activity_buffer to write through to disk.  purge_old() deletes files older
than system.log_retention_days (default 5).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LOG_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+"
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(\S+)\s(.*)$"
)

_app_handler: "_DailyFileHandler | None" = None
_activity_lock = threading.Lock()
_activity_date: str = ""
_activity_file = None


class _SingleLineFormatter(logging.Formatter):
    """Replace embedded newlines so each log entry occupies exactly one line."""

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        return s.replace("\n", " | ")


class _DailyFileHandler(logging.Handler):
    """Appends to logs/{prefix}-YYYY-MM-DD.log, rolling over at UTC midnight."""

    def __init__(self, log_dir: Path, prefix: str) -> None:
        super().__init__()
        self._log_dir = log_dir
        self._prefix = prefix
        self._current_date = ""
        self._file = None
        fmt = _SingleLineFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        fmt.converter = time.gmtime
        self.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            date_str = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d")
            self.acquire()
            try:
                if date_str != self._current_date or self._file is None:
                    if self._file:
                        self._file.close()
                    self._current_date = date_str
                    self._file = open(
                        self._log_dir / f"{self._prefix}-{date_str}.log",
                        "a", encoding="utf-8",
                    )
                self._file.write(self.format(record) + "\n")
                self._file.flush()
            finally:
                self.release()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        self.acquire()
        try:
            if self._file:
                self._file.close()
                self._file = None
        finally:
            self.release()
        super().close()


def install(log_dir: Path) -> None:
    """Attach file handlers to the root logger and wire the activity buffer.

    Idempotent — safe to call multiple times (e.g. during test runs).
    """
    global _app_handler
    log_dir.mkdir(parents=True, exist_ok=True)

    if _app_handler is not None:
        return

    _app_handler = _DailyFileHandler(log_dir, "argus")
    _app_handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.addHandler(_app_handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        if _app_handler not in lg.handlers:
            lg.addHandler(_app_handler)

    import app.core.activity_buffer as _ab
    _ab._file_emit_fn = _write_activity


def _write_activity(entry: dict) -> None:
    global _activity_date, _activity_file
    from app.core.paths import logs_dir
    date_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    log_dir = logs_dir()
    file_entry = {
        "time": datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "category": entry.get("category", ""),
        "message": entry.get("message", ""),
    }
    with _activity_lock:
        if date_str != _activity_date or _activity_file is None:
            if _activity_file:
                _activity_file.close()
            _activity_date = date_str
            _activity_file = open(
                log_dir / f"activity-{date_str}.log", "a", encoding="utf-8"
            )
        _activity_file.write(json.dumps(file_entry) + "\n")
        _activity_file.flush()


def purge_old(retention_days: int, log_dir: Path) -> None:
    """Delete log files older than retention_days UTC days."""
    if not log_dir.exists():
        return
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days)
    for f in log_dir.glob("*.log"):
        parts = f.stem.rsplit("-", 3)
        if len(parts) != 4:
            continue
        try:
            file_date = datetime.strptime("-".join(parts[1:]), "%Y-%m-%d")
            if file_date < cutoff:
                f.unlink(missing_ok=True)
        except ValueError:
            pass


def list_dates(log_dir: Path) -> list[dict]:
    """Return available log dates, newest first, with per-type metadata."""
    if not log_dir.exists():
        return []
    dates: dict[str, dict] = {}
    for f in log_dir.glob("*.log"):
        parts = f.stem.rsplit("-", 3)
        if len(parts) != 4:
            continue
        try:
            date_str = "-".join(parts[1:])
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        prefix = parts[0]
        if date_str not in dates:
            dates[date_str] = {"date": date_str, "has_app": False, "has_activity": False}
        if prefix == "argus":
            dates[date_str]["has_app"] = True
            dates[date_str]["app_size"] = f.stat().st_size
        elif prefix == "activity":
            dates[date_str]["has_activity"] = True
            dates[date_str]["activity_size"] = f.stat().st_size
    return sorted(dates.values(), key=lambda d: d["date"], reverse=True)


def read_app_log(
    date: str,
    log_dir: Path,
    offset: int,
    limit: int,
    level: str | None = None,
    q: str | None = None,
) -> dict:
    """Return a paginated slice of an app log file, parsed into structured records.

    level filters by log level; q is a space-delimited text filter (prefix with - to exclude).
    """
    path = log_dir / f"argus-{date}.log"
    if not path.exists():
        return {"total": 0, "offset": offset, "limit": limit, "lines": []}

    parsed: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            m = _LOG_RE.match(raw)
            if m:
                parsed.append({
                    "time": m.group(1),
                    "level": m.group(2),
                    "logger": m.group(3),
                    "message": m.group(4),
                })
            elif parsed:
                parsed[-1]["message"] += " | " + raw

    if level:
        parsed = [ln for ln in parsed if ln["level"] == level.upper()]

    if q:
        terms = q.strip().lower().split()
        def _matches(ln: dict) -> bool:
            hay = f"{ln['time']} {ln['level']} {ln['logger']} {ln['message']}".lower()
            for t in terms:
                if t.startswith("-"):
                    if len(t) > 1 and t[1:] in hay:
                        return False
                elif t not in hay:
                    return False
            return True
        parsed = [ln for ln in parsed if _matches(ln)]

    total = len(parsed)
    return {"total": total, "offset": offset, "limit": limit, "lines": parsed[offset: offset + limit]}


def read_activity_log(date: str, log_dir: Path, offset: int, limit: int) -> dict:
    """Return a paginated slice of an activity log file."""
    path = log_dir / f"activity-{date}.log"
    if not path.exists():
        return {"total": 0, "offset": offset, "limit": limit, "events": []}

    events: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError:
                pass

    total = len(events)
    return {"total": total, "offset": offset, "limit": limit, "events": events[offset: offset + limit]}


def delete_date(date: str, log_dir: Path) -> None:
    """Delete both log files for a given UTC date."""
    for prefix in ("argus", "activity"):
        p = log_dir / f"{prefix}-{date}.log"
        p.unlink(missing_ok=True)
