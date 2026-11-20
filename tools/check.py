#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/check.py — بوابة الجودة الموحدة قبل أي دفع (A2 — المرحلة 3 / ما قبل الدمج).

يشغّل **نفس نطاق CI حرفياً** (.github/workflows/ci.yml) بالترتيب ويتوقف عند أول فشل:
  1. pyflakes  على *.py tests/*.py tools/*.py
  2. py_compile على *.py
  3. pytest -q

السبب: pytest وحده لا يشغّل pyflakes، وقد سبق أن أُعلنت كوميتات «خضراء» بينما CI أحمر
عند خطوة Lint (استيرادات غير مستخدمة) — هذا السكربت يمنع تكرار ذلك.

- stdlib فقط (لا يستورد شيئاً من المشروع، لا يلمس الشبكة ولا البيانات).
- يوسّع الأنماط بنفسه (glob) حتى لا يعتمد على الصدفة أو PowerShell.
- لا يدخل حزمة النشر (pack_deploy يضم الجذر و static/ فقط).

الاستخدام:
    python tools/check.py            # كل الخطوات
    python tools/check.py --no-tests # Lint + Compile فقط (سريع)
الخروج: 0 نجاح · 1 فشل خطوة · 2 أداة مفقودة (pyflakes/pytest غير مثبتين).
"""
import argparse
import glob
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

LINT_PATTERNS = ("*.py", "tests/*.py", "tools/*.py")
COMPILE_PATTERNS = ("*.py",)


def _expand(patterns):
    files = []
    for pat in patterns:
        files.extend(sorted(glob.glob(os.path.join(ROOT, *pat.split("/")))))
    return files


def _module_available(name):
    r = subprocess.run([sys.executable, "-c", f"import {name}"], capture_output=True)
    return r.returncode == 0


def _run(label, cmd):
    print(f"\n▶ {label}")
    t0 = time.monotonic()
    r = subprocess.run(cmd, cwd=ROOT)
    dt = time.monotonic() - t0
    status = "✅" if r.returncode == 0 else "❌"
    print(f"{status} {label} — exit {r.returncode} — {dt:.1f}s")
    return r.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description="بوابة الجودة الموحدة: pyflakes → py_compile → pytest (نفس نطاق CI).")
    ap.add_argument("--no-tests", action="store_true", help="تخطي pytest (Lint + Compile فقط)")
    args = ap.parse_args(argv)

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    for mod in ("pyflakes",) + (() if args.no_tests else ("pytest",)):
        if not _module_available(mod):
            print(f"❌ {mod} غير مثبت — ثبّت أدوات التطوير: pip install -r requirements-dev.txt")
            return 2

    lint_files = _expand(LINT_PATTERNS)
    compile_files = _expand(COMPILE_PATTERNS)
    print(f"📂 {ROOT}")
    print(f"🔎 Lint: {len(lint_files)} ملفاً · Compile: {len(compile_files)} ملفاً")

    rc = _run("Lint (pyflakes)", [sys.executable, "-m", "pyflakes", *lint_files])
    if rc != 0:
        return 1
    rc = _run("Compile (py_compile)", [sys.executable, "-m", "py_compile", *compile_files])
    if rc != 0:
        return 1
    if not args.no_tests:
        env_note = "" if os.environ.get("PYTEST_DISABLE_DOTENV") else " (conftest يضبط PYTEST_DISABLE_DOTENV=1 تلقائياً)"
        print(f"ℹ️ pytest{env_note}")
        rc = _run("Tests (pytest -q)", [sys.executable, "-m", "pytest", "-q"])
        if rc != 0:
            return 1

    print("\n🏁 كل البوابات خضراء — جاهز للدفع.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
