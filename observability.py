# -*- coding: utf-8 -*-
"""
observability — المرحلة 5 (09 §4 + 12 §3.1): /logs و/trace و/doctor.
  - `trace_text(job_id)`: رحلة الفويس من jobs.ndjson: استلام → تحميل (محاولات) → محاولات Gemini (موديل/مفتاح/نتيجة/ms)
    → تفريغ → إرسال/فشل/تأجيل، بالأزمنة النسبية + الجلسة. لا شبكة.
  - `doctor_report()`: فحص محلي بلا AI وبلا Gemini: نبض الـ poller، الطابور، القواطع، الحصة، المفاتيح، القرص/
    .persist_probe، أحجام logs/sessions/incidents/jobs، Python/وقت التشغيل/إعادات التشغيل. كل فحص معزول —
    فشل واحد لا يوقف التقرير (12 §5 بند 2). يرجع (lines, worst) حيث worst ∈ ok|warn|fail.
  - `logs_text(arg)`: آخر N سطر / errors (محجوبة).
"""
import os
import platform
import shutil
import sys
import time
from datetime import datetime

from config import Config

OK, WARN, FAIL = "ok", "warn", "fail"
_ICON = {OK: "✅", WARN: "⚠️", FAIL: "❌"}
_RANK = {OK: 0, WARN: 1, FAIL: 2}


# ======================================================================
# /trace
# ======================================================================
_EVENT_LABEL = {
    "received": "📥 استلام", "downloaded": "⬇️ تحميل", "download_fail": "📡 فشل تحميل", "attempt": "🤖 محاولة Gemini",
    "transcribed": "📝 تفريغ", "sent": "📤 إرسال", "failed": "❌ فشل", "deferred": "⏳ تأجيل",
    "skipped": "⏭ تخطٍّ", "resumed": "🔁 استئناف",
}


def _fmt_ts(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except Exception:
        return "?"


def trace_text(job_id):
    """نص /trace. None لو المهمة غير موجودة."""
    import jobs
    rows = jobs.events_of(job_id)
    if not rows:
        return None
    t0 = float(rows[0].get("ts") or 0)
    jid = rows[0].get("job_id")
    lines = [f"🔎 تتبع الفويس [{jid}]"]
    meta = {}
    gem_attempts = dl_fails = 0
    for r in rows:
        meta.update({k: v for k, v in r.items() if k in ("source", "label", "size", "mime", "session_id", "session_name", "model", "key", "chat_id", "message_id")})
        ev = r.get("event")
        rel = float(r.get("ts") or t0) - t0
        head = f"  +{rel:6.1f}ث  {_fmt_ts(r.get('ts'))}  {_EVENT_LABEL.get(ev, ev)}"
        det = ""
        if ev == "received":
            det = f"{r.get('label', '')} {r.get('source', '')} {_fmt_size(r.get('size'))}".strip()
        elif ev == "downloaded":
            det = _fmt_size(r.get("bytes"))
        elif ev == "download_fail":
            dl_fails += 1
            det = f"#{r.get('attempt')} {r.get('error', '')}"
        elif ev == "attempt":
            gem_attempts += 1
            det = f"{_short(r.get('att_model'))} [{r.get('att_key')}] {r.get('att_outcome')} {r.get('att_ms', 0)}ms"
            if r.get("att_detail"):
                det += f" — {r['att_detail']}"
        elif ev == "transcribed":
            det = f"{_short(r.get('model'))} [{r.get('key')}] {r.get('elapsed')}ث"
        elif ev == "sent":
            det = f"{'مكرر من ' + str(r.get('dedup_of')) if r.get('dedup_of') else ''} {r.get('mode') or ''} {'+منظم' if r.get('polished') else ''}".strip()
        elif ev == "failed":
            det = str(r.get("error", ""))[:160]
        elif ev == "deferred":
            det = f"{r.get('wait_s')}ث ({r.get('kind')}) — {str(r.get('reason', ''))[:100]}"
        lines.append(f"{head}: {det}" if det else head)
    total = float(rows[-1].get("ts") or t0) - t0
    sess = f" · 📂 «{meta['session_name']}» [{meta.get('session_id')}]" if meta.get("session_name") else " · بلا جلسة"
    lines.append(f"── الحالة النهائية: {rows[-1].get('event')} · الإجمالي {total:.1f}ث · محاولات التحميل الفاشلة {dl_fails} · محاولات Gemini {gem_attempts}{sess}")
    return "\n".join(lines)


def _short(m):
    return str(m or "?").replace("gemini-", "")


def _fmt_size(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{n / 1024:.0f}KB"


# ======================================================================
# /logs
# ======================================================================
def logs_text(arg=None):
    """arg: None → آخر LOG_TAIL_DEFAULT · 'errors' → آخر LOG_ERRORS_TAIL خطأ · رقم → آخر N. محجوب."""
    import logkit
    errors = str(arg or "").lower() in ("errors", "error", "err", "أخطاء")
    n = None
    if arg and str(arg).isdigit():
        n = max(1, min(200, int(arg)))
    lines = logkit.tail(n=n, errors_only=errors)
    if not lines:
        return "📭 لا سجلات بعد (LOG_TO_FILE=0؟ أو لم يُكتب شيء اليوم)."
    title = f"🧾 آخر {len(lines)} {'سطر خطأ/تحذير' if errors else 'سطر'} (الأسرار محجوبة):"
    return title + "\n" + "\n".join(lines)


# ======================================================================
# /doctor — محلي بلا AI
# ======================================================================
def _check(fn):
    """ينفّذ فحصاً معزولاً: يرجع (status, text). استثناء → ⚠️ مع السبب — التقرير يكتمل دائماً."""
    try:
        return fn()
    except Exception as e:
        return WARN, f"تعذر الفحص ({type(e).__name__}: {e})"


def _dir_size(path):
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _mb(n):
    return f"{n / 1024 / 1024:.1f}MB"


def persist_probe_check():
    """09 §6: يكتب .persist_probe بوقت البدء؛ لو غاب والفهرس يقول جلسات موجودة → تحذير 'المجلد غير مستمر'."""
    path = Config.PERSIST_PROBE_FILE
    existed = os.path.exists(path)
    try:
        import sessions
        n_sessions = len(sessions.list_sessions(include_archived=True))
    except Exception:
        n_sessions = 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not existed:
            with open(path, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
    except OSError as e:
        return FAIL, f"تعذر كتابة .persist_probe ({e})"
    if not existed and n_sessions > 0:
        return WARN, f"يبدو أن DATA_DIR غير مستمر — .persist_probe غائب رغم وجود {n_sessions} جلسة"
    return OK, "DATA_DIR مستمر (.persist_probe موجود)" if existed else "DATA_DIR جديد (.persist_probe كُتب الآن)"


def doctor_report(bot_state=None, queue=None, watchdog=None, now=None):
    """
    يرجع (lines: [str], worst: ok|warn|fail). bot_state/queue/watchdog تُحقن (telegram_bot.STATE/QUEUE, RUNTIME watchdog)
    — تُقرأ تلقائياً لو لم تُمرَّر. لا نداء لـ Gemini إطلاقاً (12 §3.1: recent_attempts بدلاً منه).
    """
    now = now or time.time()
    if bot_state is None or queue is None:
        try:
            import telegram_bot
            bot_state = bot_state if bot_state is not None else telegram_bot.STATE
            queue = queue if queue is not None else telegram_bot.QUEUE
        except Exception:
            bot_state, queue = bot_state or {}, queue
    results = []

    def add(name, fn):
        st, txt = _check(fn)
        results.append((st, f"{_ICON[st]} {name}: {txt}"))

    # 1) نبض الاستقبال
    def _poller():
        import heartbeat
        if not bot_state or not bot_state.get("alive"):
            return WARN, "البوت غير نشط في هذه العملية (وضع ويب فقط؟)"
        lp = bot_state.get("last_poll_ok")
        age = int(now - lp) if lp else None
        beats = heartbeat.snapshot()
        parts = [f"آخر poll ناجح منذ {age}ث" if age is not None else "لا poll ناجح بعد"]
        for n_, b in beats.items():
            parts.append(f"{n_} نبض منذ {b.get('age_s')}ث")
        flags = []
        if bot_state.get("protect_mode"):
            flags.append("حماية")
        if bot_state.get("emergency_mode"):
            flags.append("طوارئ")
        txt = " · ".join(parts) + (f" · وضع: {'+'.join(flags)}" if flags else "")
        if age is None or age > 300:
            return FAIL, txt
        if age > 120 or flags:
            return WARN, txt
        return OK, txt
    add("الاستقبال", _poller)

    # 2) الطابور
    def _queue():
        if queue is None:
            return WARN, "لا طابور في هذه العملية"
        snap = queue.snapshot()
        oldest = max([i["waiting_s"] for i in snap["items"]] or [0])
        st = snap["stats"]
        txt = f"{snap['size']} منتظرة · جارٍ {snap['in_flight']} · أقدم انتظار {oldest}ث · منجز {st.get('done', 0)} · فشل {st.get('failed', 0)} · مؤجل {st.get('deferred', 0)}"
        if oldest > 900:
            return FAIL, txt
        if snap["size"] > 5 or oldest > 300:
            return WARN, txt
        return OK, txt
    add("الطابور", _queue)

    # 3) القواطع + آخر المحاولات (بدل ping)
    def _circuits():
        from model_state import model_circuit
        snap = model_circuit.snapshot(Config.MODELS)
        paused = [x for x in snap if x["paused"]]
        txt = " · ".join(f"{_short(x['model'])}: {'⛔ ' + str(x['reason']) + ' يفتح بعد ' + str(x['remaining_s']) + 'ث' if x['paused'] else 'مفتوح'}" for x in snap)
        recent = getattr(model_circuit, "recent_attempts", None)
        if callable(recent):
            try:
                r = recent()
                if r:
                    txt += f" · آخر {len(r)} محاولة مسجلة"
            except Exception:
                pass
        if len(paused) == len(snap):
            return FAIL, txt
        if paused:
            return WARN, txt
        return OK, txt
    add("قواطع الموديلات", _circuits)

    # 4) الحصة + المفاتيح
    def _keys():
        import key_manager
        km = key_manager
        keys = km.data.get("keys", [])
        if not keys:
            return FAIL, "لا مفاتيح Gemini"
        invalid = [k for k in keys if k.get("invalid") or k.get("status") == "invalid"]
        avail = sum(1 for k in keys if any(km.is_model_available(k, m, now) for m in Config.MODELS))
        led = km.quota_ledger()
        txt = f"{len(keys)} مفتاح · متاح الآن {avail} · غير صالح {len(invalid)} · متبقي اليوم ~{led.get('remaining')} ({led.get('mode')})"
        if avail == 0:
            return FAIL, txt
        if invalid or led.get("mode") == "saver":
            return WARN, txt
        return OK, txt
    add("المفاتيح والحصة", _keys)

    # 5) القرص
    def _disk():
        import store
        ok, det = store.probe_writable(Config.DATA_DIR)
        usage = shutil.disk_usage(Config.DATA_DIR)
        free_mb = usage.free / 1024 / 1024
        txt = f"{'قابل للكتابة' if ok else 'للقراءة فقط (' + det + ')'} · حر {free_mb:.0f}MB من {usage.total / 1024 / 1024 / 1024:.1f}GB"
        if not ok:
            return FAIL, txt
        if free_mb < Config.DISK_LOW_MB:
            return WARN, txt
        return OK, txt
    add("القرص", _disk)
    add("استمرارية البيانات", persist_probe_check)

    # 6) الأحجام vs السقوف
    def _sizes():
        import logkit
        logs_b = logkit.total_size_bytes()
        sess_b = _dir_size(Config.SESSIONS_DIR) if os.path.isdir(Config.SESSIONS_DIR) else 0
        inc_b = _dir_size(Config.INCIDENTS_DIR) if os.path.isdir(Config.INCIDENTS_DIR) else 0
        jobs_b = os.path.getsize(Config.JOBS_FILE) if os.path.exists(Config.JOBS_FILE) else 0
        all_md = os.path.join(Config.TRANSCRIPTS_DIR, "ALL.md")
        arch_b = os.path.getsize(all_md) if os.path.exists(all_md) else 0
        cap = Config.LOG_TOTAL_MAX_MB * 1024 * 1024
        txt = f"logs {_mb(logs_b)}/{Config.LOG_TOTAL_MAX_MB}MB · sessions {_mb(sess_b)} · ALL.md {_mb(arch_b)} · jobs {_mb(jobs_b)} · incidents {_mb(inc_b)}"
        if logs_b > cap or arch_b > 50 * 1024 * 1024:
            return WARN, txt
        return OK, txt
    add("الأحجام", _sizes)

    # 7) الجلسات: اتساق الفهرس مع الملفات
    def _sessions():
        import sessions
        items = sessions.list_sessions(include_archived=True)
        missing = [s["id"] for s in items if not os.path.exists(sessions.session_file(s["id"]))]
        cur = sessions.current()
        txt = f"{len(items)} جلسة · نشطة: {('«' + cur['name'] + '»') if cur else 'لا'} · ملفات مفقودة {len(missing)}"
        return (WARN if missing else OK), txt
    add("الجلسات", _sessions)

    # 8) الحوادث الأخيرة
    def _incidents():
        import incidents
        recent = incidents.list_recent(3)
        if not recent:
            return OK, "لا حوادث مسجلة"
        return WARN, "آخر الحوادث: " + " · ".join(os.path.basename(p) for p in recent)
    add("الحوادث", _incidents)

    # 9) العملية
    def _process():
        started = (bot_state or {}).get("started_at")
        up = int(now - started) if started else None
        wd = ""
        if watchdog is not None:
            try:
                s = watchdog.snapshot()
                wd = f" · watchdog L1 {s.get('restarts', 0)} مرة (في النافذة {s.get('l1_in_window', 0)}) مستوى {s.get('level') or 'طبيعي'}"
            except Exception:
                pass
        gen = os.environ.get("WORKER_GENERATION")
        txt = f"Python {platform.python_version()} · {sys.platform} · تشغيل منذ {up // 60 if up is not None else '?'} د" + (f" · العامل #{gen}" if gen else "") + wd
        return OK, txt
    add("العملية", _process)

    worst = OK
    for st, _ in results:
        if _RANK[st] > _RANK[worst]:
            worst = st
    header = f"🩺 الطبيب الذاتي — {datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M')} — الحالة العامة: {_ICON[worst]}"
    return [header] + [t for _s, t in results], worst


def doctor_text(**kw):
    lines, _worst = doctor_report(**kw)
    return "\n".join(lines)
