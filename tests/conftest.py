# -*- coding: utf-8 -*-
"""
إعداد مشترك للاختبارات:
- يوجّه keys.json لمجلد مؤقت حتى لا تلمس الاختبارات ملفات المستخدم الحقيقية.
- يمسح متغيرات البيئة الحساسة حتى تكون النتائج حتمية.
"""
import os
import sys
import importlib
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

ENV_KEYS = [
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_USERNAME", "TELEGRAM_CHAT_ID", "RECORDINGS_DIR",
    "PORT", "SERVER_PORT", "HOST", "WEB_ACCESS_TOKEN", "DATA_DIR", "MAX_UPLOAD_MB",
]


@pytest.fixture
def fresh_env(monkeypatch, tmp_path):
    """بيئة نظيفة + مسار keys.json مؤقت. يعيد تحميل config و key_manager."""
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    import config
    importlib.reload(config)
    monkeypatch.setattr(config.Config, "KEYS_JSON_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setattr(config.Config, "KEYS_TXT_FILE", str(tmp_path / "keys.txt"))
    # 1b: سجل المهام jobs.ndjson في مجلد مؤقت دائماً (jobs/voice_queue يحملان مرجعهما الخاص لـ Config)
    jobs_file = str(tmp_path / "state" / "jobs.ndjson")
    monkeypatch.setattr(config.Config, "JOBS_FILE", jobs_file)
    import jobs
    import voice_queue
    monkeypatch.setattr(jobs.Config, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(voice_queue.Config, "JOBS_FILE", jobs_file)
    import key_manager
    importlib.reload(key_manager)
    # قاطع الموديلات singleton مشترك بين الوحدات — نبدأ كل اختبار بقواطع مفتوحة
    import model_state
    model_state.model_circuit.reset()
    model_state.model_circuit._history.clear()
    return key_manager


@pytest.fixture(autouse=True)
def _stop_unified_queue():
    """1b: لا نترك خيط transcribe-N حياً بين الاختبارات (الطابور الموحّد singleton في telegram_bot)."""
    yield
    tb = sys.modules.get("telegram_bot")
    if tb is not None and getattr(tb, "QUEUE", None) is not None:
        try:
            tb.stop_queue(timeout=2)
        except Exception:
            pass


@pytest.fixture
def km(fresh_env):
    """نسخة KeyManager جديدة على ملف مؤقت."""
    return fresh_env.KeyManager()
