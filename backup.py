# -*- coding: utf-8 -*-
"""
backup.py — بناء حزم النسخ الاحتياطي (09 §4، 13 S4، 17 المرحلة 0).

قاعدة أمان ثابتة: /backup **لا يحوي أبداً** keys.json ولا أي ملف أسرار.
الأسرار لها أمر منفصل /backup_secrets بتأكيد صريح (telegram_bot).

يُعيد bytes لملف ZIP في الذاكرة + قائمة بما دخل فيه، كي يُختبر بلا شبكة ولا قرص إضافي.
"""
import io
import os
import zipfile

from config import Config

# الملفات التي تُعدّ أسراراً ولا تدخل في /backup مهما كان موقعها
SECRET_BASENAMES = ("keys.json", "keys.txt", ".env")


def _is_secret(path):
    base = os.path.basename(path)
    return base in SECRET_BASENAMES or base.startswith(".keys-")


def data_roots():
    """المجلدات/الملفات غير السرية التي تدخل في النسخة العامة (ما وُجد منها فقط)."""
    d = Config.DATA_DIR
    candidates = [
        os.path.join(d, "transcripts"),
        os.path.join(d, "sessions"),
        os.path.join(d, "state"),
        os.path.join(d, "glossary.json"),
        os.path.join(d, "settings.json"),
        os.path.join(d, "roles.json"),
    ]
    return [c for c in candidates if os.path.exists(c)]


def build_zip(paths, exclude_secrets=True):
    """
    يضغط المسارات (ملفات أو مجلدات) في ZIP داخل الذاكرة.
    يعيد (zip_bytes, included: list[str]) حيث included أسماء الأعضاء داخل الأرشيف.
    """
    buf = io.BytesIO()
    included = []
    base_dir = Config.DATA_DIR

    def _arc(fp):
        # اسم داخل الأرشيف: نسبي إلى DATA_DIR؛ لو الملف خارجه (مثلاً keys.json في مجلد آخر) نستخدم اسمه فقط —
        # لا نكتب أبداً مسارات تبدأ بـ ../ داخل ZIP (zip-slip عند الفك).
        rel = os.path.relpath(fp, base_dir)
        if rel.startswith("..") or os.path.isabs(rel):
            return os.path.basename(fp)
        return rel.replace(os.sep, "/")
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            if os.path.isdir(p):
                for root, _dirs, files in os.walk(p):
                    for fn in sorted(files):
                        fp = os.path.join(root, fn)
                        if exclude_secrets and _is_secret(fp):
                            continue
                        arc = _arc(fp)
                        z.write(fp, arc)
                        included.append(arc)
            elif os.path.isfile(p):
                if exclude_secrets and _is_secret(p):
                    continue
                arc = _arc(p)
                z.write(p, arc)
                included.append(arc)
    return buf.getvalue(), included


def build_public_backup():
    """النسخة العامة: كل بيانات المالك غير السرية. لا keys.json أبداً."""
    return build_zip(data_roots(), exclude_secrets=True)


def build_secrets_backup():
    """نسخة الأسرار: keys.json فقط (إن وُجد). تُرسل فقط بعد تأكيد صريح."""
    paths = [Config.KEYS_JSON_FILE] if os.path.exists(Config.KEYS_JSON_FILE) else []
    return build_zip(paths, exclude_secrets=False)


def contains_secret(zip_bytes):
    """فحص دفاعي: هل يحوي الأرشيف أي ملف سري؟ يُستخدم في الاختبارات وقبل الإرسال."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        return any(_is_secret(n) for n in z.namelist())
