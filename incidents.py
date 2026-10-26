# -*- coding: utf-8 -*-
"""
incidents — تقرير الحادث التلقائي (12 §3.3 + 09 §4):
  متى: stall/تدخل الـ watchdog (L1/L3/L3b/L4)، إعادة تشغيل العملية (restart_reason.json عند الإقلاع)،
       ≥ INCIDENT_FAIL_STREAK فشل متتالٍ في الطابور.
  ماذا: `incidents/INC-YYYYMMDD-HHMM[-n].md` يحوي: الملخص، الخط الزمني (آخر المهام من jobs.ndjson بأحداثها)،
       حالة القواطع والمفاتيح والطابور، مستوى الـ watchdog، آخر أسطر الأخطاء (محجوبة الأسرار).
  ثم: ملخص سطرين للمالك مع تلميح /trace و/incidents (عبر notify_fn المحقونة — لا شبكة هنا).
  تهدئة: لا تقريرين لنفس النوع خلال INCIDENT_COOLDOWN_S. الأقدم يُحذف بعد INCIDENTS_KEEP.
كل شيء آمن: أي فشل يُطبع ولا يوقف البوت.
"""
import os
import threading
import time
from datetime import datetime

from config import Config

_lock = threading.Lock()
_last_by_kind = {}          # kind -> wall ts آخر تقرير
_fail_streak = {"n": 0}
_notify_fn = None           # يُضبط من main/telegram_bot: fn(text)

KIND_LABEL = {
    "stall": "تعليق الاستقبال (watchdog)",
    "restart": "إعادة تشغيل العملية",
    "fail_streak": "فشل متتالٍ في الطابور",
    "manual": "تقرير يدوي",
}


def set_notifier(fn):
    global _notify_fn
    _notify_fn = fn


def incidents_dir():
    return Config.INCIDENTS_DIR


def list_recent(n=10):
    """مسارات آخر n تقرير (الأحدث أولاً)."""
    try:
        files = [os.path.join(incidents_dir(), f) for f in os.listdir(incidents_dir()) if f.startswith("INC-") and f.endswith(".md")]
    except OSError:
        return []
    files.sort(reverse=True)
    return files[:n]


def _prune():
    files = list_recent(10_000)
    for p in files[Config.INCIDENTS_KEEP:]:
        try:
            os.remove(p)
        except OSError:
            pass


def _unique_path(now):
    base = os.path.join(incidents_dir(), "INC-" + datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M"))
    path = base + ".md"
    n = 2
    while os.path.exists(path):
        path = f"{base}-{n}.md"
        n += 1
    return path


def _safe(fn, default=""):
    try:
        return fn()
    except Exception as e:
        return f"(تعذر: {type(e).__name__}: {e})" if default == "" else default


def _timeline_md(max_jobs=8):
    import jobs
    state = jobs.load_state()
    recent = sorted(state.values(), key=lambda j: j.get("order", 0))[-max_jobs:]
    if not recent:
        return "_لا مهام مسجلة._"
    out = []
    for j in recent:
        ts = datetime.fromtimestamp(float(j.get("first_ts") or 0)).strftime("%H:%M:%S")
        dur = float(j.get("last_ts") or 0) - float(j.get("first_ts") or 0)
        sess = f" · 📂 {j['session_name']}" if j.get("session_name") else ""
        err = f" — {str(j.get('error'))[:120]}" if j.get("error") else ""
        out.append(f"- `{j['job_id']}` {ts} → **{j.get('state')}** ({dur:.0f}ث) أحداث: {' → '.join(j.get('events', []))}{sess}{err}")
    return "\n".join(out)


def _circuits_md():
    from model_state import model_circuit
    rows = []
    for x in model_circuit.snapshot(Config.MODELS):
        rows.append(f"- {x['model']}: {'⛔ ' + str(x['reason']) + ' حتى ' + str(x['until']) if x['paused'] else '✅ مفتوح'} (ضربات {x.get('strikes', 0)})")
    return "\n".join(rows) or "_—_"


def _keys_md():
    import key_manager
    led = key_manager.quota_ledger()
    keys = key_manager.data.get("keys", [])
    now = time.time()
    avail = sum(1 for k in keys if any(key_manager.is_model_available(k, m, now) for m in Config.MODELS))
    return f"- مفاتيح: {len(keys)} (متاح {avail}) · متبقي اليوم ~{led.get('remaining')} · وضع {led.get('mode')}"


def _queue_md(queue):
    if queue is None:
        return "_لا طابور._"
    s = queue.snapshot()
    return f"- حجم {s['size']} · جارٍ {s['in_flight']} · إحصاء {s['stats']}"


def _watchdog_md(watchdog):
    if watchdog is None:
        return "_—_"
    s = watchdog.snapshot()
    lines = [f"- المستوى: {s.get('level') or 'طبيعي'} · L1 إجمالاً {s.get('restarts')} · في النافذة {s.get('l1_in_window')} · L4 مطلوب: {s.get('l4_requested')}"]
    for ev in s.get("recent", []):
        lines.append(f"  - قبل {ev['age_s']}ث [{ev['level']}] {ev['detail']}")
    return "\n".join(lines)


def _errors_md(n=30):
    import logkit
    lines = logkit.tail(n=n, errors_only=True)
    if not lines:
        return "_لا أسطر خطأ في السجل (أو LOG_TO_FILE=0)._"
    return "```\n" + "\n".join(lines) + "\n```"


def write_report(kind, detail, queue=None, watchdog=None, now=None, force=False):
    """
    يكتب التقرير ويُخطر المالك. يرجع المسار أو None (تهدئة/فشل).
    """
    now = now or time.time()
    with _lock:
        last = _last_by_kind.get(kind)
        if not force and last is not None and now - last < Config.INCIDENT_COOLDOWN_S:
            return None
        _last_by_kind[kind] = now
    try:
        os.makedirs(incidents_dir(), exist_ok=True)
        path = _unique_path(now)
        gen = os.environ.get("WORKER_GENERATION")
        md = [
            f"# 🚨 حادث — {KIND_LABEL.get(kind, kind)}",
            "",
            f"- **الوقت:** {datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')}" + (f" · العامل #{gen}" if gen else ""),
            f"- **النوع:** `{kind}`",
            f"- **التفصيل:** {detail}",
            "",
            "## الخط الزمني (آخر المهام من jobs.ndjson)",
            _safe(_timeline_md),
            "",
            "## القواطع",
            _safe(_circuits_md),
            "",
            "## المفاتيح والحصة",
            _safe(_keys_md),
            "",
            "## الطابور",
            _safe(lambda: _queue_md(queue)),
            "",
            "## الـ watchdog",
            _safe(lambda: _watchdog_md(watchdog)),
            "",
            "## آخر أسطر الأخطاء (محجوبة)",
            _safe(_errors_md),
            "",
            "_استخدم `/trace <job_id>` لرحلة فويس بعينه، و`/doctor` للفحص الحي._",
            "",
        ]
        import store
        store.atomic_write_text(path, "\n".join(md), mode=0o600)
        _prune()
    except Exception as e:
        print(f"⚠️ incidents: تعذر كتابة التقرير ({type(e).__name__}: {e})")
        return None
    print(f"🚨 تقرير حادث: {os.path.basename(path)} — {KIND_LABEL.get(kind, kind)}: {detail}")
    if _notify_fn:
        try:
            _notify_fn(f"🚨 حادث: {KIND_LABEL.get(kind, kind)} — {str(detail)[:160]}\n"
                       f"📄 {os.path.basename(path)} · /incidents للتقرير · /doctor للفحص · /trace <job_id> لفويس بعينه")
        except Exception as e:
            print(f"⚠️ incidents: تعذر إخطار المالك ({e})")
    return path


# ---------------------------------------------------------------- المشغّلات
def on_watchdog_event(level, detail, queue=None, watchdog=None):
    """يُنادى من الـ watchdog عند أي تصعيد (L1/L3/L3b/L4). L1 واحد → تقرير stall (بتهدئة)."""
    return write_report("stall", f"{level}: {detail}", queue=queue, watchdog=watchdog)


def on_boot_restart(reason_data, queue=None, watchdog=None):
    """يُنادى عند الإقلاع لو وُجد restart_reason.json (L4/crash/frozen)."""
    if not reason_data:
        return None
    r = reason_data.get("reason")
    return write_report("restart", f"{r}: {reason_data.get('detail') or ''}".strip(": "), queue=queue, watchdog=watchdog, force=True)


def on_job_result(ok, error=None, queue=None, watchdog=None):
    """يُنادى بعد كل مهمة: نجاح يصفّر السلسلة؛ فشل متتالٍ ≥ INCIDENT_FAIL_STREAK → تقرير مرة واحدة لكل سلسلة."""
    with _lock:
        if ok:
            _fail_streak["n"] = 0
            return None
        _fail_streak["n"] += 1
        n = _fail_streak["n"]
    if n == Config.INCIDENT_FAIL_STREAK:
        return write_report("fail_streak", f"{n} فويسات فشلت متتالية — آخر خطأ: {str(error or '')[:160]}", queue=queue, watchdog=watchdog)
    return None


def fail_streak():
    return _fail_streak["n"]


def reset_state():
    """للاختبارات."""
    with _lock:
        _last_by_kind.clear()
        _fail_streak["n"] = 0


def latest_text(max_chars=3500):
    """نص آخر تقرير لأمر /incidents (مقتطع)."""
    files = list_recent(1)
    if not files:
        return None
    try:
        with open(files[0], "r", encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return None
    return txt[:max_chars] + ("\n…" if len(txt) > max_chars else "")
