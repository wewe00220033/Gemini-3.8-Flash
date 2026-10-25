# -*- coding: utf-8 -*-
"""
store.py — أدوات التخزين المشتركة (المرحلة 0، المرجع: architecture-v2/13 §2).

القواعد الإلزامية لكل ملف حالة:
1. كتابة ذرية: tmp في نفس المجلد → flush → fsync → os.replace. لا يُترك الملف نصف مكتوب أبداً.
2. schema_version في رأس كل JSON مع دالة migrate لكل ملف (تبدأ no-op).
3. تحميل متسامح: ملف تالف → يُنقل إلى *.corrupt-<ts> ويُحمَّل الافتراضي. لا انهيار عند البدء أبداً.
4. append_line لملفات ndjson/md: فتح بوضع إلحاق + flush + fsync (طلب المالك: الحفظ اللحظي على القرص).

لا يعتمد على أي وحدة أخرى في المشروع كي يُستخدم من الجميع بلا دورات استيراد.
"""
import json
import os
import shutil
import tempfile
import threading
import time

_locks_guard = threading.Lock()
_path_locks = {}


def _lock_for(path):
    """قفل لكل مسار — كتابتان متزامنتان لنفس الملف من خيطين تتسلسلان."""
    key = os.path.abspath(path)
    with _locks_guard:
        lk = _path_locks.get(key)
        if lk is None:
            lk = threading.RLock()
            _path_locks[key] = lk
        return lk


def atomic_write_text(path, text, mode=0o600, fsync=True):
    """يكتب نصاً كاملاً ذرياً. mode=0o600 افتراضياً لأن معظم ملفاتنا تحوي أسراراً أو خصوصية."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    with _lock_for(path):
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                if fsync:
                    os.fsync(f.fileno())
            try:
                os.chmod(tmp, mode)
            except OSError:
                pass  # Windows — غير حرج
            os.replace(tmp, path)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            finally:
                raise


def atomic_write_json(path, data, schema_version=None, **kw):
    """يكتب JSON ذرياً. لو schema_version أُعطي يُثبَّت في الرأس (data يجب أن يكون dict)."""
    if schema_version is not None:
        if not isinstance(data, dict):
            raise TypeError("schema_version يتطلب dict")
        data = dict(data)
        data["schema_version"] = schema_version
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), **kw)


def quarantine_corrupt(path):
    """ينقل ملفاً تالفاً جانباً باسم زمني ويعيد المسار الجديد (أو None لو لم يوجد)."""
    if not os.path.exists(path):
        return None
    dest = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        shutil.move(path, dest)
    except OSError:
        return None
    return dest


def load_json_tolerant(path, default, schema_version=None, migrate=None, on_corrupt=None):
    """
    يحمّل JSON بتسامح:
    - غير موجود → default (نسخة).
    - تالف/ليس بالنوع المتوقع → يُعزل *.corrupt-<ts> + default + استدعاء on_corrupt(path, dest, err) إن وُجد.
    - schema_version أقل من المطلوب → migrate(data, from_version) إن وُجد، ثم تثبيت الرقم الجديد.
    لا يرفع استثناءً أبداً من أجل ملف سيئ — هذا شرط «لا انهيار عند البدء».
    """
    def _default():
        return json.loads(json.dumps(default))  # نسخة عميقة بسيطة

    if not os.path.exists(path):
        return _default()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if default is not None and not isinstance(data, type(default)):
            raise ValueError(f"نوع غير متوقع: {type(data).__name__}")
    except Exception as e:
        dest = quarantine_corrupt(path)
        if on_corrupt:
            try:
                on_corrupt(path, dest, e)
            except Exception:
                pass
        return _default()

    if schema_version is not None and isinstance(data, dict):
        have = int(data.get("schema_version") or 0)
        if have < schema_version:
            if migrate:
                try:
                    data = migrate(data, have) or data
                except Exception as e:
                    dest = quarantine_corrupt(path)
                    if on_corrupt:
                        try:
                            on_corrupt(path, dest, e)
                        except Exception:
                            pass
                    return _default()
            data["schema_version"] = schema_version
    return data


def append_line(path, line, fsync=True, mode=0o600):
    """
    إلحاق سطر واحد (ndjson / md) مع flush + fsync فوري [طلب المالك ج5].
    لا يُعاد كتابة الملف كاملاً أبداً (13 §2 قاعدة 4).
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    if not line.endswith("\n"):
        line += "\n"
    with _lock_for(path):
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        if new:
            try:
                os.chmod(path, mode)
            except OSError:
                pass


def append_json_line(path, obj, **kw):
    """سطر ndjson واحد (بدون indent، بدون أسطر داخلية)."""
    append_line(path, json.dumps(obj, ensure_ascii=False, separators=(",", ":")), **kw)


def read_json_lines(path, on_bad_line=None):
    """يقرأ ndjson متسامحاً: السطر التالف يُتخطى (مع on_bad_line(lineno, text, err) إن وُجد) — آخر سطر قد يكون مبتوراً بعد انقطاع."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for i, raw in enumerate(f, 1):
            s = raw.strip()
            if not s:
                continue
            try:
                out.append(json.loads(s))
            except Exception as e:
                if on_bad_line:
                    try:
                        on_bad_line(i, s, e)
                    except Exception:
                        pass
    return out


def probe_writable(directory):
    """
    فحص DATA_DIR (09 §6): هل المجلد موجود وقابل للكتابة فعلاً؟
    يعيد (ok: bool, detail: str). لا يرفع استثناءً.
    """
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".probe-", dir=directory)
        os.close(fd)
        os.remove(tmp)
        return True, "rw"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
