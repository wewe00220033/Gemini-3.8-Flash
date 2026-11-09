# -*- coding: utf-8 -*-
"""
tests/test_m1_v211.py — اختبارات حزمة M1 الرسمية (v2.11).
تغطي:
1. توقف المشرف الفوري عند EXIT_CONFIG=2 دون إعادة تشغيل مع تدوين السبب.
2. استمرار إعادة تشغيل المشرف عند أكواد الانهيار العادية.
3. استخدام spawn() لعزل جلسة العملية (CREATE_NEW_PROCESS_GROUP على Windows / start_new_session على POSIX).
4. استنفاد موديلات الصوت لا يقفل flash-lite.
5. المفتاح الباطل (invalid) يقفل كل الموديلات بلا استثناء.
6. نقطة _check المركزية في observability تحجب توكن البوت في الاستثناءات.
7. تنويعات ملفات الأسرار والبادئات في backup.py.
8. استثناء .env.example من قائمة الأسرار في backup.py.
9. فرض سقف فك الضغط التراكمي في restore_test ضد zip-bomb.
"""
import json
import os
import subprocess
import sys
import time
import zipfile
import pytest

from config import Config
import supervisor as sv
import observability
import backup


# ------------------------------------------------------------------ 1, 2, 3: Supervisor
FAKE_WORKER = r'''
import os, sys, time
plan = os.environ.get("FAKE_PLAN", "0").split(",")
gen = int(sys.argv[sys.argv.index("--generation") + 1])
step = plan[min(gen - 1, len(plan) - 1)]
time.sleep(0.05)
sys.exit(int(step))
'''


@pytest.fixture
def sup_env(tmp_path, monkeypatch):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        time.sleep(min(s, 0.05))

    def make(plan, **kw):
        monkeypatch.setenv("FAKE_PLAN", plan)
        kw.setdefault("backoff", (5, 10, 20, 60))
        kw.setdefault("max_per_hour", 20)
        kw.setdefault("beat_max", 60)
        kw.setdefault("shutdown_grace", 2)
        return sv.Supervisor(data_dir=str(data), script=str(script), python=sys.executable,
                             sleep=fake_sleep, log=lambda *a: None, **kw)

    return make, data, sleeps


def test_supervisor_exit_config_stops_without_restart(sup_env):
    """خروج العامل بكود 2 (EXIT_CONFIG) يوقف المشرف فوراً بكود 2 مع تسجيل config_error."""
    make, data, sleeps = sup_env
    s = make("2")
    code = s.run()
    assert code == sv.EXIT_CONFIG
    assert code == 2
    assert s.generation == 1
    # لم يدخل في سلم التهدئة (backoff ladder) إطلاقاً — أي نوم كان فقط نبض استطلاع الدورة (1ث)
    backoff_delays = [x for x in sleeps if x != 1]
    assert len(backoff_delays) == 0
    assert os.path.exists(s.reason_file)
    r = json.load(open(s.reason_file, encoding="utf-8"))
    assert r["reason"] == "config_error"
    assert r["exit_code"] == 2


def test_supervisor_crash_code_still_restarts(sup_env):
    """خروج العامل بكود انهيار (مثلاً 1) يستمر في اتباع سلم إعادة التشغيل ثم يخرج 0."""
    make, data, sleeps = sup_env
    s = make("1,0")
    code = s.run()
    assert code == sv.EXIT_OK
    assert s.generation == 2
    backoff_delays = [x for x in sleeps if x != 1]
    assert backoff_delays == [5]


def test_spawn_uses_new_session_or_group(tmp_path, monkeypatch):
    """spawn() يمرر CREATE_NEW_PROCESS_GROUP على Windows أو start_new_session على POSIX."""
    script = tmp_path / "dummy.py"
    script.write_text("print('ok')", encoding="utf-8")
    data = tmp_path / "data"
    s = sv.Supervisor(data_dir=str(data), script=str(script), python=sys.executable, log=lambda *a: None)

    captured_kwargs = {}
    original_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        return original_popen([sys.executable, "-c", "import sys; sys.exit(0)"])

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    proc = s.spawn()
    proc.wait()

    if os.name == "nt":
        flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        assert captured_kwargs.get("creationflags", 0) & flag != 0
    else:
        assert captured_kwargs.get("start_new_session") is True


# ------------------------------------------------------------------ 4, 5: Key Manager
def test_audio_exhaustion_does_not_block_flash_lite(fresh_env):
    """استنفاد موديلات الصوت لا يحجب موديل flash-lite للمساعد النصي."""
    km = fresh_env.KeyManager()
    km.data = {
        "keys": [
            {
                "key": "AIzaSyTestAudioExhausted",
                "name": "AudioExhaustedProject",
                "status": "exhausted",
                "exhausted_until": time.time() + 3600,
                "exhausted_models": {Config.MODELS[0]: time.time() + 3600},
            }
        ]
    }
    key_entry = km.data["keys"][0]
    # موديل الصوت محجوب
    assert km.is_model_available(key_entry, Config.MODELS[0]) is False
    # flash-lite غير محجوب ومتاح للاستخدام
    assert km.is_model_available(key_entry, "gemini-3.5-flash-lite") is True


def test_invalid_key_blocks_every_model(fresh_env):
    """المفتاح الباطل (invalid) يحجب كافة الموديلات بلا استثناء بما فيها flash-lite."""
    km = fresh_env.KeyManager()
    km.data = {
        "keys": [
            {
                "key": "AIzaSyTestInvalidKeyAllBlocked",
                "name": "InvalidProject",
                "status": "invalid",
                "exhausted_until": time.time() + 3600,
            }
        ]
    }
    key_entry = km.data["keys"][0]
    assert km.is_model_available(key_entry, Config.MODELS[0]) is False
    assert km.is_model_available(key_entry, "gemini-3.5-flash-lite") is False
    assert km._model_blocked_until(key_entry, "gemini-3.5-flash-lite") > time.time()


# ------------------------------------------------------------------ 6: Observability
def test_doctor_check_redacts_token():
    """نقطة _check المركزية في observability تحجب توكن البوت في رسائل الاستثناء."""
    raw_token = "1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ123456789"

    def faulty_doctor_check():
        raise RuntimeError(f"Connection failed to api.telegram.org/bot{raw_token}/getMe")

    status, report = observability._check(faulty_doctor_check)
    assert status == observability.WARN
    assert raw_token not in report
    assert "****:****" in report


# ------------------------------------------------------------------ 7, 8: Backup Secrets
def test_backup_secret_variants():
    """التحقق من حظر كافة تنويعات ملفات الأسرار والبادئات."""
    assert backup._is_secret("keys.json.backup") is True
    assert backup._is_secret("keys.txt.old") is True
    assert backup._is_secret(".env.production") is True
    assert backup._is_secret(".env.local") is True
    assert backup._is_secret(".keys-prod.json") is True
    assert backup._is_secret("state/keys.json.save") is True
    assert backup._is_secret("normal_document.md") is False
    assert backup._is_secret("transcripts/sample.json") is False


def test_env_example_not_secret():
    """استثناء .env.example صراحةً بحيث لا يُعامل كسرّ."""
    assert backup._is_secret(".env.example") is False
    assert backup._is_secret("data/.env.example") is False
    assert backup._is_secret("/root/project/.env.example") is False


# ------------------------------------------------------------------ 9: Restore Test Size Cap
def test_restore_test_size_cap(tmp_path, monkeypatch):
    """restore_test() يرفض الأرشيف الذي يتجاوز سقف الفك التراكمي RESTORE_MAX_BYTES."""
    zip_p = tmp_path / "bomb_test.zip"
    monkeypatch.setattr(backup, "RESTORE_MAX_BYTES", 1024)

    # ننشئ أرشيفاً فيه ملف غير سري بحجم 4096 بايت (أكبر من 1024 بايت)
    with zipfile.ZipFile(str(zip_p), "w") as z:
        z.writestr("glossary.json", "{}" * 2000)

    rep = backup.restore_test(str(zip_p))
    assert rep["ok"] is False
    assert "تجاوز سقف الفك الأقصى" in str(rep["error"])
    assert "glossary.json" in rep["unsafe"]
