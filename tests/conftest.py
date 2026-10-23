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
    import key_manager
    importlib.reload(key_manager)
    return key_manager


@pytest.fixture
def km(fresh_env):
    """نسخة KeyManager جديدة على ملف مؤقت."""
    return fresh_env.KeyManager()
