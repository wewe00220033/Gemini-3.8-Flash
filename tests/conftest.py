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
    # مرحلة 2: ملفا القاطع ومجموعات المفاتيح في المجلد المؤقت أيضاً (لا نلمس state/ الحقيقي)
    monkeypatch.setattr(config.Config, "CIRCUIT_FILE", str(tmp_path / "state" / "circuit.json"))
    monkeypatch.setattr(config.Config, "KEY_GROUPS_FILE", str(tmp_path / "state" / "key_groups.json"))
    # مرحلة 3: الأرشيف/القاموس/الأدوار/الإعدادات في المجلد المؤقت + تصفير الكاشات (singletons)
    monkeypatch.setattr(config.Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config.Config, "TRANSCRIPTS_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setattr(config.Config, "GLOSSARY_FILE", str(tmp_path / "glossary.json"))
    monkeypatch.setattr(config.Config, "ROLES_FILE", str(tmp_path / "roles.json"))
    monkeypatch.setattr(config.Config, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    # مرحلة 4: الجلسات في المجلد المؤقت + تصفير كاش الجلسة النشطة
    monkeypatch.setattr(config.Config, "SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(config.Config, "SESSIONS_INDEX_FILE", str(tmp_path / "sessions" / "sessions_index.json"))
    monkeypatch.setattr(config.Config, "CURRENT_SESSION_FILE", str(tmp_path / "sessions" / "current_session.json"))
    # هذه الوحدات تحمل مرجعها الخاص لـ Config (from config import Config) — بعد reload(config) يصبح قديماً
    for mod_name in ("glossary", "archive", "settings", "prompts", "text_assist", "transcriber", "sessions", "delivery"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "Config"):
            monkeypatch.setattr(mod, "Config", config.Config)
    for mod_name in ("settings", "prompts"):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            mod.reset_cache()
    gl = sys.modules.get("glossary")
    if gl is not None:
        gl._singleton = None
    ss = sys.modules.get("sessions")
    if ss is not None:
        ss.reset_cache()
    import key_manager
    importlib.reload(key_manager)
    # قاطع الموديلات singleton مشترك بين الوحدات — نبدأ كل اختبار بقواطع مفتوحة وبلا حفظ على القرص
    import model_state
    model_state.model_circuit.detach_store()
    model_state.model_circuit.reset()
    model_state.model_circuit._history.clear()
    return key_manager


@pytest.fixture(autouse=True)
def _no_network_polish(monkeypatch):
    """مرحلة 3: المساعد النصي (flash-lite) لا يلمس الشبكة في الاختبارات أبداً.
    الافتراضي: تنظيم وهمي حتمي. اختبارات المرحلة 3 تستبدله صراحةً عند الحاجة (فشل/نجاح)."""
    import text_assist

    def _stub_polish(raw_text, role_id=None, glossary=None, context_summary=None):
        return ("[منظم-اختبار] " + raw_text, None)

    def _no_post(*a, **k):
        raise AssertionError("text_assist: network call in tests is forbidden")

    monkeypatch.setattr(text_assist, "polish", _stub_polish)
    monkeypatch.setattr(text_assist.requests, "post", _no_post)
    # مرحلة 4: التسمية الذكية (flash-lite نصياً) بلا شبكة أيضاً — عنوان حتمي قصير
    try:
        import sessions
        monkeypatch.setattr(sessions, "voice_title", lambda raw, generate_fn=None: "عنوان-اختبار")
    except ImportError:  # pragma: no cover
        pass
    yield


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
