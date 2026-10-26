# -*- coding: utf-8 -*-
"""
backup.py — بناء حزم النسخ الاحتياطي (09 §4، 13 S4، 17 المرحلة 0).

قاعدة أمان ثابتة: /backup **لا يحوي أبداً** keys.json ولا أي ملف أسرار.
الأسرار لها أمر منفصل /backup_secrets بتأكيد صريح (telegram_bot).

يُعيد bytes لملف ZIP في الذاكرة + قائمة بما دخل فيه، كي يُختبر بلا شبكة ولا قرص إضافي.
"""
import datetime
import io
import json
import os
import shutil
import tempfile
import time
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


# ======================================================================
# المرحلة 5.1 — 09 §5: النسخ التلقائي الأسبوعي + نسخة محلية (آخر 4) + تقسيم > 50MB + /restore_test
# ======================================================================
_DAY_ALIASES = {
    "mon": 0, "monday": 0, "الاثنين": 0, "الإثنين": 0, "اثنين": 0,
    "tue": 1, "tuesday": 1, "الثلاثاء": 1, "ثلاثاء": 1,
    "wed": 2, "wednesday": 2, "الأربعاء": 2, "الاربعاء": 2, "أربعاء": 2,
    "thu": 3, "thursday": 3, "الخميس": 3, "خميس": 3,
    "fri": 4, "friday": 4, "الجمعة": 4, "جمعة": 4,
    "sat": 5, "saturday": 5, "السبت": 5, "سبت": 5,
    "sun": 6, "sunday": 6, "الأحد": 6, "الاحد": 6, "أحد": 6,
}
_DAY_NAMES_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def weekday_index(value=None):
    """يحوّل BACKUP_WEEKLY_DAY (mon..sun / 0..6 / اسم عربي) إلى 0..6 (الاثنين=0). غير معروف → الجمعة."""
    v = str(Config.BACKUP_WEEKLY_DAY if value is None else value).strip().lower()
    if v.isdigit() and 0 <= int(v) <= 6:
        return int(v)
    return _DAY_ALIASES.get(v, 4)


def weekday_name_ar(idx=None):
    return _DAY_NAMES_AR[weekday_index() if idx is None else idx]


def backups_dir():
    return Config.BACKUPS_DIR


def list_local(kind="backup"):
    """[(path, mtime)] من الأقدم للأحدث — ملفات backup_*.zip (أو backup_*_part*.zip) في backups/."""
    d = backups_dir()
    out = []
    try:
        for fn in os.listdir(d):
            if fn.startswith(kind + "_") and fn.endswith(".zip"):
                p = os.path.join(d, fn)
                try:
                    out.append((p, os.path.getmtime(p)))
                except OSError:
                    pass
    except OSError:
        return []
    out.sort(key=lambda x: (x[1], x[0]))
    return out


def prune_local(keep=None):
    """يبقي آخر keep نسخة (مجموعة الأجزاء تُعامل كنسخة واحدة بطابعها الزمني). يرجع عدد المحذوف."""
    keep = Config.BACKUPS_KEEP if keep is None else keep
    groups = {}
    for p, mt in list_local():
        stem = os.path.basename(p)[len("backup_"):-4]
        stem = stem.split("_part")[0]
        groups.setdefault(stem, []).append(p)
    stems = sorted(groups.keys())
    removed = 0
    for stem in stems[:-keep] if keep > 0 else stems:
        for p in groups[stem]:
            try:
                os.remove(p)
                removed += 1
            except OSError:
                pass
    return removed


def split_bytes(zip_bytes, part_mb=None):
    """> BACKUP_SPLIT_MB → أجزاء خام متتالية (تُجمَّع بـ cat/copy /b). ≤ الحد → قطعة واحدة."""
    limit = (Config.BACKUP_SPLIT_MB if part_mb is None else part_mb) * 1024 * 1024
    if len(zip_bytes) <= limit:
        return [zip_bytes]
    return [zip_bytes[i:i + limit] for i in range(0, len(zip_bytes), limit)]


def save_local(zip_bytes, when=None):
    """
    يكتب النسخة في backups/backup_YYYY-MM-DD_HHMM.zip (أو _partN.zip لو كبيرة) ثم يطبّق الاحتفاظ (آخر 4).
    يرجع (paths, removed_count).
    """
    d = backups_dir()
    os.makedirs(d, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M", time.localtime(when or time.time()))
    parts = split_bytes(zip_bytes)
    paths = []
    for i, chunk in enumerate(parts, 1):
        name = f"backup_{stamp}.zip" if len(parts) == 1 else f"backup_{stamp}_part{i}.zip"
        p = os.path.join(d, name)
        tmp = p + ".tmp"
        with open(tmp, "wb") as f:
            f.write(chunk)
        os.replace(tmp, p)
        paths.append(p)
    removed = prune_local()
    return paths, removed


# ---- جدولة أسبوعية (مرة في اليوم المحدد عند الساعة المحددة، بلا تكرار داخل نفس الأسبوع) ----
def _state_path():
    return Config.BACKUP_STATE_FILE


def load_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d):
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _state_path())
    except OSError:
        pass


def _week_key(t):
    """مفتاح أسبوع ISO بالتوقيت المحلي: 2026-W43."""
    lt = time.localtime(t)
    y, w, _d = datetime.date(lt.tm_year, lt.tm_mon, lt.tm_mday).isocalendar()
    return f"{y}-W{w:02d}"


def weekly_due(now=None):
    """
    هل حان موعد النسخة الأسبوعية؟ (09 §5): اليوم = BACKUP_WEEKLY_DAY، الساعة ≥ BACKUP_HOUR،
    ولم تُنفَّذ بعد في هذا الأسبوع (ISO). لو البوت كان مطفأً وقت الموعد وشُغّل لاحقاً في نفس اليوم → تُنفَّذ (catch-up).
    """
    if not Config.BACKUP_WEEKLY_ENABLED:
        return False
    now = time.time() if now is None else now
    lt = time.localtime(now)
    if lt.tm_wday != weekday_index() or lt.tm_hour < Config.BACKUP_HOUR:
        return False
    return load_state().get("last_week") != _week_key(now)


def mark_done(now=None, paths=None, ok=True, error=None):
    now = time.time() if now is None else now
    st = load_state()
    st.update({"last_week": _week_key(now), "last_ts": now, "last_ok": bool(ok),
               "last_paths": [os.path.basename(p) for p in (paths or [])], "last_error": error})
    _save_state(st)
    return st


def run_weekly(send_fn=None, now=None):
    """
    ينفّذ النسخة الأسبوعية: بناء → فحص الأسرار → حفظ محلي (+احتفاظ 4) → إرسال الأجزاء عبر send_fn(name, bytes, caption).
    يرجع dict نتيجة (بلا استثناءات) ويسجّل الحالة حتى لا تتكرر في نفس الأسبوع.
    """
    now = time.time() if now is None else now
    res = {"ok": False, "paths": [], "sent": 0, "removed": 0, "files": 0, "error": None}
    try:
        zip_bytes, included = build_public_backup()
        if contains_secret(zip_bytes):
            raise RuntimeError("النسخة العامة احتوت ملفاً سرياً — أُلغيت")
        if not included:
            res.update(ok=True, error="لا بيانات للنسخ بعد")
            mark_done(now, [], ok=True, error=res["error"])
            return res
        paths, removed = save_local(zip_bytes, when=now)
        res.update(paths=paths, removed=removed, files=len(included))
        if send_fn is not None:
            parts = split_bytes(zip_bytes)
            for i, chunk in enumerate(parts, 1):
                name = os.path.basename(paths[i - 1])
                cap = (f"🗂️ نسخة أسبوعية تلقائية — {len(included)} ملف، بلا أسرار."
                       + (f" جزء {i}/{len(parts)} (اجمع الأجزاء ثم فك الضغط)" if len(parts) > 1 else "")
                       + "\n/restore_test للتحقق من سلامتها · /backup_secrets للمفاتيح")
                send_fn(name, chunk, cap)
                res["sent"] += 1
        res["ok"] = True
        mark_done(now, paths, ok=True)
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        mark_done(now, res["paths"], ok=False, error=res["error"])
        try:
            import audit
            audit.record("backup.weekly_failed", actor="scheduler", error=res["error"])
        except Exception:
            pass
        return res
    try:
        import audit
        audit.record("backup.weekly", actor="scheduler", files=res["files"], parts=len(res["paths"]), removed=res["removed"])
    except Exception:
        pass
    return res


# ---- /restore_test (09 §5 fire drill) ----
EXPECTED_MEMBERS = ("glossary.json", "settings.json", "roles.json", "sessions/sessions_index.json", "transcripts/ALL.md")


def restore_test(zip_path=None):
    """
    يفك آخر ZIP محلي (أو المسار المعطى) في مجلد مؤقت ويتحقق: CRC لكل عضو، لا مسارات خطرة (zip-slip)،
    لا ملفات سرية، JSON صالح لكل *.json، ووجود الملفات المتوقعة. يرجع dict تقرير (بلا استثناءات) ثم يحذف المؤقت.
    """
    rep = {"ok": False, "path": None, "members": 0, "bytes": 0, "json_ok": 0, "json_bad": [], "missing": [],
           "bad_crc": [], "unsafe": [], "secrets": [], "error": None}
    if zip_path is None:
        local = list_local()
        if not local:
            rep["error"] = "لا توجد نسخة محلية في backups/ بعد — نفّذ /backup أو انتظر النسخة الأسبوعية"
            return rep
        zip_path = local[-1][0]
        if "_part" in os.path.basename(zip_path):
            rep["error"] = "آخر نسخة مقسّمة لأجزاء — اجمع الأجزاء يدوياً ثم اختبر الملف الكامل"
            rep["path"] = zip_path
            return rep
    rep["path"] = zip_path
    tmp = tempfile.mkdtemp(prefix="restore_test_")
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                rep["bad_crc"].append(bad)
            names = z.namelist()
            rep["members"] = len(names)
            for n in names:
                norm = n.replace("\\", "/")
                if norm.startswith("/") or ".." in norm.split("/") or (len(norm) > 1 and norm[1] == ":"):
                    rep["unsafe"].append(n)
                    continue
                if _is_secret(n):
                    rep["secrets"].append(n)
                    continue
                z.extract(n, tmp)
                fp = os.path.join(tmp, *norm.split("/"))
                if os.path.isfile(fp):
                    rep["bytes"] += os.path.getsize(fp)
                    if norm.endswith(".json"):
                        try:
                            with open(fp, "r", encoding="utf-8") as f:
                                json.load(f)
                            rep["json_ok"] += 1
                        except (OSError, ValueError):
                            rep["json_bad"].append(n)
            present = {n.replace("\\", "/") for n in names}
            rep["missing"] = [m for m in EXPECTED_MEMBERS if m not in present]
        rep["ok"] = not (rep["bad_crc"] or rep["unsafe"] or rep["secrets"] or rep["json_bad"]) and rep["members"] > 0
    except Exception as e:
        rep["error"] = f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rep


def restore_test_text(rep):
    """تقرير عربي قصير لنتيجة restore_test."""
    if rep.get("error") and not rep.get("members"):
        return f"❌ تمرين الاستعادة: {rep['error']}"
    icon = "✅" if rep["ok"] else "❌"
    lines = [f"{icon} تمرين الاستعادة — {os.path.basename(rep['path'] or '?')}",
             f"• الأعضاء: {rep['members']} · الحجم بعد الفك: {rep['bytes'] / 1024:.0f}KB · JSON سليم: {rep['json_ok']}"]
    if rep["missing"]:
        lines.append("• ⚠️ غير موجود (قد يكون طبيعياً لو لم تُنشأ بعد): " + ", ".join(rep["missing"]))
    if rep["bad_crc"]:
        lines.append("• ❌ CRC تالف: " + ", ".join(rep["bad_crc"]))
    if rep["json_bad"]:
        lines.append("• ❌ JSON تالف: " + ", ".join(rep["json_bad"]))
    if rep["unsafe"]:
        lines.append("• ❌ مسارات خطرة (رُفض فكّها): " + ", ".join(rep["unsafe"]))
    if rep["secrets"]:
        lines.append("• ❌ ملفات سرية داخل النسخة العامة: " + ", ".join(rep["secrets"]))
    if rep.get("error"):
        lines.append(f"• ⚠️ {rep['error']}")
    lines.append("• المجلد المؤقت حُذف بعد الفحص — بياناتك الحية لم تُمس.")
    return "\n".join(lines)
