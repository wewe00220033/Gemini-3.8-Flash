# -*- coding: utf-8 -*-
"""
jobs — سجل المهام على القرص `state/jobs.ndjson` (03 §3، 22 U1): سطر لكل حدث، fsync، بلا قاعدة بيانات.

الأحداث: received → downloaded → transcribed → sent | failed | skipped
كل سطر: {"ts", "job_id", "event", ...حقول}. المهمة = آخر حالة لها.

يوفر:
- `record(job_id, event, **fields)`   — كتابة حدث (append + fsync عبر store).
- `load_state()`                      — إعادة بناء {job_id: job} من الملف (الأحدث يغلب).
- `pending_jobs(max_age_h)`           — المهام التي لم تصل إلى sent/failed/skipped → تُستأنف بعد restart (P0).
- `find_recent_result(file_unique_id, hours)` — idempotency: نفس الملف خلال 24ساعة → النص المحفوظ.
- `compact_if_needed()`               — يضغط الملف لآخر N مهمة كي لا ينمو بلا حد.

لا يستورد سوى Config وstore (قابل للاختبار بمسار مؤقت).
"""
import json
import os
import threading
import time

from config import Config
import store

TERMINAL = ("sent", "failed", "skipped")
_lock = threading.RLock()
_seq = 0   # ترتيب الكتابة داخل العملية — ts قد يتساوى بين سطرين متتاليين


def _path():
    return Config.JOBS_FILE


def _ensure_dir():
    d = os.path.dirname(_path())
    if d:
        os.makedirs(d, exist_ok=True)


def record(job_id, event, **fields):
    """يكتب حدثاً (fsync). لا يرفع استثناء للمنادي — فشل القرص يُطبع ولا يوقف التفريغ."""
    global _seq
    row = {"ts": round(time.time(), 3), "job_id": job_id, "event": event}
    row.update({k: v for k, v in fields.items() if v is not None})
    try:
        with _lock:
            _seq += 1
            row["seq"] = _seq
            _ensure_dir()
            store.append_json_line(_path(), row)
        return True
    except Exception as e:
        print(f"⚠️ jobs: تعذر تسجيل {event} لـ {job_id}: {type(e).__name__}: {e}")
        return False


def load_state():
    """{job_id: {..آخر الحقول المتراكمة.., "state": آخر حدث, "first_ts", "last_ts", "events": [..]}}"""
    jobs = {}
    if not os.path.exists(_path()):
        return jobs
    bad = []
    with _lock:
        rows = store.read_json_lines(_path(), on_bad_line=lambda ln, text, err: bad.append(ln))
    order = 0
    for row in rows:
        jid = row.get("job_id")
        ev = row.get("event")
        if not jid or not ev:
            continue
        j = jobs.setdefault(jid, {"job_id": jid, "events": [], "first_ts": row.get("ts"), "order": order})
        order += 1
        for k, v in row.items():
            if k not in ("event", "ts", "job_id", "seq"):
                j[k] = v
        j["state"] = ev
        j["last_ts"] = row.get("ts")
        j["events"].append(ev)
    if bad:
        print(f"⚠️ jobs: {len(bad)} سطر تالف في jobs.ndjson تم تجاهله")
    return jobs


def pending_jobs(max_age_h=None, now=None):
    """المهام غير المكتملة (received/downloaded/transcribed) الأحدث من max_age_h، بترتيب الاستلام."""
    max_age_h = Config.JOBS_RESUME_MAX_AGE_H if max_age_h is None else max_age_h
    now = now or time.time()
    out = []
    for j in load_state().values():
        if j.get("state") in TERMINAL:
            continue
        if j.get("first_ts") is None or now - j["first_ts"] > max_age_h * 3600:
            continue
        if not j.get("file_id"):
            continue
        out.append(j)
    out.sort(key=lambda j: j.get("order", 0))      # ترتيب أول ظهور في الملف = ترتيب الاستلام
    return out


def find_recent_result(file_unique_id, hours=None, now=None):
    """نص محفوظ لنفس file_unique_id خلال hours (idempotency) أو None."""
    if not file_unique_id:
        return None
    hours = Config.JOBS_DEDUP_HOURS if hours is None else hours
    now = now or time.time()
    best = None
    for j in load_state().values():
        if j.get("file_unique_id") != file_unique_id or j.get("state") != "sent" or not j.get("text"):
            continue
        if now - (j.get("last_ts") or 0) > hours * 3600:
            continue
        if best is None or (j.get("last_ts") or 0) > (best.get("last_ts") or 0):
            best = j
    return best


def count_by_state():
    c = {}
    for j in load_state().values():
        c[j.get("state")] = c.get(j.get("state"), 0) + 1
    return c


def compact_if_needed(keep=None):
    """لو عدد المهام > 2×keep: يعيد كتابة الملف بآخر keep مهمة (كل أحداثها مضغوطة في سطر حالة واحد)."""
    keep = Config.JOBS_COMPACT_KEEP if keep is None else keep
    with _lock:
        jobs = load_state()
        if len(jobs) <= 2 * keep:
            return False
        recent = sorted(jobs.values(), key=lambda j: j.get("order", 0))[-keep:]
        lines = []
        for j in recent:
            row = {k: v for k, v in j.items() if k not in ("events", "state", "first_ts", "last_ts", "order")}
            row.update({"ts": j.get("last_ts"), "event": j.get("state"), "compacted": True})
            lines.append(json.dumps(row, ensure_ascii=False))
        store.atomic_write_text(_path(), "\n".join(lines) + "\n")
        return True
