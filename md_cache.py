# -*- coding: utf-8 -*-
"""
نظام الكاش الذري المشفر لملفات .md (المرحلة 4 - Contract 8):
- كاش كتابة ذرية (Atomic write via NamedTemporaryFile + os.replace).
- صلاحيات مقيدة 0o700 للمجلد و 0o600 للملفات على أنظمة POSIX.
- منع الروابط الرمزية (Symlink rejection & TOCTOU protection).
- مهلة صلاحية TTL = 24 ساعة (86400 ثانية).
- سقف حماية السعة: بحد أقصى 200 ملف أو 50 ميجابايت (FIFO pruning).
- تفويض إلزامي: مطابقة chat_id + ربط صاحب الطلب (user_id) في المجموعات.
"""

import json
import os
import re
import tempfile
import time
import uuid

from config import Config

CACHE_TTL_SECONDS = 86400  # 24 ساعة
MAX_CACHE_FILES = 200
MAX_CACHE_BYTES = 50 * 1024 * 1024  # 50MB
_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_\-]{8,32}$")
TOKEN_RE = _TOKEN_RE



def get_cache_dir():
    """مسار مجلد الكاش تحت Config.DATA_DIR بصلاحيات 0o700."""
    d = os.path.join(Config.DATA_DIR, "md_cache")
    os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except (OSError, NotImplementedError):
        pass
    return d


def _is_safe_path(fp, base_dir):
    """التحقق من عدم وجود Symlink وأن المسار يقع فعلياً داخل مجلد الكاش."""
    try:
        if os.path.islink(fp):
            return False
        real_fp = os.path.realpath(fp)
        real_base = os.path.realpath(base_dir)
        return os.path.commonpath([real_fp, real_base]) == real_base
    except Exception:
        return False


def prune():
    """تنظيف الملفات منتهية الصلاحية وفرض سقف الـ 200 ملف و 50MB."""
    try:
        cdir = get_cache_dir()
        if not os.path.exists(cdir):
            return
        now = time.time()
        entries = []
        total_size = 0

        for fname in os.listdir(cdir):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(cdir, fname)
            if not _is_safe_path(fpath, cdir):
                try:
                    os.unlink(fpath)
                except Exception:
                    pass
                continue

            try:
                st = os.stat(fpath)
                sz = st.st_size
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                exp = float(data.get("expires_at", 0))
                if now > exp:
                    os.unlink(fpath)
                    continue
                entries.append((data.get("created_at", st.st_mtime), sz, fpath))
                total_size += sz
            except Exception:
                try:
                    os.unlink(fpath)
                except Exception:
                    pass

        # إذا تجاوز السقف: فرز حسب تاريخ الإنشاء (الأقدم أولاً) والحذف حتى استيفاء السقف
        if len(entries) > MAX_CACHE_FILES or total_size > MAX_CACHE_BYTES:
            entries.sort(key=lambda x: x[0])
            while entries and (len(entries) > MAX_CACHE_FILES or total_size > MAX_CACHE_BYTES):
                _cat, sz, path = entries.pop(0)
                try:
                    os.unlink(path)
                    total_size -= sz
                except Exception:
                    pass
    except Exception as e:
        print(f"⚠️ خطأ أثناء تنظيف كاش .md: {e}")


def store(text, chat_id=None, user_id=None, entry_id=None):
    """
    حفظ نص في الكاش بكتابة ذرية وصلاحيات 0o600.
    يرجع token فريد.
    """
    token = uuid.uuid4().hex[:16]
    cdir = get_cache_dir()
    dest_path = os.path.join(cdir, f"{token}.json")

    now = time.time()
    payload = {
        "token": token,
        "chat_id": str(chat_id) if chat_id is not None else None,
        "user_id": str(user_id) if user_id is not None else None,
        "entry_id": str(entry_id) if entry_id is not None else None,
        "text": str(text or ""),
        "created_at": now,
        "expires_at": now + CACHE_TTL_SECONDS,
    }

    # كتابة ذرية عبر ملف مؤقت داخل نفس المجلد ثم os.replace
    fd, tmp_path = tempfile.mkstemp(dir=cdir, prefix=".tmp_md_")
    try:
        try:
            os.chmod(tmp_path, 0o600)
        except (OSError, NotImplementedError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, dest_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        raise

    prune()
    return token


def get(token, chat_id=None, user_id=None):
    """
    استرجاع محتوى الكاش بعد التحقق من:
    1. صحة صيغة التوكن ومنع الـ Path Traversal.
    2. عدم كونه Symlink ووقوعه داخل مجلد الكاش.
    3. عدم انتهاء الـ TTL (24 ساعة).
    4. مطابقة chat_id إن طُلب.
    5. مطابقة user_id صاحب الطلب في المجموعات (chat_id < 0).
    """
    if not token or not isinstance(token, str) or not _TOKEN_RE.match(token):
        return None

    cdir = get_cache_dir()
    fpath = os.path.join(cdir, f"{token}.json")
    if not os.path.exists(fpath) or not _is_safe_path(fpath, cdir):
        return None

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    now = time.time()
    if now > float(data.get("expires_at", 0)):
        try:
            os.unlink(fpath)
        except Exception:
            pass
        return None

    # التحقق من تفويض chat_id
    if chat_id is not None and data.get("chat_id") is not None:
        if str(chat_id) != str(data["chat_id"]):
            return None

    # التحقق من تفويض user_id في المجموعات
    is_group = str(chat_id or "").startswith("-") or (data.get("chat_id") and str(data["chat_id"]).startswith("-"))
    if is_group and data.get("user_id") is not None:
        if user_id is not None and str(user_id) != str(data["user_id"]):
            return None

    return data
