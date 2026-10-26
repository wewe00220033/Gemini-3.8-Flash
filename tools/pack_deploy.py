#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/pack_deploy.py — يجهّز deploy_ready.zip للرفع المباشر على Bot-Hosting / Pterodactyl.

المبدأ: **قائمة سماح** (ملفات التشغيل فقط) لا قائمة منع — فالملف السري لا يدخل إلا لو أُدرج صراحة،
ثم **حارس أسرار** يفحص كل عضو قبل الإغلاق ويرفض الأرشيف كله لو تسرّب أي شيء (keys.json/.env/state/…).

يُستبعد تلقائياً: .git/ tests/ __pycache__/ .env keys.json architecture-v2/ genspark-review/ الوثائق
العربية الطويلة، البيانات الحية (state/ logs/ backups/ transcripts/ sessions/ incidents/ data/)، وملفات .bat المحلية.

الاستخدام:
    python tools/pack_deploy.py                 # ينتج deploy_ready.zip في جذر المشروع
    python tools/pack_deploy.py -o out.zip      # اسم آخر
    python tools/pack_deploy.py --list          # يعرض ما سيُضمَّن فقط بلا كتابة
    python tools/pack_deploy.py --with-tests    # يضمّ tests/ وpytest.ini (لتشغيل الاختبارات على الخادم)
الخروج: 0 نجاح · 2 تسرّب سري أو ملف مطلوب مفقود.
"""
import argparse
import os
import sys
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

# ملفات التشغيل الضرورية (غيابها = فشل)
REQUIRED_FILES = (
    "main.py", "config.py", "key_manager.py", "telegram_bot.py", "web_service.py", "transcriber.py",
    "requirements.txt", "VERSION", "static/index.html",
)
# ملفات اختيارية مفيدة على الخادم
OPTIONAL_FILES = (
    "README.md", "DEPLOY.md", "LICENSE", ".env.example", "runtime.txt", "start.sh",
    "smoke_failover.py", "transcribe_tool.py",
)
# مجلدات تُضمَّن كاملة (ملفات .py/.html/.css/.js/.txt/.md فقط)
INCLUDE_DIRS = ("static",)
INCLUDE_EXTS = (".py", ".html", ".css", ".js", ".txt", ".md", ".json")
# وحدات بايثون في الجذر — تُضمَّن كلها ما عدا المستبعد أدناه
ROOT_PY_EXCLUDE = ("test_360_exhaustive.py",)

# حارس الأسرار/البيانات: أي عضو يطابق أحد هذه الأنماط يُفشل الأرشيف
SECRET_BASENAMES = ("keys.json", "keys.txt", ".env", ".persist_probe", "backup_state.json", "current_session.json")
SECRET_PREFIXES = (".env.",)                     # .env.local, .env.prod … (لكن .env.example مسموح صراحة)
SECRET_DIR_PARTS = ("state", "logs", "backups", "transcripts", "sessions", "incidents", "data",
                    ".git", "__pycache__", ".smoke_failover", ".pytest_cache")
SECRET_EXTS = (".ndjson", ".log", ".pyc")
RUNTIME_JSON = ("glossary.json", "roles.json", "settings.json", "sessions_index.json")


def is_forbidden(rel):
    """True لو المسار النسبي سري/بيانات حية — يُستخدم كحارس نهائي على كل عضو."""
    rel = rel.replace("\\", "/")
    base = os.path.basename(rel)
    parts = rel.split("/")
    if base == ".env.example":
        return False
    if base in SECRET_BASENAMES or base in RUNTIME_JSON:
        return True
    if any(base.startswith(p) for p in SECRET_PREFIXES):
        return True
    if any(p in SECRET_DIR_PARTS for p in parts[:-1]):
        return True
    if rel.endswith(SECRET_EXTS) or ".corrupt-" in base:
        return True
    return False


def collect(root=None, with_tests=False):
    """قائمة (rel_path, abs_path) المرشحة للأرشيف. يرفع FileNotFoundError لو ملف مطلوب غائب."""
    root = ROOT if root is None else root
    out = []
    seen = set()

    def add(rel):
        rel = rel.replace("\\", "/")
        ap = os.path.join(root, *rel.split("/"))
        if rel in seen or not os.path.isfile(ap):
            return False
        seen.add(rel)
        out.append((rel, ap))
        return True

    missing = [f for f in REQUIRED_FILES if not os.path.isfile(os.path.join(root, *f.split("/")))]
    if missing:
        raise FileNotFoundError("ملفات تشغيل مطلوبة مفقودة: " + ", ".join(missing))
    for f in REQUIRED_FILES + OPTIONAL_FILES:
        add(f)
    for fn in sorted(os.listdir(root)):
        if fn.endswith(".py") and fn not in ROOT_PY_EXCLUDE and not fn.startswith("test_"):
            add(fn)
    for d in INCLUDE_DIRS + (("tests",) if with_tests else ()):
        dp = os.path.join(root, d)
        if not os.path.isdir(dp):
            continue
        for dirpath, dirnames, filenames in os.walk(dp):
            dirnames[:] = [x for x in dirnames if x not in SECRET_DIR_PARTS]
            for fn in sorted(filenames):
                if fn.endswith(INCLUDE_EXTS):
                    add(os.path.relpath(os.path.join(dirpath, fn), root))
    if with_tests:
        add("pytest.ini")
        add("requirements-dev.txt")
    out.sort()
    return out


def build(output, root=None, with_tests=False, dry_run=False):
    """يبني الأرشيف ويرجع (members, total_bytes). يرفع RuntimeError عند تسرّب سري."""
    root = ROOT if root is None else root
    members = collect(root, with_tests)
    leaked = [rel for rel, _ in members if is_forbidden(rel)]
    if leaked:
        raise RuntimeError("رُفض الأرشيف — ملفات سرية/بيانات حية: " + ", ".join(leaked))
    total = sum(os.path.getsize(ap) for _, ap in members)
    if dry_run:
        return members, total
    tmp = output + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, ap in members:
            z.write(ap, rel)
    # فحص ثانٍ على الأرشيف المكتوب نفسه (دفاع في العمق)
    with zipfile.ZipFile(tmp) as z:
        bad = [n for n in z.namelist() if is_forbidden(n)]
        if z.testzip() is not None or bad:
            os.remove(tmp)
            raise RuntimeError("فشل التحقق من الأرشيف: " + (", ".join(bad) or "CRC"))
    os.replace(tmp, output)
    return members, total


def main(argv=None):
    ap = argparse.ArgumentParser(description="تجهيز deploy_ready.zip لملفات التشغيل فقط (بلا أسرار/اختبارات/بيانات).")
    ap.add_argument("-o", "--output", default=None, help="المخرج (الافتراضي <root>/deploy_ready.zip)")
    ap.add_argument("--root", default=None, help="جذر المشروع (الافتراضي مجلد المستودع)")
    ap.add_argument("--list", action="store_true", help="عرض المحتويات فقط بلا كتابة")
    ap.add_argument("--with-tests", action="store_true", help="ضمّ tests/ وpytest.ini")
    args = ap.parse_args(argv)
    root = os.path.abspath(args.root) if args.root else ROOT
    output = args.output or os.path.join(root, "deploy_ready.zip")
    try:
        members, total = build(output, root=root, with_tests=args.with_tests, dry_run=args.list)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"❌ {e}")
        return 2
    for rel, ap_ in members:
        print(f"  {os.path.getsize(ap_):>9,}  {rel}")
    print(f"{'📋' if args.list else '✅'} {len(members)} ملفاً · {total / 1024:.0f}KB"
          + ("" if args.list else f" → {output} ({os.path.getsize(output) / 1024:.0f}KB مضغوطاً)"))
    if not args.list:
        print("↑ ارفعه في Files → Upload ثم Unarchive وانقل المحتويات للجذر (DEPLOY.md §ب). لا يحوي keys.json ولا .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
