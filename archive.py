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
_RAW_MARK, _POL_MARK = "### الخام", "### المنظم"


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


def append(raw_text, model=None, elapsed=None, polished=None, session_name=None, key_name=None, when=None, extra=None):
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
        block = _ENTRY_MARK + " · ".join(meta) + "\n" + _RAW_MARK + "\n" + (raw_text or "").strip() + "\n"
        if polished:
            block += _POL_MARK + "\n" + polished.strip() + "\n"
        block += "\n"
        with _lock:
            _append_text(block)
        return True
    except Exception as e:
        print(f"⚠️ الأرشفة: تعذّر الكتابة في ALL.md ({type(e).__name__}: {e})")
        return False


def append_polished(polished_text):
    """يلحق «### المنظم» بآخر مدخل (المنظم يصل بعد الخام بثوانٍ — 07 §1). يرجع True عند النجاح."""
    if not polished_text:
        return False
    try:
        with _lock:
            block = _read_last_block()
            if block is None or _POL_MARK in block:
                return False
            _append_text(_POL_MARK + "\n" + polished_text.strip() + "\n\n")
        return True
    except Exception as e:
        print(f"⚠️ الأرشفة: تعذّر إلحاق المنظم ({e})")
        return False


def _read_last_block(tail_bytes=65536):
    path = all_path()
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


def last():
    """آخر مدخل {"meta", "raw", "polished"} أو None. يقرأ ذيل الملف فقط."""
    with _lock:
        block = _read_last_block()
    if not block:
        return None
    lines = block.strip("\n").split("\n")
    meta = lines[0][len(_ENTRY_MARK):].strip()
    raw, polished, cur = [], [], None
    for ln in lines[1:]:
        if ln.startswith(_RAW_MARK):
            cur = raw
        elif ln.startswith(_POL_MARK):
            cur = polished
        elif cur is not None:
            cur.append(ln)
    return {"meta": meta, "raw": "\n".join(raw).strip(), "polished": "\n".join(polished).strip() or None}


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
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for ln in f:
            if ln.startswith(_ENTRY_MARK):
                meta = ln[len(_ENTRY_MARK):].strip()
            elif meta and not ln.startswith("### ") and wl in ln.lower():
                hits.append({"meta": meta, "line": ln.strip()[:200]})
                meta = None   # مدخل واحد = نتيجة واحدة
                if len(hits) >= limit:
                    break
    return hits
