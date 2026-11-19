# -*- coding: utf-8 -*-
"""
memory.py — محرك الذاكرة السلبي وعزل المواضيع (T8 — DEC-P2-03/04).

- Passive File-Over-App: صفر أوامر سلاش وصفر أزرار تفاعلية.
- MEMORY.md: ثوابت المالك (للقراءة فقط، منع تلوث الذاكرة Zero-Poisoning).
- daily/YYYY-MM-DD.md: فهرس يومي مختصر (سطر واحد لكل فويس).
- context/<topic>.md: سياق متحرك مضغوط (Rolling FIFO).
- review/YYYY-WW.md: تقرير مراجعة أسبوعي سلبي يُكتب أول فويس في كل أسبوع جديد.
"""

import os
import re
import time
import datetime
import threading
import logging
from typing import Optional, Tuple, List, Dict

from config import Config

logger = logging.getLogger("memory")

DEFAULT_TOPICS = ["coding", "family", "friends", "education", "religion", "notes"]

_lock = threading.RLock()
_cached_owner_ctx: Optional[str] = None
_cached_owner_sig = None
_last_check_mono: float = 0.0


def reset_state():
    """إعادة ضبط حالة الكاش والمراقب (للاختبارات)."""
    global _cached_owner_ctx, _cached_owner_sig, _last_check_mono
    with _lock:
        _cached_owner_ctx = None
        _cached_owner_sig = None
        _last_check_mono = 0.0


def get_owner_context(memory_cfg: Optional[dict] = None, force: bool = False) -> Optional[str]:
    """
    يقرأ ثوابت المالك من DATA_DIR/memory/MEMORY.md (T8 — DEC-P2-03).
    - تحميل كسول آمن بمتابعة بصمة (st_mtime_ns, st_size) وفاصل تهدئة >= 5 ثوانٍ.
    - قص محكم: أول inject_max_lines سطراً (افتراضي 200)، ثم inject_max_bytes بايت UTF-8 (افتراضي 25600).
    - إرجاع None إذا كانت الذاكرة معطلة أو الملف غير موجود أو فارغاً.
    """
    global _cached_owner_ctx, _cached_owner_sig, _last_check_mono

    cfg = memory_cfg or {}
    if not cfg.get("enabled", False):
        return None

    mem_dir = getattr(Config, "MEMORY_DIR", os.path.join(Config.DATA_DIR, "memory"))
    path = getattr(Config, "MEMORY_FILE", "") or os.path.join(mem_dir, "MEMORY.md")

    with _lock:
        now_mono = time.monotonic()
        if not force and _cached_owner_ctx is not None and (now_mono - _last_check_mono < 5.0):
            return _cached_owner_ctx

        _last_check_mono = now_mono
        try:
            st = os.stat(path)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            _cached_owner_ctx = None
            _cached_owner_sig = None
            return None

        if sig == _cached_owner_sig and _cached_owner_ctx is not None and not force:
            return _cached_owner_ctx

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except OSError as e:
            logger.warning("فشل قراءة MEMORY.md: %s", e)
            return None

        if not raw_text.strip():
            _cached_owner_ctx = None
            _cached_owner_sig = sig
            return None

        # 1. القص بالسطور أولاً
        max_lines = int(cfg.get("inject_max_lines", 200) or 200)
        lines = raw_text.splitlines()
        clipped_lines = "\n".join(lines[:max_lines])

        # 2. القص بالبايت ثانياً (UTF-8 bytes)
        max_bytes = int(cfg.get("inject_max_bytes", 25600) or 25600)
        raw_bytes = clipped_lines.encode("utf-8")[:max_bytes]
        clipped_text = raw_bytes.decode("utf-8", errors="ignore").strip()

        _cached_owner_ctx = clipped_text if clipped_text else None
        _cached_owner_sig = sig
        return _cached_owner_ctx


def extract_and_strip_topic(text: str, allowed_topics: Optional[List[str]] = None) -> Tuple[str, str]:
    """
    يستخرج ويجرد وسم #topic من النص (T8 — DEC-P2-03).
    - يبحث عن #topic: <name>.
    - يجرد السطر تماماً من النص قبل العرض والمسودات والحراس والقاموس.
    - يتحقق من انتمائه للقائمة المسموحة، وإلا يرتد بأمان إلى 'notes'.
    """
    if not text:
        return "", "notes"

    allowed = [t.lower().strip() for t in (allowed_topics or DEFAULT_TOPICS)]
    match = re.search(r'#topic:\s*([a-zA-Z0-9_\u0600-\u06FF]+)', text, flags=re.IGNORECASE)

    clean_text = text
    if match:
        found_raw = match.group(1).lower().strip()
        topic = found_raw if found_raw in allowed else "notes"
        # حذف السطر بالكامل بما فيه السطر الجديد
        clean_text = re.sub(r'(?m)^[ \t]*#topic:[ \t]*[^\r\n]*\r?\n?', '', text, flags=re.IGNORECASE)
        # مسح أي بقايا في حال لم يكن في سطر منفصل
        clean_text = re.sub(r'#topic:[ \t]*[^\r\n]*', '', clean_text, flags=re.IGNORECASE).strip()
    else:
        topic = "notes"

    return clean_text.strip(), topic


def get_topic_context(topic: str, memory_cfg: Optional[dict] = None) -> Optional[str]:
    """
    يقرأ السياق المتحرك المضغوط للموضوع المحدد من context/<topic>.md (T8).
    سقف الحروف الافتراضي context_max_chars (800 حرف).
    """
    cfg = memory_cfg or {}
    if not cfg.get("enabled", False):
        return None

    mem_dir = getattr(Config, "MEMORY_DIR", os.path.join(Config.DATA_DIR, "memory"))
    safe_topic = re.sub(r'[^a-zA-Z0-9_]', '_', str(topic or "notes").lower())
    ctx_path = os.path.join(mem_dir, "context", f"{safe_topic}.md")

    if not os.path.exists(ctx_path):
        return None

    try:
        with open(ctx_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        return None

    if not content:
        return None

    max_chars = int(cfg.get("context_max_chars", 800) or 800)
    return content[:max_chars].strip()


def append_daily(entry_id: str, topic: str, golden_preview: str, timestamp: Optional[float] = None, memory_cfg: Optional[dict] = None):
    """
    يسجل سطراً واحداً في فهرس السجل اليومي daily/YYYY-MM-DD.md (T8):
    HH:MM · #topic · <entry_id> · <أول 80 حرف من الذهبية>
    """
    cfg = memory_cfg or {}
    if not cfg.get("enabled", False):
        return

    mem_dir = getattr(Config, "MEMORY_DIR", os.path.join(Config.DATA_DIR, "memory"))
    daily_dir = os.path.join(mem_dir, "daily")
    try:
        os.makedirs(daily_dir, exist_ok=True)
    except OSError:
        pass

    now = datetime.datetime.fromtimestamp(timestamp) if timestamp else datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    daily_file = os.path.join(daily_dir, f"{date_str}.md")

    clean_snippet = " ".join(str(golden_preview or "").split())[:80]
    line = f"{time_str} · #{topic} · {entry_id} · {clean_snippet}\n"

    with _lock:
        try:
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logger.warning("فشل الكتابة في daily/%s.md: %s", date_str, e)


def append_guard_warning(word: str, timestamp: Optional[float] = None, memory_cfg: Optional[dict] = None):
    """
    يسجل تنبيهاً حتمياً للألفاظ المستحدثة في فهرس daily/YYYY-MM-DD.md (T8-fix — DEC-P2-03):
    ⚠️ · guard · <word>
    """
    cfg = memory_cfg or {}
    if not cfg.get("enabled", False):
        return

    w_clean = str(word or "").strip()
    if not w_clean:
        return

    mem_dir = getattr(Config, "MEMORY_DIR", os.path.join(Config.DATA_DIR, "memory"))
    daily_dir = os.path.join(mem_dir, "daily")
    try:
        os.makedirs(daily_dir, exist_ok=True)
    except OSError:
        pass

    now = datetime.datetime.fromtimestamp(timestamp) if timestamp else datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    daily_file = os.path.join(daily_dir, f"{date_str}.md")
    line = f"⚠️ · guard · {w_clean}\n"

    with _lock:
        try:
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            logger.warning("فشل تسجيل تحذير الحارس في daily/%s.md: %s", date_str, e)


def update_topic_context(topic: str, golden_text: str, memory_cfg: Optional[dict] = None):
    """
    يحدّث السياق المتحرك context/<topic>.md (Rolling FIFO — T8/T8-fix):
    - يقتطع ملخصاً بطول (context_max_chars // context_per_topic_n).
    - يحتفظ بآخر n مقتطفات، ويسقط سطوراً كاملة من البداية عند تجاوز السقف دون بتر الكلمات.
    """
    cfg = memory_cfg or {}
    if not cfg.get("enabled", False):
        return

    mem_dir = getattr(Config, "MEMORY_DIR", os.path.join(Config.DATA_DIR, "memory"))
    safe_topic = re.sub(r'[^a-zA-Z0-9_]', '_', str(topic or "notes").lower())
    ctx_dir = os.path.join(mem_dir, "context")
    try:
        os.makedirs(ctx_dir, exist_ok=True)
    except OSError:
        pass

    ctx_file = os.path.join(ctx_dir, f"{safe_topic}.md")
    n = int(cfg.get("context_per_topic_n", 3) or 3)
    max_chars = int(cfg.get("context_max_chars", 800) or 800)
    chunk_size = max(50, max_chars // max(1, n))

    snippet = " ".join(str(golden_text or "").split())[:chunk_size].strip()
    if not snippet:
        return

    with _lock:
        existing = []
        if os.path.exists(ctx_file):
            try:
                with open(ctx_file, "r", encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if ln:
                            existing.append(ln if ln.startswith("- ") else f"- {ln}")
            except OSError:
                existing = []

        existing.append(f"- {snippet}")
        kept = existing[-n:]

        # إسقاط سطور كاملة من البداية حتى لا تتجاوز الحروف السقف المحدد (T8-fix ثغرة 3)
        while kept and sum(len(x) + 1 for x in kept) > max_chars:
            if len(kept) == 1:
                # لو كان السطر الوحيد المتبقي أطول من السقف، يُقَص عند أقرب كلمة
                first_clipped = kept[0][:max_chars]
                kept[0] = first_clipped.rsplit(" ", 1)[0] if " " in first_clipped else first_clipped
                break
            kept.pop(0)

        content = "\n".join(kept)

        try:
            with open(ctx_file, "w", encoding="utf-8") as f:
                f.write(content + "\n")
        except OSError as e:
            logger.warning("فشل تحديث context/%s.md: %s", safe_topic, e)


def maybe_write_weekly_review(
    glossary=None,
    guard_warnings: Optional[List[str]] = None,
    memory_cfg: Optional[dict] = None,
    now: Optional[datetime.datetime] = None,
) -> Optional[str]:
    """
    يولّد تقرير المراجعة الأسبوعي review/YYYY-WW.md للأسبوع السابق فقط (T8/T8-fix).
    - يعمل سلبياً مرة واحدة عند أول فويس في أسبوع جديد (إذا غاب فويس لأسبوعين يلخص الأسبوع المباشر السابق).
    - يفلتر ملفات daily الصادرة في الأسبوع المنصرم فقط لمنع تضخم الإحصائيات التراكمية.
    - المصادر (حتمية بلا LLM):
      1. إحصائيات #topic من سجلات daily الخاصة بالأسبوع المنصرم حصراً.
      2. تكرارات تنبيهات الحارس الحتمي المسجلة في daily على مدار الأسبوع.
      3. مدخلات القاموس المعلقة (status == 'pending').
    """
    cfg = memory_cfg or {}
    if not cfg.get("enabled", False):
        return None

    mem_dir = getattr(Config, "MEMORY_DIR", os.path.join(Config.DATA_DIR, "memory"))
    review_dir = os.path.join(mem_dir, "review")
    try:
        os.makedirs(review_dir, exist_ok=True)
    except OSError:
        pass

    now_dt = now or datetime.datetime.now()
    # تحديد الأسبوع السابق
    prev_week_dt = now_dt - datetime.timedelta(days=7)
    prev_week_str = prev_week_dt.strftime("%Y-W%W")
    rev_path = os.path.join(review_dir, f"{prev_week_str}.md")

    with _lock:
        if os.path.exists(rev_path):
            return None  # تم إنشاؤه مسبقاً لهذا الأسبوع

        # 1. إحصائيات المواضيع وتنبيهات الحارس من ملفات daily الخاصة بالأسبوع السابق فقط (T8-fix ثغرة 1 و 2)
        topic_counts: Dict[str, int] = {}
        guard_counts: Dict[str, int] = {}
        daily_dir = os.path.join(mem_dir, "daily")
        if os.path.exists(daily_dir):
            try:
                for fname in sorted(os.listdir(daily_dir)):
                    m = re.match(r"^(\d{4}-\d{2}-\d{2})\.md$", fname)
                    if not m:
                        continue
                    try:
                        f_date = datetime.date.fromisoformat(m.group(1))
                        if f_date.strftime("%Y-W%W") != prev_week_str:
                            continue
                    except ValueError:
                        continue

                    fpath = os.path.join(daily_dir, fname)
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for ln in f:
                            ln_strip = ln.strip()
                            if ln_strip.startswith("⚠️ · guard ·"):
                                g_word = ln_strip.split("·")[-1].strip()
                                if g_word:
                                    guard_counts[g_word] = guard_counts.get(g_word, 0) + 1
                                continue

                            parts = ln.split("·")
                            if len(parts) >= 2:
                                t_part = parts[1].strip()
                                if t_part.startswith("#"):
                                    t_name = t_part[1:].strip()
                                    topic_counts[t_name] = topic_counts.get(t_name, 0) + 1
            except Exception as e_scan:
                logger.warning("خطأ في فحص مجلد daily للتقرير الأسبوعي: %s", e_scan)

        # 2. مصطلحات القاموس المعلقة
        pending_entries = []
        if glossary is not None:
            try:
                ents = glossary.entries() if callable(getattr(glossary, "entries", None)) else getattr(glossary, "entries", [])
                for it in ents:
                    if it.get("status") == "pending":
                        pending_entries.append(it)
            except Exception:
                pass

        lines = [
            f"# 📋 تقرير المراجعة الأسبوعي — أسبوع {prev_week_str}",
            "",
            "## 📊 توزيع المواضيع المسجلة:",
        ]
        if topic_counts:
            for top, cnt in sorted(topic_counts.items(), key=lambda x: -x[1]):
                lines.append(f"- `#{top}`: {cnt} تسجيل")
        else:
            lines.append("- لا توجد تسجيلات مرصودة للأسبوع المنصرم.")

        lines.extend([
            "",
            "## 📝 مصطلحات القاموس المعلقة (Pending):",
        ])
        if pending_entries:
            for p in pending_entries[:30]:
                pats = ", ".join(p.get("patterns", []))
                rep = p.get("replacement", "")
                hits = p.get("hits", 0)
                lines.append(f"- `{pats}` ➔ `{rep}` (تكرار: {hits})")
        else:
            lines.append("- لا توجد مصطلحات معلقة بانتظار التأكيد.")

        lines.extend([
            "",
            "## ⚠️ تنبيهات الحارس الحتمي للألفاظ المستحدثة:",
        ])
        if guard_counts:
            for w, cnt in sorted(guard_counts.items(), key=lambda x: -x[1]):
                lines.append(f"- `{w}`: {cnt} مرات")
        elif guard_warnings:
            for w in sorted(set(guard_warnings)):
                lines.append(f"- `{w}`")
        else:
            lines.append("- لا توجد ألفاظ غير موثقة.")

        doc = "\n".join(lines) + "\n"
        try:
            with open(rev_path, "w", encoding="utf-8") as f:
                f.write(doc)
            return rev_path
        except OSError as e_w:
            logger.warning("فشل كتابة review/%s.md: %s", prev_week_str, e_w)
            return None
