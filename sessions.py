# -*- coding: utf-8 -*-
"""
الجلسات (المقطوعات) والتسمية الذكية والسياق — مواصفتا 08 و10 (المرحلة 4).

نموذج البيانات (08 §1):
    DATA_DIR/sessions/
      sessions_index.json      {schema_version, sessions:[{id,name,status,started,ended,count,last_ts,role_override,
                                                           pending_title, strikes, simple_left, context_off}]}
      session_<uuid8>.md       ملف الجلسة (append-only) برأس YAML بسيط لإعادة بناء الفهرس (08 §4)
      current_session.json     {"id": ...} أو {"id": null} — يستمر بعد restart

المبادئ:
- كتابة مزدوجة: كل فويس في ALL.md (archive.py) **و** في ملف الجلسة النشطة (نفس صيغة المدخل).
- لا حذف نهائي أبداً (08 §6): /end يقفل، والدمج يؤرشف الأصلين (status=archived).
- لا جلسة تلقائية؛ بلا جلسة نشطة = ALL.md فقط (archive يتولاه).
- كل عمليات JSON ذرية (store)، والـ .md بـ append + fsync.
- السياق (10 §2): L0 مصطلحات الجلسة (محلياً بلا AI) + L1 آخر CONTEXT_LAST_N خام مقتطعة إلى CONTEXT_MAX_CHARS.
- 2-strike (10 §3): الضربات تُحفظ في الفهرس لكل جلسة؛ بعد ضربتين → بسيط لـ SIMPLE_MODE_COOLDOWN فويسات.
- التسمية الذكية (08 §3): اقتراح عبر text_assist (flash-lite نصياً) بعد إرسال الخام؛ قبول صامت عند الفويس التالي.
"""

import os
import re
import threading
import uuid
from datetime import datetime

from config import Config
import store

SCHEMA_VERSION = 1
_lock = threading.RLock()
_index_cache = None
_index_path = None
_current_cache = "unset"     # sentinel
_current_path = None

_ENTRY_MARK = "## 🎙️ "
_ENTRY_RE = re.compile(r"^## 🎙️ ", re.M)
_RAW_MARK, _POL_MARK, _TITLE_MARK = "### الخام", "### المنظم", "### العنوان"
_BAD_NAME_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')
_TECH_TERM_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+#.\-]{1,30}\b")


# ======================================================================
# مسارات ومساعدات
# ======================================================================
def sessions_dir():
    return Config.SESSIONS_DIR


def index_path():
    return Config.SESSIONS_INDEX_FILE


def current_path():
    return Config.CURRENT_SESSION_FILE


def session_file(session_id):
    return os.path.join(sessions_dir(), f"session_{session_id}.md")


def _now_str(when=None):
    return (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _short_model(model):
    return (model or "?").replace("gemini-", "")


def sanitize_name(name):
    """08 §3: إزالة / \\ : * ? " < > | + حد SESSION_NAME_MAX حرفاً. يرجع '' لو فارغ."""
    s = _BAD_NAME_CHARS.sub(" ", str(name or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:Config.SESSION_NAME_MAX].strip()


def default_name(when=None):
    # بلا ":" (محظورة في أسماء الملفات على ويندوز — sanitize_name تزيلها)
    return "جلسة " + (when or datetime.now()).strftime("%Y-%m-%d %H.%M")


def reset_cache():
    global _index_cache, _index_path, _current_cache, _current_path
    with _lock:
        _index_cache, _index_path = None, None
        _current_cache, _current_path = "unset", None


# ======================================================================
# الفهرس
# ======================================================================
def _default_index():
    return {"schema_version": SCHEMA_VERSION, "sessions": []}


def _load_index():
    global _index_cache, _index_path
    if _index_cache is not None and _index_path == index_path():
        return _index_cache
    corrupt = []
    data = store.load_json_tolerant(index_path(), default=_default_index(),
                                    on_corrupt=lambda p, d, e: corrupt.append(str(e)))
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        data = _default_index()
    if corrupt or (not data["sessions"] and os.path.isdir(sessions_dir())):
        # فهرس تالف/مفقود وملفات موجودة → إعادة بناء من رؤوس session_*.md (08 §4)
        rebuilt = _rebuild_from_files()
        if rebuilt:
            data = {"schema_version": SCHEMA_VERSION, "sessions": rebuilt}
            _write_index(data)
            print(f"🔁 الجلسات: أُعيد بناء الفهرس من {len(rebuilt)} ملف" + (" (كان تالفاً)" if corrupt else ""))
    _index_cache, _index_path = data, index_path()
    return data


def _write_index(data=None):
    data = data if data is not None else _index_cache
    try:
        store.atomic_write_json(index_path(), data)
    except Exception as e:  # pragma: no cover
        print(f"⚠️ sessions_index.json: تعذّر الحفظ ({e})")


def _rebuild_from_files():
    out = []
    try:
        names = sorted(os.listdir(sessions_dir()))
    except OSError:
        return out
    for fn in names:
        m = re.fullmatch(r"session_([0-9a-f]{8})\.md", fn)
        if not m:
            continue
        path = os.path.join(sessions_dir(), fn)
        meta = _read_yaml_head(path)
        count, tail = _scan_file(path)
        # الحالة الحقيقية من ذيل الملف (append-only): آخر حدث ⏹️/▶️؛ الاسم الأخير من 📛/✏️ لو وُجد
        status = "active"
        if tail.get("last_event") == "end":
            status = "ended"
        if tail.get("merged"):
            status = "archived"
        out.append({"id": m.group(1), "name": tail.get("name") or meta.get("name") or f"جلسة {m.group(1)}",
                    "status": status, "started": meta.get("started") or "",
                    "ended": tail.get("ended"), "count": count, "last_ts": tail.get("last_ts") or meta.get("started") or "",
                    "role_override": None, "pending_title": None, "strikes": 0, "simple_left": 0,
                    "summary": tail.get("summary"), "summary_at": tail.get("summary_at", 0),
                    "context_off": False})
    return out


def _read_yaml_head(path):
    meta = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            first = f.readline()
            if first.strip() != "---":
                return meta
            for _ in range(20):
                line = f.readline()
                if not line or line.strip() == "---":
                    break
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return meta


def _scan_file(path):
    """(count, tail): عدد المدخلات + آخر حدث/اسم/طابع زمني من ملف الجلسة (لإعادة البناء)."""
    count, tail = 0, {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                if ln.startswith(_ENTRY_MARK):
                    count += 1
                    tail["last_ts"] = ln[len(_ENTRY_MARK):len(_ENTRY_MARK) + 19]
                elif ln.startswith("⏹️ انتهت"):
                    tail["last_event"] = "end"
                    tail["ended"] = ln.split(":", 1)[1].strip()[:19] if ":" in ln else None
                elif ln.startswith("▶️ استُؤنفت"):
                    tail["last_event"] = "resume"
                    tail["ended"] = None
                elif ln.startswith(("📛 الاسم: «", "✏️ أُعيدت التسمية: «")):
                    tail["name"] = ln.split("«", 1)[1].rsplit("»", 1)[0]
                elif ln.startswith("📦 دُمجت في"):
                    tail["merged"] = True
                elif ln.startswith("🧾 ملخص: "):
                    tail["summary"] = ln[len("🧾 ملخص: "):].strip()
                    tail["summary_at"] = count
    except OSError:
        pass
    return count, tail


def _find(session_id_or_prefix):
    sid = str(session_id_or_prefix or "").strip()
    if not sid:
        return None
    for s in _load_index()["sessions"]:
        if s["id"] == sid or s["id"].startswith(sid):
            return s
    return None


def get(session_id):
    with _lock:
        s = _find(session_id)
        return dict(s) if s else None


def list_sessions(status=None, include_archived=False):
    """الأحدث أولاً. status: active|ended|archived|None."""
    with _lock:
        out = []
        for s in _load_index()["sessions"]:
            if status and s.get("status") != status:
                continue
            if not include_archived and not status and s.get("status") == "archived":
                continue
            out.append(dict(s))
        out.sort(key=lambda s: s.get("last_ts") or s.get("started") or "", reverse=True)
        return out


def unique_name(name, exclude_id=None):
    """تكرار الاسم → لاحقة (2) (3)… (08 §3)."""
    base = sanitize_name(name) or default_name()
    existing = {s["name"] for s in _load_index()["sessions"] if s["id"] != exclude_id}
    if base not in existing:
        return base
    n = 2
    while f"{base} ({n})" in existing:
        n += 1
    return f"{base} ({n})"


# ======================================================================
# الجلسة النشطة
# ======================================================================
def current_id():
    global _current_cache, _current_path
    with _lock:
        if _current_cache != "unset" and _current_path == current_path():
            return _current_cache
        data = store.load_json_tolerant(current_path(), default={"id": None})
        sid = data.get("id") if isinstance(data, dict) else None
        s = _find(sid) if sid else None
        if s is None or s.get("status") != "active":
            sid = None
        _current_cache, _current_path = sid, current_path()
        return sid


def current():
    sid = current_id()
    return get(sid) if sid else None


def current_name():
    s = current()
    return s["name"] if s else None


def _set_current(sid):
    global _current_cache, _current_path
    try:
        store.atomic_write_json(current_path(), {"id": sid})
    except Exception as e:  # pragma: no cover
        print(f"⚠️ current_session.json: {e}")
    _current_cache, _current_path = sid, current_path()


# ======================================================================
# دورة الحياة
# ======================================================================
def start(name=None, when=None):
    """جلسة جديدة تبدأ فوراً (تغلق النشطة الحالية لو وُجدت — جلسة واحدة نشطة). يرجع dict الجلسة."""
    with _lock:
        cur = current_id()
        if cur:
            end(cur, when=when)
        idx = _load_index()
        sid = uuid.uuid4().hex[:8]
        while _find(sid):
            sid = uuid.uuid4().hex[:8]
        auto = not (name and sanitize_name(name))
        nm = unique_name(name if not auto else default_name(when))
        s = {"id": sid, "name": nm, "status": "active", "started": _now_str(when), "ended": None, "count": 0,
             "last_ts": _now_str(when), "role_override": None, "pending_title": None, "auto_named": auto,
             "strikes": 0, "simple_left": 0, "context_off": False}
        idx["sessions"].append(s)
        _write_index(idx)
        _write_file_header(s)
        _set_current(sid)
        return dict(s)


def _write_file_header(s):
    path = session_file(s["id"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    import archive
    head = ("---\n"
            f"id: {s['id']}\n"
            f"name: \"{s['name']}\"\n"
            f"status: {s['status']}\n"
            f"started: {s['started']}\n"
            "---\n"
            f"# 📂 جلسة: «{s['name']}»\n\n" + archive.banner_text() + "\n\n"
            f"▶️ بدأت: {s['started']}\n\n")
    _append_file(path, head)


def _append_file(path, text):
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def end(session_id=None, when=None):
    """ينهي الجلسة (النشطة افتراضياً). حفظ صامت؛ لا إرسال قسري (08 §2). يرجع dict أو None."""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return None
        if s.get("status") == "active":
            s["status"] = "ended"
            s["ended"] = _now_str(when)
            _write_index()
            try:
                _append_file(session_file(s["id"]), f"⏹️ انتهت: {s['ended']} · {s['count']} فويس\n\n")
            except OSError as e:  # pragma: no cover
                print(f"⚠️ ملف الجلسة: {e}")
        if current_id() == s["id"]:
            _set_current(None)
        return dict(s)


def resume(session_id, when=None):
    """استئناف جلسة منتهية (تصبح النشطة؛ النشطة الحالية تُغلق). None لو غير موجودة/مؤرشفة."""
    with _lock:
        s = _find(session_id)
        if s is None or s.get("status") == "archived":
            return None
        cur = current_id()
        if cur and cur != s["id"]:
            end(cur, when=when)
        if s["status"] != "active":
            s["status"] = "active"
            s["ended"] = None
            s["last_ts"] = _now_str(when)
            _write_index()
            try:
                _append_file(session_file(s["id"]), f"▶️ استُؤنفت: {_now_str(when)}\n\n")
            except OSError:  # pragma: no cover
                pass
        _set_current(s["id"])
        return dict(s)


def rename(new_name, session_id=None):
    """تعديل يدوي يغلب الاقتراح (08 §7.3). يرجع الاسم الجديد أو None."""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        clean = sanitize_name(new_name)
        if s is None or not clean:
            return None
        s["name"] = unique_name(clean, exclude_id=s["id"])
        s["pending_title"] = None
        s["auto_named"] = False
        _write_index()
        try:
            _append_file(session_file(s["id"]), f"✏️ أُعيدت التسمية: «{s['name']}»\n\n")
        except OSError:  # pragma: no cover
            pass
        return s["name"]


def merge(id_a, id_b, name=None, when=None):
    """دمج جلستين بالترتيب الزمني في جلسة جديدة (منتهية)؛ الأصلان يُؤرشفان لا يُحذفان (08 §2)."""
    with _lock:
        a, b = _find(id_a), _find(id_b)
        if a is None or b is None or a["id"] == b["id"]:
            return None
        for s in (a, b):
            if s["id"] == current_id():
                end(s["id"], when=when)
        entries = _entries_of(a["id"]) + _entries_of(b["id"])
        entries.sort(key=lambda e: e["meta"][:19])
        sid = uuid.uuid4().hex[:8]
        nm = unique_name(name or f"{a['name']} + {b['name']}")
        s = {"id": sid, "name": nm, "status": "ended", "started": min(a["started"], b["started"]),
             "ended": _now_str(when), "count": len(entries), "last_ts": _now_str(when), "role_override": None,
             "pending_title": None, "auto_named": False, "strikes": 0, "simple_left": 0, "context_off": False,
             "merged_from": [a["id"], b["id"]]}
        idx = _load_index()
        idx["sessions"].append(s)
        a["status"] = b["status"] = "archived"
        _write_index(idx)
        for src in (a, b):
            try:
                _append_file(session_file(src["id"]), f"📦 دُمجت في [{sid}] «{nm}»: {_now_str(when)}\n\n")
            except OSError:  # pragma: no cover
                pass
        _write_file_header(s)
        body = "".join(_format_entry_block(e["meta"], e["raw"], e["polished"], e.get("title")) for e in entries)
        _append_file(session_file(sid), body + f"⏹️ انتهت (دمج): {s['ended']} · {s['count']} فويس\n\n")
        return dict(s)


# ======================================================================
# الكتابة المزدوجة (المدخلات)
# ======================================================================
def _format_entry_block(meta, raw, polished=None, title=None):
    block = _ENTRY_MARK + meta + "\n"
    if title:
        block += _TITLE_MARK + "\n" + title.strip() + "\n"
    block += _RAW_MARK + "\n" + (raw or "").strip() + "\n"
    if polished:
        block += _POL_MARK + "\n" + polished.strip() + "\n"
    return block + "\n"


def append_entry(raw_text, model=None, elapsed=None, key_name=None, when=None, extra=None, session_id=None):
    """يكتب مدخلاً في ملف الجلسة النشطة (بنفس صيغة ALL.md). يرجع True لو كُتب، False لو لا جلسة/فشل."""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return False
        try:
            meta = [_now_str(when), f"جلسة: «{s['name']}»", _short_model(model)]
            if elapsed is not None:
                meta.append(f"{elapsed}ث")
            if key_name:
                meta.append(f"🔑 {key_name}")
            if extra:
                meta.append(str(extra))
            _write_file_header(s)
            _append_file(session_file(s["id"]), _format_entry_block(" · ".join(meta), raw_text))
            s["count"] = int(s.get("count") or 0) + 1
            s["last_ts"] = _now_str(when)
            _write_index()
            return True
        except Exception as e:
            print(f"⚠️ ملف الجلسة: تعذّر الكتابة ({type(e).__name__}: {e})")
            return False


def append_polished(polished_text, session_id=None):
    """يلحق «### المنظم» بآخر مدخل في ملف الجلسة (بلا تكرار)."""
    if not polished_text:
        return False
    with _lock:
        sid = session_id or current_id()
        if not sid:
            return False
        try:
            block = _read_last_block(session_file(sid))
            if block is None or _POL_MARK in block:
                return False
            _append_file(session_file(sid), _POL_MARK + "\n" + polished_text.strip() + "\n\n")
            return True
        except Exception as e:  # pragma: no cover
            print(f"⚠️ ملف الجلسة: تعذّر إلحاق المنظم ({e})")
            return False


def append_title(title, session_id=None):
    """يلحق «### العنوان» بآخر مدخل (append-only؛ العنوان يصل بعد الخام)."""
    title = (title or "").strip()
    if not title:
        return False
    with _lock:
        sid = session_id or current_id()
        if not sid:
            return False
        try:
            block = _read_last_block(session_file(sid))
            if block is None or _TITLE_MARK in block:
                return False
            _append_file(session_file(sid), _TITLE_MARK + "\n" + title + "\n\n")
            return True
        except Exception:  # pragma: no cover
            return False


def _read_last_block(path, tail_bytes=65536):
    if not os.path.exists(path):
        return None
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - tail_bytes))
        data = f.read().decode("utf-8", errors="ignore")
    parts = _ENTRY_RE.split(data)
    if len(parts) < 2:
        return None
    return _ENTRY_MARK + parts[-1]


def _parse_block(block):
    lines = block.strip("\n").split("\n")
    meta = lines[0][len(_ENTRY_MARK):].strip()
    raw, pol, title, cur = [], [], [], None
    for ln in lines[1:]:
        if ln.startswith(_RAW_MARK):
            cur = raw
        elif ln.startswith(_POL_MARK):
            cur = pol
        elif ln.startswith(_TITLE_MARK):
            cur = title
        elif ln.startswith(("⏹️ ", "▶️ ", "✏️ ", "📛 ", "📦 ", "🎭 ", "🧾 ")):
            cur = None
        elif cur is not None:
            cur.append(ln)
    return {"meta": meta, "raw": "\n".join(raw).strip(), "polished": "\n".join(pol).strip() or None,
            "title": "\n".join(title).strip() or None}


def _entries_of(session_id):
    path = session_file(session_id)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = f.read()
    parts = _ENTRY_RE.split(data)
    return [_parse_block(_ENTRY_MARK + p) for p in parts[1:]]


def entries(session_id):
    with _lock:
        return _entries_of(session_id)


def last_entries(n=3, session_id=None):
    """آخر n مدخلات (من الملف، بلا LLM) — بطاقة الاستئناف (08 §2 RESUME_RECAP)."""
    with _lock:
        sid = session_id or current_id()
        if not sid:
            return []
        return _entries_of(sid)[-n:]


def export_bytes(session_id):
    """محتوى ملف الجلسة كـ bytes (أو None). /export (08 §2)."""
    path = session_file(session_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def summary_card(session_id=None):
    """«📂 اسم — N فويس | آخر نشاط …» (08 §2)."""
    s = get(session_id) if session_id else current()
    if not s:
        return None
    return f"📂 {s['name']} — {s.get('count', 0)} فويس | آخر نشاط {s.get('last_ts') or s.get('started')}"


def list_text(page=1, per_page=None, status=None):
    """قائمة نصية مرقّمة (5/صفحة) بالصيغة «اسم (N فويس)» مع id للاستئناف."""
    per_page = per_page or Config.PANEL_PAGE_SIZE
    items = list_sessions(status=status)
    if not items:
        return "لا توجد جلسات بعد. /new_session [اسم] لبدء واحدة."
    pages = max(1, (len(items) + per_page - 1) // per_page)
    page = min(max(1, page), pages)
    chunk = items[(page - 1) * per_page: page * per_page]
    cur = current_id()
    lines = [f"📋 الجلسات ({len(items)}) — صفحة {page}/{pages}:"]
    for s in chunk:
        mark = "🟢" if s["id"] == cur else ("⏹️" if s["status"] == "ended" else "📦")
        lines.append(f"{mark} [{s['id']}] {s['name']} ({s.get('count', 0)} فويس) · {s.get('last_ts', '')[:16]}")
    if pages > 1 and page < pages:
        lines.append(f"الصفحة التالية: /sessions {page + 1}")
    lines.append("/resume <id> للاستئناف · /export <id> للتنزيل")
    return "\n".join(lines)


# ======================================================================
# التسمية الذكية (08 §3)
# ======================================================================
def needs_title_suggestion(session_id=None):
    """هل الجلسة لا تزال باسمها المؤقت وبلا اقتراح معلّق؟"""
    s = get(session_id) if session_id else current()
    return bool(s and s.get("auto_named") and not s.get("pending_title") and s.get("count", 0) >= 1)


def propose_title(raw_text, session_id=None, generate_fn=None):
    """
    يطلب اسماً (2–4 كلمات) من أول SMART_TITLE_WORDS كلمة عبر text_assist (نصياً). يحفظه pending_title.
    generate_fn(system, user) -> (text|None, reason) — يُحقن في الاختبارات. يرجع الاسم المقترح أو None.
    """
    if not Config.SMART_TITLE_ENABLED:
        return None
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return None
    words = (raw_text or "").split()[:Config.SMART_TITLE_WORDS]
    if len(words) < 3:
        return None
    if generate_fn is None:
        import text_assist
        generate_fn = text_assist.generate
    system = ("اقترح عنواناً قصيراً (2 إلى 4 كلمات، بلا علامات ترقيم ولا أقواس ولا شرح) يلخّص موضوع هذا النص المفرغ "
              "من فويس عربي. أخرج العنوان فقط.")
    try:
        text, _reason = generate_fn(system, " ".join(words))
    except Exception:
        text = None
    title = sanitize_name((text or "").strip().split("\n")[0].strip("«»\"'.:- "))
    if not title or len(title.split()) > 6:
        return None
    with _lock:
        s = _find(sid)
        if s is None:
            return None
        s["pending_title"] = title
        _write_index()
    return title


def suggest_titles(raw_text=None, n=None, session_id=None, generate_fn=None):
    """
    «🎲 3 اقتراحات» (08 §2) — عند الطلب فقط: n نداءات flash-lite نصية، كل اقتراح 2–4 كلمات، مُنقّى ومُزال التكرار.
    raw_text=None → آخر نص خام في الجلسة. يرجع قائمة (قد تكون أقصر من n أو فارغة). لا يغيّر الفهرس.
    """
    n = n or Config.TITLE_SUGGESTIONS_N
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return []
        if raw_text is None:
            ents = _entries_of(s["id"])
            raw_text = ents[-1]["raw"] if ents else ""
    words = (raw_text or "").split()[:Config.SMART_TITLE_WORDS]
    if len(words) < 3:
        return []
    if generate_fn is None:
        import text_assist
        generate_fn = text_assist.generate
    out, seen = [], {sanitize_name(s.get("name") or "").lower()}
    for i in range(n):
        system = ("اقترح عنواناً قصيراً (2 إلى 4 كلمات، بلا علامات ترقيم ولا أقواس ولا شرح) يلخّص موضوع هذا النص المفرغ "
                  f"من فويس عربي. هذا الاقتراح رقم {i + 1}؛ اجعله مختلفاً في الصياغة عن: {', '.join(out) or 'لا شيء'}. أخرج العنوان فقط.")
        try:
            text, _reason = generate_fn(system, " ".join(words))
        except Exception:
            text = None
        t = sanitize_name((text or "").strip().split("\n")[0].strip("«»\"'.:- "))
        if not t or len(t.split()) > 6 or t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
    return out


def remember_title_options(options, session_id=None):
    """يحفظ آخر اقتراحات «🎲» في الفهرس ليعمل /pick_title بعد إعادة التشغيل."""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return False
        s["title_options"] = list(options or [])[:10]
        _write_index()
        return True


def title_options(session_id=None):
    s = get(session_id) if session_id else current()
    return list((s or {}).get("title_options") or [])


def pick_title(index_1based, session_id=None):
    """يعتمد الاقتراح رقم n من آخر «🎲» (إعادة تسمية + مسح المعلّق والخيارات). يرجع الاسم أو None."""
    opts = title_options(session_id)
    try:
        i = int(index_1based) - 1
    except (TypeError, ValueError):
        return None
    if i < 0 or i >= len(opts):
        return None
    with _lock:
        sid = session_id or current_id()
    new_name = rename(opts[i], sid)
    if not new_name:
        return None
    with _lock:
        s2 = _find(sid)
        if s2 is not None:
            s2["title_options"] = []
            _write_index()
    return new_name


def accept_pending_title(session_id=None):
    """قبول صامت (08 §2 قرار المالك أ): يعتمد الاقتراح المعلّق اسماً للجلسة. يرجع الاسم أو None."""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None or not s.get("pending_title"):
            return None
        s["name"] = unique_name(s["pending_title"], exclude_id=s["id"])
        s["pending_title"] = None
        s["auto_named"] = False
        _write_index()
        try:
            _append_file(session_file(s["id"]), f"📛 الاسم: «{s['name']}»\n\n")
        except OSError:  # pragma: no cover
            pass
        return s["name"]


def reject_pending_title(session_id=None):
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return False
        s["pending_title"] = None
        _write_index()
        return True


def voice_title(raw_text, generate_fn=None):
    """عنوان قصير للفويس نفسه (3–6 كلمات) لترويسة الرسالة والأرشيف — نصي فقط. None عند التعطيل/الفشل."""
    if not Config.SMART_TITLE_ENABLED:
        return None
    words = (raw_text or "").split()
    if len(words) < 8:
        return None
    if generate_fn is None:
        import text_assist
        generate_fn = text_assist.generate
    system = ("اكتب عنواناً معبّراً من 3 إلى 6 كلمات لهذا النص المفرغ من فويس عربي، بلا علامات ترقيم ولا شرح. "
              "أخرج العنوان فقط.")
    try:
        text, _reason = generate_fn(system, " ".join(words[:Config.SMART_TITLE_WORDS]))
    except Exception:
        text = None
    t = sanitize_name((text or "").strip().split("\n")[0].strip("«»\"'.:- "))
    if not t or len(t.split()) > 8:
        return None
    return t


# ======================================================================
# دور الجلسة (06 §2 / 08 §1): session.role_override
# ======================================================================
def set_role_override(role_id, session_id=None):
    """تعيين/إلغاء تجاوز الدور لجلسة (None أو "" = الدور الافتراضي). يُعاد False إن لم توجد جلسة."""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return False
        s["role_override"] = (role_id or "").strip().lower() or None
        _write_index()
        try:
            _append_file(session_file(s["id"]), f"🎭 دور الجلسة: {s['role_override'] or 'الافتراضي'}\n\n")
        except OSError:
            pass
    try:
        import audit
        audit.record("session.role_override", session=sid, role=s["role_override"] or "-")
    except Exception:
        pass
    return True


def role_override(session_id=None):
    """الدور المتجاوز للجلسة (الحالية افتراضياً) أو None."""
    s = get(session_id) if session_id else current()
    return (s or {}).get("role_override") or None


def terms_hint(session_id=None):
    """L0 (10 §2): سطر تلميح إملائي من مصطلحات الجلسة لطبقة A. فارغ إن لا جلسة/لا مصطلحات."""
    terms = session_terms(session_id)
    if not terms:
        return ""
    return "أسماء ظهرت في هذه الجلسة، اكتبها كما هي: " + ", ".join(terms)


# ======================================================================
# L2 (10 §2): ملخص الجلسة التراكمي — كل SESSION_SUMMARY_EVERY فويسات عبر flash-lite (نصياً)
# يدخل طبقة B (التنظيم) فقط. لا يُحقن أبداً في طبقة A (التفريغ).
# ======================================================================
def summary(session_id=None):
    """آخر ملخص تراكمي محفوظ للجلسة (أو None)."""
    s = get(session_id) if session_id else current()
    return (s or {}).get("summary") or None


def summary_due(session_id=None):
    """هل حان تحديث الملخص؟ (count وصل مضاعف SESSION_SUMMARY_EVERY ولم يُلخَّص عنده بعد)."""
    every = int(Config.SESSION_SUMMARY_EVERY or 0)
    if every <= 0:
        return False
    s = get(session_id) if session_id else current()
    if not s:
        return False
    count = int(s.get("count") or 0)
    return count > 0 and count % every == 0 and int(s.get("summary_at") or 0) < count


def maybe_update_summary(session_id=None, generate_fn=None):
    """
    يحدّث الملخص إن حان وقته: يُغذّي flash-lite بالملخص السابق + آخر N نصوص خام (مقتطعة) ويطلب ملخصاً ≤ SESSION_SUMMARY_MAX_CHARS.
    يُخزَّن في الفهرس ويُلحق بملف الجلسة كسطر «🧾 ملخص:» (append-only → يُستعاد عند إعادة البناء).
    يرجع الملخص الجديد أو None (لا شيء حان/فشل). أي فشل صامت — لا يؤثر على الفويس.
    """
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return None
        sid = s["id"]
    if not summary_due(sid):
        return None
    every = int(Config.SESSION_SUMMARY_EVERY)
    with _lock:
        s = _find(sid)
        prev = (s or {}).get("summary") or ""
        count = int((s or {}).get("count") or 0)
        ents = _entries_of(sid)[-every:]
    if not ents:
        return None
    if generate_fn is None:
        import text_assist
        generate_fn = text_assist.generate
    chunk = "\n---\n".join((e["raw"] or "")[:1200] for e in ents)
    system = (f"أنت تلخّص جلسة تفريغ صوتي عربية تراكمياً. اكتب ملخصاً موجزاً (حتى {Config.SESSION_SUMMARY_MAX_CHARS} حرف) "
              "يذكر الموضوع، الأسماء والمصطلحات التقنية كما وردت، والقرارات/النقاط المفتوحة. بلا مقدمات ولا عناوين. أخرج الملخص فقط.")
    user = (f"الملخص السابق:\n{prev}\n\nالنصوص الجديدة:\n{chunk}" if prev else chunk)
    try:
        text, _reason = generate_fn(system, user)
    except Exception:
        text = None
    text = " ".join((text or "").split())
    if not text:
        return None
    text = text[:Config.SESSION_SUMMARY_MAX_CHARS]
    with _lock:
        s = _find(sid)
        if s is None:
            return None
        s["summary"] = text
        s["summary_at"] = count
        _write_index()
        try:
            _append_file(session_file(sid), f"🧾 ملخص: {text}\n\n")
        except OSError:  # pragma: no cover
            pass
    return text


# ======================================================================
# السياق (10 §2) + 2-strike (10 §3)
# ======================================================================
def session_terms(session_id=None, limit=None):
    """L0: الأسماء التقنية (لاتينية) التي ظهرت في الجلسة — استخراج محلي بلا AI."""
    limit = limit or Config.CONTEXT_TERMS_MAX
    with _lock:
        sid = session_id or current_id()
        if not sid:
            return []
        ents = _entries_of(sid)
    seen = {}
    for e in ents:
        for m in _TECH_TERM_RE.findall(e["raw"] or ""):
            if len(m) < 2 or m.lower() in ("the", "and", "ok", "a"):
                continue
            seen[m] = seen.get(m, 0) + 1
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    return [k for k, _ in ranked[:limit]]


def context_block(session_id=None):
    """
    L1: آخر CONTEXT_LAST_N خام من الجلسة النشطة مقتطعة إلى CONTEXT_MAX_CHARS (الأحدث يُحفظ أولاً).
    يرجع '' لو لا جلسة/معطّل/الجلسة في الوضع البسيط. الطول ≤ CONTEXT_MAX_CHARS دائماً (10 §7.4).
    """
    if not Config.CONTEXT_ENABLED:
        return ""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None or s.get("context_off") or int(s.get("simple_left") or 0) > 0:
            return ""
        ents = _entries_of(sid)[-Config.CONTEXT_LAST_N:]
    if not ents:
        return ""
    budget = Config.CONTEXT_MAX_CHARS
    pieces = []
    for e in reversed(ents):          # الأحدث أولاً في توزيع الميزانية
        raw = re.sub(r"\s+", " ", e["raw"] or "").strip()
        if not raw or budget <= 0:
            continue
        piece = raw if len(raw) <= budget else raw[:budget].rsplit(" ", 1)[0] + "…"
        pieces.append(piece)
        budget -= len(piece) + 1
    pieces.reverse()
    out = "\n".join(pieces)
    return out[:Config.CONTEXT_MAX_CHARS]


def context_prompt_text(session_id=None):
    """نص الحقن في طبقة A: «سياق سابق — لا تعِد كتابته، فرّغ الصوت الجديد فقط» + L1."""
    l1 = context_block(session_id)
    if not l1:
        return ""
    return "سياق سابق من نفس الجلسة (للفهم فقط — لا تعِد كتابته، فرّغ الصوت الجديد فقط):\n" + l1


def context_mode(session_id=None):
    """'off' (لا جلسة/معطّل) | 'simple' (بسيط مؤقتاً) | 'full'."""
    if not Config.CONTEXT_ENABLED:
        return "off"
    s = get(session_id) if session_id else current()
    if not s:
        return "off"
    if s.get("context_off") or int(s.get("simple_left") or 0) > 0:
        return "simple"
    return "full"


def set_context_enabled(enabled, session_id=None):
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return False
        s["context_off"] = not bool(enabled)
        if enabled:
            s["strikes"], s["simple_left"] = 0, 0
        _write_index()
        return True


def is_context_echo(output_text, l1_text, ratio=None):
    """
    كشف «صدى السياق» (10 §3): نسبة كلمات الناتج (متتابعات 3 كلمات) الموجودة حرفياً في L1 ≥ CONTEXT_ECHO_RATIO.
    نصوص قصيرة جداً (< 6 كلمات) لا تُحتسب صدى (تجنّب إنذارات كاذبة).
    """
    ratio = Config.CONTEXT_ECHO_RATIO if ratio is None else ratio
    if not output_text or not l1_text:
        return False
    norm = lambda t: re.sub(r"[^\w\s]", " ", t).split()  # noqa: E731
    out_w, ctx_w = norm(output_text), norm(l1_text)
    if len(out_w) < 6 or len(ctx_w) < 3:
        return False
    ctx_tri = {tuple(ctx_w[i:i + 3]) for i in range(len(ctx_w) - 2)}
    tris = [tuple(out_w[i:i + 3]) for i in range(len(out_w) - 2)]
    hit = sum(1 for t in tris if t in ctx_tri)
    return (hit / len(tris)) >= ratio


def record_strike(reason, session_id=None):
    """ضربة بسبب السياق (BLOCKED/BAD_REQUEST/فارغ/صدى). عند الوصول للحد → بسيط لـ SIMPLE_MODE_COOLDOWN. يرجع العدد."""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return 0
        s["strikes"] = int(s.get("strikes") or 0) + 1
        if s["strikes"] >= Config.CONTEXT_STRIKES_TO_SIMPLE:
            s["simple_left"] = Config.SIMPLE_MODE_COOLDOWN
            print(f"⚙️ الجلسة «{s['name']}»: ضربتان ({reason}) — الوضع البسيط لـ {s['simple_left']} فويسات")
        _write_index()
        return s["strikes"]


def record_success(used_context, session_id=None):
    """نجاح تفريغ: يصفّر الضربات؛ لو كنا في الوضع البسيط يُنقص العدّاد (يعود السياق بعد الكولداون)."""
    with _lock:
        sid = session_id or current_id()
        s = _find(sid) if sid else None
        if s is None:
            return
        s["strikes"] = 0
        if int(s.get("simple_left") or 0) > 0 and not used_context:
            s["simple_left"] = int(s["simple_left"]) - 1
        _write_index()


def strikes(session_id=None):
    s = get(session_id) if session_id else current()
    return int(s.get("strikes") or 0) if s else 0


# ======================================================================
# إحصاء (08 §5)
# ======================================================================
def stats_text():
    items = list_sessions(include_archived=True)
    active = [s for s in items if s["status"] == "active"]
    total_voices = sum(int(s.get("count") or 0) for s in items if s["status"] != "archived")
    cur = current()
    lines = [f"📊 الجلسات: {len(items)} (نشطة: {len(active)}) · فويسات داخل جلسات: {total_voices}"]
    if cur:
        lines.append(f"🟢 النشطة: «{cur['name']}» — {cur.get('count', 0)} فويس · السياق: {context_mode()}"
                     + (f" · ضربات: {cur.get('strikes', 0)}" if cur.get("strikes") else "")
                     + (f" · 🧾 ملخص L2 عند الفويس {cur.get('summary_at')}" if cur.get("summary") else ""))
        if cur.get("summary"):
            lines.append(f"🧾 {cur['summary'][:300]}{'…' if len(cur['summary']) > 300 else ''}")
    else:
        lines.append("لا جلسة نشطة (الفويسات تُؤرشف في ALL.md فقط).")
    return "\n".join(lines)
