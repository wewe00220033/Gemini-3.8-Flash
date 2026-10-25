# -*- coding: utf-8 -*-
"""
preflight.py — تقرير الإقلاع بسطر واحد + معلومات النسخة (22 Z5/Z6، 17 المرحلة 0).

- version_info(): رقم النسخة من ملف VERSION + git sha إن وُجد (محلياً) وإلا بصمة قصيرة لملفات .py (على الاستضافة ZIP).
- run_preflight(): فحوص بلا شبكة (مفاتيح، توكن، DATA_DIR قابل للكتابة، ملفات حالة، قاطع الموديلات)
  ويعيد (ok, line, details). السطر يُطبع في اللوج ويُرسل على تليجرام عند الإقلاع إن NOTIFY_ON_START.
"""
import glob
import hashlib
import os
import subprocess
import time

import clock
import store
from config import Config

_STARTED_MONO = clock.monotonic()


def _read_version_file():
    p = os.path.join(Config.SCRIPT_DIR, "VERSION")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip() or "0"
    except OSError:
        return "0"


def _git_sha():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Config.SCRIPT_DIR,
            capture_output=True, text=True, timeout=3,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _py_fingerprint():
    """بصمة 7 أحرف لمحتوى ملفات .py في الجذر — تميّز نسخة ZIP مرفوعة بلا git."""
    h = hashlib.sha1()
    for p in sorted(glob.glob(os.path.join(Config.SCRIPT_DIR, "*.py"))):
        try:
            with open(p, "rb") as f:
                h.update(os.path.basename(p).encode() + f.read())
        except OSError:
            continue
    return h.hexdigest()[:7]


def version_info():
    sha = _git_sha()
    return {
        "version": _read_version_file(),
        "build": sha or f"zip-{_py_fingerprint()}",
        "source": "git" if sha else "fingerprint",
        "uptime_s": int(clock.monotonic() - _STARTED_MONO),
    }


def version_line():
    v = version_info()
    up = v["uptime_s"]
    h, m = divmod(up // 60, 60)
    return f"🏷️ v{v['version']} · {v['build']} · uptime {h}h{m:02d}m"


def run_preflight(key_manager, model_circuit=None, web_port=None, web_error=None, jobs_pending=None):
    """
    يعيد (ok, line, details). لا شبكة، لا رفع استثناء.
    details: dict لكل فحص {ok, text}.
    """
    details = {}

    # 1) المفاتيح
    try:
        now = time.time()
        keys = key_manager.data.get("keys", [])
        avail = sum(1 for k in keys if any(key_manager.is_model_available(k, m, now) for m in Config.MODELS))
        details["keys"] = {"ok": avail > 0, "text": f"keys {avail}/{len(keys)}"}
    except Exception as e:
        details["keys"] = {"ok": False, "text": f"keys ?({type(e).__name__})"}

    # 2) تليجرام مضبوط (التوكن + chat_id) — بلا نداء شبكي
    try:
        token, _, chat_id = key_manager.get_telegram_config()
        tg_ok = bool(token and (chat_id or "").strip())
        details["tg"] = {"ok": tg_ok, "text": "tg ok" if tg_ok else ("tg no-token" if not token else "tg no-chat")}
    except Exception as e:
        details["tg"] = {"ok": False, "text": f"tg ?({type(e).__name__})"}

    # 3) الويب
    if web_error:
        details["web"] = {"ok": False, "text": f"web :{web_port} FAIL"}
    else:
        details["web"] = {"ok": True, "text": f"web :{web_port}"}

    # 4) DATA_DIR
    ok, det = store.probe_writable(Config.DATA_DIR)
    details["data_dir"] = {"ok": ok, "text": "DATA_DIR rw" if ok else f"DATA_DIR RO ({det})"}

    # 5) مهام معلقة (يُمرَّر من الطابور لاحقاً؛ None = غير متاح بعد)
    if jobs_pending is not None:
        details["jobs"] = {"ok": True, "text": f"jobs pending {jobs_pending}"}

    # 6) القاطع
    if model_circuit is not None:
        try:
            snap = model_circuit.snapshot(Config.MODELS)
            paused = [m for m, st in snap.items() if isinstance(st, dict) and st.get("paused")]
            details["circuit"] = {"ok": not paused, "text": "circuit clear" if not paused else f"circuit paused {','.join(paused)}"}
        except Exception:
            details["circuit"] = {"ok": True, "text": "circuit ?"}

    all_ok = all(d["ok"] for d in details.values())
    parts = [("✅ " if d["ok"] else "❌ ") + d["text"] if not d["ok"] else d["text"] for d in details.values()]
    line = ("✅ " if all_ok else "⚠️ ") + " · ".join(parts) + " · " + version_line()
    return all_ok, line, details
