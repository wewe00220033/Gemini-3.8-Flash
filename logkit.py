# -*- coding: utf-8 -*-
"""
logkit — سجل «كاميرات المراقبة» (09 §2–3):
  - `RedactFilter`: يحجب مفاتيح Gemini (AIza…) وتوكن تليجرام وتوكن الويب قبل أي كتابة — على مستوى logging
    فيحمي كل القنوات (stdout + الملف) مرة واحدة.
  - `DailyFileHandler`: `logs/app_YYYY-MM-DD.log` — ملف جديد كل يوم (UTC) أو عند LOG_FILE_MAX_MB؛ القديم يُضغط `.gz`.
  - `enforce_retention()`: حذف ما أقدم من LOG_RETENTION_DAYS ثم الأقدم أولاً حتى السقف LOG_TOTAL_MAX_MB.
  - `tail(n, errors_only)` / `read_day(date)`: لأمر /logs — الأسطر تُحجب مرة ثانية عند القراءة (دفاع في العمق).
stdout يبقى كما هو (لوحة Pterodactyl تقرأه). كل الدوال آمنة: فشل القرص لا يوقف البوت.
"""
import gzip
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

from config import Config

_GEMINI_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")
_TG_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")
_BEARER_RE = re.compile(r"(?i)(x-goog-api-key|authorization|api[_-]?key|token)([\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9_\-\.]{16,})")


def redact(text):
    """يحجب الأسرار في نص. يُبقي أول 4 أحرف من مفتاح Gemini للتمييز (AIza…****)."""
    if not text:
        return text
    t = _GEMINI_RE.sub("AIza…****", str(text))
    t = _TG_RE.sub("****:****", t)
    wt = getattr(Config, "WEB_ACCESS_TOKEN", "") or ""
    if len(wt) >= 8:
        t = t.replace(wt, "****")
    t = _BEARER_RE.sub(lambda m: m.group(1) + m.group(2) + "****", t)
    return t


class RedactFilter(logging.Filter):
    """فلتر logging: يحجب الرسالة ووسائطها قبل التنسيق."""

    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: (redact(v) if isinstance(v, str) else v) for k, v in record.args.items()}
                else:
                    record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        except Exception:
            pass
        return True


def _utc_day(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(), tz=timezone.utc).strftime("%Y-%m-%d")


def logs_dir():
    return Config.LOGS_DIR


def day_path(day=None):
    return os.path.join(logs_dir(), f"app_{day or _utc_day()}.log")


class DailyFileHandler(logging.Handler):
    """ملف يومي (UTC) + تدوير بالحجم داخل اليوم (app_YYYY-MM-DD.1.log.gz …) + ضغط القديم."""

    def __init__(self, level=logging.NOTSET):
        super().__init__(level)
        self._lock2 = threading.Lock()
        self._day = None
        self._fh = None
        self._bytes = 0

    def _open(self, day):
        os.makedirs(logs_dir(), exist_ok=True)
        path = day_path(day)
        self._fh = open(path, "a", encoding="utf-8")
        self._bytes = os.path.getsize(path) if os.path.exists(path) else 0
        self._day = day

    def _close(self):
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
        self._fh = None

    @staticmethod
    def _gzip_old(path):
        """يضغط ملفاً مكتملاً إلى .gz ويحذف الأصل (فشل → يبقى الأصل)."""
        try:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                return
            with open(path, "rb") as src, gzip.open(path + ".gz", "wb") as dst:
                dst.write(src.read())
            os.remove(path)
        except Exception:
            pass

    def _rotate_size(self):
        """داخل اليوم: app_D.log → app_D.<n>.log.gz ثم ملف جديد."""
        self._close()
        base = day_path(self._day)
        n = 1
        while os.path.exists(f"{base[:-4]}.{n}.log.gz"):
            n += 1
        try:
            os.replace(base, f"{base[:-4]}.{n}.log")
            self._gzip_old(f"{base[:-4]}.{n}.log")
        except OSError:
            pass
        self._open(self._day)

    def emit(self, record):
        try:
            line = self.format(record) + "\n"
            day = _utc_day(record.created)
            with self._lock2:
                if self._fh is None:
                    self._open(day)
                elif day != self._day:
                    old = self._day
                    self._close()
                    self._gzip_old(day_path(old))
                    self._open(day)
                    enforce_retention()
                self._fh.write(line)
                self._fh.flush()
                self._bytes += len(line.encode("utf-8"))
                if self._bytes > Config.LOG_FILE_MAX_MB * 1024 * 1024:
                    self._rotate_size()
        except Exception:
            pass   # السجل لا يكسر البوت أبداً

    def close(self):
        with self._lock2:
            self._close()
        super().close()


_LOG_NAME_RE = re.compile(r"^app_(\d{4}-\d{2}-\d{2})(?:\.\d+)?\.log(?:\.gz)?$")


def list_log_files():
    """[(path, day, size)] مرتبة من الأقدم للأحدث."""
    out = []
    try:
        for fn in os.listdir(logs_dir()):
            m = _LOG_NAME_RE.match(fn)
            if m:
                p = os.path.join(logs_dir(), fn)
                try:
                    out.append((p, m.group(1), os.path.getsize(p)))
                except OSError:
                    pass
    except OSError:
        return []
    out.sort(key=lambda x: (x[1], x[0]))
    return out


def total_size_bytes():
    return sum(sz for _p, _d, sz in list_log_files())


def enforce_retention(now=None):
    """09 §1/§7: حذف > LOG_RETENTION_DAYS، ثم الأقدم أولاً حتى الحجم ≤ LOG_TOTAL_MAX_MB. يرجع عدد المحذوف."""
    now = now if now is not None else time.time()
    removed = 0
    files = list_log_files()
    today = _utc_day(now)
    cutoff = _utc_day(now - Config.LOG_RETENTION_DAYS * 86400)
    keep = []
    for p, day, sz in files:
        if day < cutoff and day != today:
            try:
                os.remove(p)
                removed += 1
                continue
            except OSError:
                pass
        keep.append((p, day, sz))
    limit = Config.LOG_TOTAL_MAX_MB * 1024 * 1024
    total = sum(sz for _p, _d, sz in keep)
    for p, day, sz in keep:
        if total <= limit:
            break
        if day == today:
            continue
        try:
            os.remove(p)
            removed += 1
            total -= sz
        except OSError:
            pass
    return removed


def _read_lines(path):
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


_ERROR_MARK_RE = re.compile(r"\| (ERROR|WARNING|CRITICAL) \||❌|⚠️|Traceback|Error\b")


def tail(n=None, errors_only=False, days_back=3):
    """آخر n سطر (محجوبة) من ملفات الأيام الأخيرة؛ errors_only → أسطر الأخطاء/التحذيرات فقط."""
    n = n or (Config.LOG_ERRORS_TAIL if errors_only else Config.LOG_TAIL_DEFAULT)
    files = list_log_files()[-max(1, days_back * 3):]
    lines = []
    for p, _d, _s in files:
        lines.extend(_read_lines(p))
    if errors_only:
        lines = [ln for ln in lines if _ERROR_MARK_RE.search(ln)]
    return [redact(ln) for ln in lines[-n:]]


def read_day(day):
    """كل أسطر يوم محدد (YYYY-MM-DD) محجوبة — لأمر /logs <date> كملف. [] لو لا شيء."""
    lines = []
    for p, d, _s in list_log_files():
        if d == day:
            lines.extend(_read_lines(p))
    return [redact(ln) for ln in lines]


def install(root=None, level=None):
    """يركّب فلتر الحجب على الجذر + ملف يومي (لو LOG_TO_FILE). يرجع handler الملف أو None. آمن للتكرار."""
    root = root or logging.getLogger()
    flt = RedactFilter()
    if not any(isinstance(f, RedactFilter) for f in root.filters):
        root.addFilter(flt)
    for h in root.handlers:
        if not any(isinstance(f, RedactFilter) for f in h.filters):
            h.addFilter(flt)
    if not Config.LOG_TO_FILE:
        return None
    for h in root.handlers:
        if isinstance(h, DailyFileHandler):
            return h
    try:
        fh = DailyFileHandler()
        fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S"))
        fh.addFilter(flt)
        if level is not None:
            fh.setLevel(level)
        root.addHandler(fh)
        enforce_retention()
        return fh
    except Exception as e:
        print(f"⚠️ logkit: تعذر فتح ملف السجل ({type(e).__name__}: {e}) — stdout فقط")
        return None
