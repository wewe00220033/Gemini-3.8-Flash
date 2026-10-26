# -*- coding: utf-8 -*-
"""
chore/deploy-kit — tools/pack_deploy.py (أرشيف نشر بلا أسرار) و .env.example (لا متغيرات وهمية). بلا شبكة.
"""
import importlib.util
import os
import re
import zipfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture(scope="module")
def pack():
    spec = importlib.util.spec_from_file_location("pack_deploy", os.path.join(ROOT, "tools", "pack_deploy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_project(tmp_path, with_secrets=True):
    """شجرة مشروع مصغّرة: ملفات تشغيل + أسرار وبيانات حية يجب ألا تدخل الأرشيف."""
    for f in ("main.py", "config.py", "key_manager.py", "telegram_bot.py", "web_service.py", "transcriber.py",
              "requirements.txt", "VERSION", "README.md", ".env.example", "extra_module.py", "test_360_exhaustive.py"):
        (tmp_path / f).write_text(f"# {f}\n", encoding="utf-8")
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    if with_secrets:
        (tmp_path / "keys.json").write_text('{"keys": ["AIzaSy_SECRET"]}', encoding="utf-8")
        (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=1:x\n", encoding="utf-8")
        (tmp_path / ".env.prod").write_text("X=1\n", encoding="utf-8")
        for d in ("state", "logs", "backups", "transcripts", "sessions", "incidents", "__pycache__", ".git"):
            (tmp_path / d).mkdir()
        (tmp_path / "state" / "jobs.ndjson").write_text("{}\n", encoding="utf-8")
        (tmp_path / "logs" / "2026-10-26.log").write_text("x\n", encoding="utf-8")
        (tmp_path / "backups" / "backup_2026-10-24_0300.zip").write_bytes(b"PK")
        (tmp_path / "__pycache__" / "main.cpython-312.pyc").write_bytes(b"\x00")
        (tmp_path / "glossary.json").write_text("{}", encoding="utf-8")
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".persist_probe").write_text("1", encoding="utf-8")
    return tmp_path


def test_pack_includes_runtime_only_and_excludes_secrets(pack, tmp_path):
    root = _fake_project(tmp_path)
    out = str(tmp_path / "out" / "deploy_ready.zip")
    os.makedirs(os.path.dirname(out))
    members, total = pack.build(out, root=str(root))
    names = sorted(rel for rel, _ in members)
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == names and z.testzip() is None
    assert "main.py" in names and "static/index.html" in names and "extra_module.py" in names
    assert ".env.example" in names                                   # القالب مسموح صراحة
    for forbidden in ("keys.json", ".env", ".env.prod", "state/jobs.ndjson", "logs/2026-10-26.log",
                      "backups/backup_2026-10-24_0300.zip", "__pycache__/main.cpython-312.pyc",
                      "glossary.json", "settings.json", ".persist_probe", "tests/test_x.py", "pytest.ini",
                      "test_360_exhaustive.py"):
        assert forbidden not in names, forbidden
    assert total == sum(os.path.getsize(ap) for _, ap in members) > 0
    assert not os.path.exists(out + ".tmp")


def test_pack_with_tests_flag_and_list_dry_run(pack, tmp_path):
    root = _fake_project(tmp_path)
    out = str(tmp_path / "d.zip")
    members, _ = pack.build(out, root=str(root), with_tests=True, dry_run=True)
    names = [rel for rel, _ in members]
    assert "tests/test_x.py" in names and "pytest.ini" in names
    assert not os.path.exists(out)                                   # dry-run لا يكتب
    assert "keys.json" not in names


def test_pack_fails_when_required_file_missing(pack, tmp_path):
    root = _fake_project(tmp_path)
    os.remove(root / "static" / "index.html")
    with pytest.raises(FileNotFoundError, match="static/index.html"):
        pack.build(str(tmp_path / "d.zip"), root=str(root))


def test_secret_guard_rejects_leak_even_if_allowlisted(pack, tmp_path, monkeypatch):
    """لو أُدرج ملف سري بالخطأ في قائمة السماح — الحارس يرفض الأرشيف كله ولا يترك ملفاً مؤقتاً."""
    root = _fake_project(tmp_path)
    monkeypatch.setattr(pack, "OPTIONAL_FILES", pack.OPTIONAL_FILES + ("keys.json",))
    out = str(tmp_path / "d.zip")
    with pytest.raises(RuntimeError, match="keys.json"):
        pack.build(out, root=str(root))
    assert not os.path.exists(out) and not os.path.exists(out + ".tmp")


def test_is_forbidden_patterns(pack):
    f = pack.is_forbidden
    assert f("keys.json") and f("sub/keys.json") and f(".env") and f(".env.local") and f("x/.env")
    assert f("state/jobs.ndjson") and f("logs/a.log") and f("backups/b.zip") and f("transcripts/ALL.md")
    assert f("sessions/session_1.md") and f("data/x.txt") and f(".git/config") and f("a/__pycache__/b.pyc")
    assert f("glossary.json") and f("settings.json") and f("roles.json") and f("glossary.json.corrupt-1")
    assert not f(".env.example") and not f("main.py") and not f("static/index.html") and not f("README.md")
    assert not f("tests/test_x.py")                                  # الاختبارات ليست سرية — فقط غير مضمّنة افتراضياً


def test_cli_exit_codes(pack, tmp_path, capsys):
    root = _fake_project(tmp_path)
    out = str(tmp_path / "cli.zip")
    assert pack.main(["--root", str(root), "-o", out]) == 0 and os.path.exists(out)
    printed = capsys.readouterr().out
    assert "keys.json" not in printed.split("→")[0] and "deploy_ready" not in printed
    # بلا -o → الافتراضي <root>/deploy_ready.zip
    assert pack.main(["--root", str(root)]) == 0 and os.path.exists(root / "deploy_ready.zip")
    capsys.readouterr()
    os.remove(root / "main.py")
    assert pack.main(["--root", str(root), "--list"]) == 2
    assert "main.py" in capsys.readouterr().out


def test_real_repo_archive_has_no_secrets(pack, tmp_path):
    """على المستودع الحقيقي: الأرشيف يُبنى، ويحوي الأساسيات، ولا يحوي أي ملف سري/اختبار/وثائق معمارية."""
    out = str(tmp_path / "real.zip")
    members, _ = pack.build(out, root=ROOT)
    names = [rel for rel, _ in members]
    for must in ("main.py", "config.py", "telegram_bot.py", "web_service.py", "backup.py", "panel.py",
                 "requirements.txt", "VERSION", "static/index.html", ".env.example"):
        assert must in names, must
    for rel in names:
        assert not pack.is_forbidden(rel), rel
        assert not rel.startswith(("tests/", "architecture-v2/", "genspark-review/", "requirements/", ".github/")), rel
        assert not rel.endswith(".bat"), rel
    assert "test_360_exhaustive.py" not in names


def test_env_example_lists_only_vars_the_code_reads():
    """كل متغير في .env.example (مفعّل أو معلّق) يقرؤه الكود فعلاً — لا متغيرات وهمية تضلّل المالك."""
    text = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()
    listed = set(re.findall(r"^#?\s?([A-Z][A-Z0-9_]+)=", text, flags=re.M))
    assert {"DATA_DIR", "BOT_WORKERS", "MAX_UPLOAD_MB", "DISPLAY_TZ", "WEB_ALLOW_OPEN", "TRUST_PROXY",
            "SUPERVISED", "WEB_ACCESS_TOKEN", "TELEGRAM_CHAT_ID"} <= listed
    code = ""
    for fn in os.listdir(ROOT):
        if fn.endswith(".py"):
            code += open(os.path.join(ROOT, fn), encoding="utf-8").read()
    for var in sorted(listed):
        assert f'"{var}"' in code, f"{var} مذكور في .env.example لكن الكود لا يقرؤه"
    # القيم الموصى بها لحاوية 512MB مفعّلة (غير معلّقة)
    for line in ("BOT_WORKERS=2", "MAX_UPLOAD_MB=20", "DATA_DIR=/home/container/data", "WEB_ALLOW_OPEN=0", "TRUST_PROXY=0"):
        assert re.search(rf"^{re.escape(line)}\s*$", text, flags=re.M), line


def test_deploy_md_backup_row_is_correct():
    text = open(os.path.join(ROOT, "DEPLOY.md"), encoding="utf-8").read()
    assert "يرسل لك `keys.json` كمستند" not in text                 # السطر القديم المضلّل أُزيل
    assert "/backup_secrets confirm" in text and "Pre-flight Verification" in text and "pack_deploy.py" in text

