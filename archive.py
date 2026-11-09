# -*- coding: utf-8 -*-
"""
الأرشفة الأبدية (مواصفة 08 §1، المرحلة 3a): `transcripts/ALL.md` append-only بعد كل تفريغ.

- يُكتب **قبل** الإرسال لتليجرام (outbox مجاني): لو فشل الإرسال فالنص محفوظ و`/last` يعيده.
- البانر (06 §4) يُكتب مرة واحدة في رأس الملف لا مع كل فويس.
- شكل المدخل موحد (08 §1) ليُعاد استخدامه في ملفات الجلسات (المرحلة 4):
    ## 🎙️ 2026-10-25 01:16:56 · بدون جلسة · 3.6-flash · 3.2ث
    ### الخام
    …
    ### المنظم
    …
- `last()` يقرأ آخر مدخل من ذيل الملف بلا تحميل الملف كله (الأرشيف يكبر للأبد).
- Thread-safe (قفل واحد) وكتابة append مع fsync؛ فشل القرص لا يكسر التفريغ (يرجع False).
"""

import os
import re
import threading
from datetime import datetime

from config import Config

_lock = threading.RLock()
_ENTRY_MARK = "## 🎙️ "
_ENTRY_RE = re.compile(r"^## 🎙️ ", re.M)
_RAW_MARK, _POL_MARK, _TITLE_MARK = "### الخام", "### المنظم", "### العنوان"
_ENTRY_ID_RE = re.compile(r"^<!--\s*entry_id:([A-Za-z0-9_.\-]+)\s*-->$")
_ADDENDUM_START_RE = re.compile(r"^<!--\s*addendum_for:([A-Za-z0-9_.\-]+)\s*-->$")
_ADDENDUM_END = "<!-- /addendum -->"


def all_path():
    return os.path.join(Config.DATA_DIR, "transcripts", "ALL.md")


def banner_text():
    """نص البانر (06 §4) — رأس الأرشيف + رسائل تليجرام حسب TRANSCRIPT_HEADER_TARGET."""
    return ("🎙️ تفريغ الفويس المسجل (Verbatim Transcription):\n"
            "⚠️ تنبيه: تفريغ آلي قد يحتوي على كلمات سمعية غير دقيقة حسب النطق أو النبرة.\n"
            "───────────────")


def _header():
    return "# 📚 الأرشيف العام لكل التفريغات (append-only)\n\n" + banner_text() + "\n\n"


def _short_model(model):
    return (model or "?").replace("gemini-", "")


def _append_text(text):
    path = all_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        if new_file:
            f.write(_header())
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def _is_comment_line(ln):
    s = ln.strip()
    return s.startswith("<!--") and s.endswith("-->")


def _parse_block_with_addenda(block):
    """يفكّك كتلة مدخل ويستخلص هويته (إن وُجدت) وأي كتل إلحاق موجّهة لمعرّفات محددة."""
    lines = block.strip("\n").split("\n")
    meta = lines[0][len(_ENTRY_MARK):].strip()
    raw, polished, title, cur = [], [], [], None
    entry_id = None
    addenda = {}
    add_target = None
    add_cur = None

    for ln in lines[1:]:
        stripped = ln.strip()
        m_add = _ADDENDUM_START_RE.match(stripped)
        if m_add:
            add_target = m_add.group(1)
            add_cur = None
            cur = None
            continue
        if stripped == _ADDENDUM_END:
            add_target = None
            add_cur = None
            cur = None
            continue
        if add_target is not None:
            bucket = addenda.setdefault(add_target, {"polished": [], "title": []})
            if ln.startswith(_POL_MARK):
                add_cur = bucket["polished"]
            elif ln.startswith(_TITLE_MARK):
                add_cur = bucket["title"]
            elif ln.startswith("### "):
                add_cur = None
            elif add_cur is not None and not _is_comment_line(ln):
                add_cur.append(ln)
            continue

        m_id = _ENTRY_ID_RE.match(stripped)
        if m_id and entry_id is None:
            entry_id = m_id.group(1)
            continue
        if _is_comment_line(ln):
            continue
        if ln.startswith(_RAW_MARK):
            cur = raw
        elif ln.startswith(_POL_MARK):
            cur = polished
        elif ln.startswith(_TITLE_MARK):
            cur = title
        elif ln.startswith("### "):
            cur = None
        elif cur is not None:
            cur.append(ln)

    entry = {
        "meta": meta,
        "raw": "\n".join(raw).strip(),
        "polished": "\n".join(polished).strip() or None,
        "title": "\n".join(title).strip() or None,
    }
    parsed_addenda = {}
    for tid, b in addenda.items():
        p_txt = "\n".join(b["polished"]).strip() or None
        t_txt = "\n".join(b["title"]).strip() or None
        if p_txt or t_txt:
            parsed_addenda[tid] = {"polished": p_txt, "title": t_txt}
    return entry, entry_id, parsed_addenda


def _merge_addenda_into_entry(entry, eid, all_addenda):
    if not eid or eid not in all_addenda:
        return entry
    ad = all_addenda[eid]
    if not entry.get("polished") and ad.get("polished"):
        entry["polished"] = ad["polished"]
    if not entry.get("title") and ad.get("title"):
        entry["title"] = ad["title"]
    return entry


def append(raw_text, model=None, elapsed=None, polished=None, session_name=None, key_name=None, when=None, extra=None, entry_id=None):
    """
    يضيف مدخلاً إلى ALL.md. يرجع True عند النجاح. لا يرفع استثناءً أبداً (الأرشفة لا تكسر التفريغ).
    raw_text: الخام بعد قاموس scope=both/raw_only فقط (أمانة النص).
    """
    try:
        stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
        meta = [stamp, f"جلسة: «{session_name}»" if session_name else "بدون جلسة", _short_model(model)]
        if elapsed is not None:
            meta.append(f"{elapsed}ث")
        if key_name:
            meta.append(f"🔑 {key_name}")
        if extra:
            meta.append(str(extra))
        id_line = f"<!-- entry_id:{entry_id} -->\n" if entry_id else ""
        block = _ENTRY_MARK + " · ".join(meta) + "\n" + id_line + _RAW_MARK + "\n" + (raw_text or "").strip() + "\n"
        if polished:
            block += _POL_MARK + "\n" + polished.strip() + "\n"
        block += "\n"
        with _lock:
            _append_text(block)
        return True
    except Exception as e:
        print(f"⚠️ الأرشفة: تعذّر الكتابة في ALL.md ({type(e).__name__}: {e})")
        return False


def _find_entry_state(entry_id):
    """يبحث في ALL.md عن حالة المدخل ذي entry_id وما أُلحق به. يرجع (found, has_polished, has_title)."""
    path = all_path()
    if not os.path.exists(path):
        return False, False, False
    marker = f"<!-- entry_id:{entry_id} -->"
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = f.read()
    pos = data.rfind(marker)
    if pos == -1:
        return False, False, False
    head_pos = data.rfind(_ENTRY_MARK, 0, pos)
    if head_pos == -1:
        return False, False, False
    tail_data = data[head_pos:]
    parts = _ENTRY_RE.split(tail_data)
    target_ent, _tid, add_first = _parse_block_with_addenda(_ENTRY_MARK + parts[1])
    merged_add = dict(add_first.get(entry_id, {}))
    for p in parts[2:]:
        _e, _eid, ads = _parse_block_with_addenda(_ENTRY_MARK + p)
        if entry_id in ads:
            for k in ("polished", "title"):
                if not merged_add.get(k) and ads[entry_id].get(k):
                    merged_add[k] = ads[entry_id][k]
    has_pol = bool(target_ent.get("polished") or merged_add.get("polished"))
    has_tit = bool(target_ent.get("title") or merged_add.get("title"))
    return True, has_pol, has_tit


def _append_section_to_entry(mark, body_text, field_key, entry_id=None):
    clean_body = (body_text or "").strip()
    if not clean_body:
        return False
    with _lock:
        block = _read_last_block()
        if block is None:
            return False
        ent, last_eid, addenda = _parse_block_with_addenda(block)
        if entry_id is None or last_eid == entry_id:
            merged = _merge_addenda_into_entry(dict(ent), last_eid, addenda)
            if merged.get(field_key):
                return False
            # لو كان ذيل الكتلة الأخيرة يحوي addenda أجنبية لمدخل أسبق، نعزل الإضافة لـ last_eid أو نستخدم addendum صريحاً
            foreign = any(tid != last_eid for tid in addenda)
            if foreign and entry_id:
                _append_text(f"<!-- addendum_for:{entry_id} -->\n{mark}\n{clean_body}\n{_ADDENDUM_END}\n\n")
            else:
                _append_text(mark + "\n" + clean_body + "\n\n")
            return True

        found, has_pol, has_tit = _find_entry_state(entry_id)
        if not found:
            return False
        if (field_key == "polished" and has_pol) or (field_key == "title" and has_tit):
            return False
        _append_text(f"<!-- addendum_for:{entry_id} -->\n{mark}\n{clean_body}\n{_ADDENDUM_END}\n\n")
        return True


def append_polished(polished_text, entry_id=None):
    """يلحق «### المنظم» بالمدخل المستهدف (أو الأخير) مع حفظ append-only ومنع التداخل تحت التزامن."""
    if not polished_text:
        return False
    try:
        return _append_section_to_entry(_POL_MARK, polished_text, "polished", entry_id=entry_id)
    except Exception as e:
        print(f"⚠️ الأرشفة: تعذّر إلحاق المنظم ({e})")
        return False


def append_title(title, entry_id=None):
    """المرحلة 4 (08 §3): يلحق «### العنوان» بالمدخل المستهدف (أو الأخير). بلا تكرار."""
    title = (title or "").strip()
    if not title:
        return False
    try:
        return _append_section_to_entry(_TITLE_MARK, title, "title", entry_id=entry_id)
    except Exception as e:  # pragma: no cover
        print(f"⚠️ الأرشفة: تعذّر إلحاق العنوان ({e})")
        return False


def _read_tail_parts(path, tail_bytes, min_parts=2):
    size = os.path.getsize(path)
    if size == 0:
        return []
    cur_tail = max(1024, int(tail_bytes))
    with open(path, "rb") as f:
        while True:
            seek_pos = max(0, size - cur_tail)
            f.seek(seek_pos)
            data = f.read().decode("utf-8", errors="ignore")
            parts = _ENTRY_RE.split(data)
            if len(parts) >= min_parts or seek_pos == 0:
                return parts
            cur_tail = min(size, cur_tail * 2)


def _read_last_block(tail_bytes=65536):
    path = all_path()
    if not os.path.exists(path):
        return None
    parts = _read_tail_parts(path, tail_bytes, min_parts=2)
    if len(parts) < 2:
        return None
    return _ENTRY_MARK + parts[-1]


def last():
    """آخر مدخل {"meta", "raw", "polished", "title"} أو None. يقرأ ذيل الملف فقط."""
    with _lock:
        block = _read_last_block()
    if not block:
        return None
    entry, eid, addenda = _parse_block_with_addenda(block)
    return _merge_addenda_into_entry(entry, eid, addenda)


def last_entries(n=10, tail_bytes=262144):
    """المرحلة 7 (12 §1): آخر n مدخلات [{meta, raw, polished, title}] من ذيل ALL.md — بلا تحميل الملف كاملاً."""
    path = all_path()
    if not os.path.exists(path) or n <= 0:
        return []
    with _lock:
        parts = _read_tail_parts(path, tail_bytes, min_parts=n + 1)
    parsed = []
    all_addenda = {}
    for p in parts[1:]:
        ent, eid, add_map = _parse_block_with_addenda(_ENTRY_MARK + p)
        parsed.append((ent, eid))
        for tid, fields in add_map.items():
            bucket = all_addenda.setdefault(tid, {})
            for k, v in fields.items():
                if v and not bucket.get(k):
                    bucket[k] = v
    out = [_merge_addenda_into_entry(ent, eid, all_addenda) for ent, eid in parsed[-n:]]
    return out


def count_entries():
    """عدد المدخلات (للعرض في /status) — مسح سطري بلا تحميل الملف كاملاً."""
    path = all_path()
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for ln in f if ln.startswith(_ENTRY_MARK))


def find(word, limit=5):
    """بحث نصي بسيط (08 §5 /find): أول limit مدخلات تحوي الكلمة، مع التاريخ وسطر السياق."""
    word = (word or "").strip()
    path = all_path()
    if not word or not os.path.exists(path):
        return []
    hits, meta, wl = [], None, word.lower()
    in_addendum = False
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for ln in f:
            stripped = ln.strip()
            if _ADDENDUM_START_RE.match(stripped):
                in_addendum = True
                continue
            if stripped == _ADDENDUM_END:
                in_addendum = False
                continue
            if ln.startswith(_ENTRY_MARK):
                meta = ln[len(_ENTRY_MARK):].strip()
                in_addendum = False
            elif meta and not in_addendum and not ln.startswith("### ") and not _is_comment_line(ln) and wl in ln.lower():
                hits.append({"meta": meta, "line": ln.strip()[:200]})
                meta = None   # مدخل واحد = نتيجة واحدة
                if len(hits) >= limit:
                    break
    return hits
