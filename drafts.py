# -*- coding: utf-8 -*-
"""
drafts.py — حفظ المسودات المنظمة آلياً على القرص وتطهير القديم منها (T4 — DEC-P2-04 / DEC-P2-09).
- يكتب المسودات بصيغة عامة تدعم أي عدد من المسودات الوسيطة بالإضافة للنص النهائي.
- فشل الكتابة أو التطهير لا يرفع استثناءً لضمان عدم تعطيل التسليم للمستخدم.
- يطهر المجلدات الأقدم من DRAFTS_KEEP_DAYS مرة واحدة في اليوم.
"""

from datetime import datetime, timezone, timedelta
import logging
import os
import uuid

from config import Config
import store

logger = logging.getLogger("alsami.drafts")
_last_prune_date = None


def write(entry_id, final_text=None, drafts=None, meta=None, date_str=None):
    """
    يحفظ المسودة النهائية والمسودات الوسيطة ذرياً في:
    DATA_DIR/drafts/YYYY-MM-DD/<entry_id>.md
    """
    now = datetime.now(timezone.utc)
    day_folder = date_str or now.strftime("%Y-%m-%d")

    eid = str(entry_id or "").strip()
    if not eid:
        eid = uuid.uuid4().hex[:8]
        logger.debug("⚠️ drafts.write: entry_id فارغ، تم توليد معرّف بديل %s", eid)

    lines = [f"# {eid} · {now.isoformat()}"]
    if meta and isinstance(meta, dict):
        title = meta.get("title")
        session = meta.get("session_name")
        if title or session:
            info = " | ".join(filter(None, [f"العنوان: {title}" if title else None, f"الجلسة: {session}" if session else None]))
            lines.append(f"> {info}\n")

    lines.append("## ✅ النهائي")
    if final_text:
        lines.append(str(final_text).strip())
    else:
        lines.append("*(لا يوجد نص نهائي — تعذر السبك أو فشلت المعالجة)*")

    if drafts:
        lines.append("\n## 📋 المسودات الأولية")
        for item in drafts:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                label, txt = item
                lines.append(f"\n### ⚡ {label}\n{str(txt or '').strip()}")

    body = "\n".join(lines) + "\n"
    target_dir = os.path.join(getattr(Config, "DATA_DIR", "data"), "drafts", day_folder)
    target_path = os.path.join(target_dir, f"{eid}.md")

    try:
        store.atomic_write_text(target_path, body)
    except Exception as e:
        logger.warning("⚠️ تعذر حفظ المسودة %s: %s", target_path, e)
        return None

    # محاولة التطهير الدوري في try/except منفصل لعدم حجب المسودة
    try:
        prune_old()
    except Exception as e:
        logger.debug("⚠️ فشل التطهير الدوري للمسودات: %s", e)

    return target_path


def prune_old(keep_days=None):
    """يطهر المجلدات الأقدم من keep_days (افتراضياً Config.DRAFTS_KEEP_DAYS) مرة في اليوم."""
    global _last_prune_date
    today = datetime.now(timezone.utc).date()
    if _last_prune_date == today:
        return 0
    _last_prune_date = today

    days = getattr(Config, "DRAFTS_KEEP_DAYS", 30) if keep_days is None else keep_days
    if days is None or days <= 0:
        return 0

    drafts_root = os.path.join(getattr(Config, "DATA_DIR", "data"), "drafts")
    if not os.path.isdir(drafts_root):
        return 0

    cutoff = today - timedelta(days=days)
    deleted_folders = 0
    for name in os.listdir(drafts_root):
        folder_path = os.path.join(drafts_root, name)
        if not os.path.isdir(folder_path):
            continue
        try:
            folder_date = datetime.strptime(name, "%Y-%m-%d").date()
            if folder_date < cutoff:
                import shutil
                shutil.rmtree(folder_path, ignore_errors=True)
                deleted_folders += 1
        except ValueError:
            continue
    return deleted_folders

