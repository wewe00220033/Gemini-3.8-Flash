# -*- coding: utf-8 -*-
"""
إعداد مشترك للاختبارات:
- يوجّه keys.json لمجلد مؤقت حتى لا تلمس الاختبارات ملفات المستخدم الحقيقية.
- يمسح متغيرات البيئة الحساسة حتى تكون النتائج حتمية.
"""
import atexit
import os
import shutil
import sys
import importlib
import tempfile
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_CONFIG_MODULES = (
    "glossary", "archive", "settings", "prompts", "text_assist", "transcriber",
    "sessions", "delivery", "logkit", "observability", "incidents", "watchdog",
    "jobs", "voice_queue", "backup", "audit", "panel", "fsm", "assistant",
    "interactions_client", "web_service", "key_manager", "telegram_bot",
    "main", "supervisor", "model_state",
)


def _apply_tmp_paths(mp, cfg_cls, base_dir):
    base = str(base_dir)
    mappings = {
        "DATA_DIR": base,
        "KEYS_JSON_FILE": os.path.join(base, "keys.json"),
        "KEYS_TXT_FILE": os.path.join(base, "keys.txt"),
        "JOBS_FILE": os.path.join(base, "state", "jobs.ndjson"),
        "CIRCUIT_FILE": os.path.join(base, "state", "circuit.json"),
        "KEY_GROUPS_FILE": os.path.join(base, "state", "key_groups.json"),
        "WORKER_BEAT_FILE": os.path.join(base, "state", "worker.beat"),
        "RESTART_REASON_FILE": os.path.join(base, "state", "restart_reason.json"),
        "BACKUP_STATE_FILE": os.path.join(base, "state", "backup_state.json"),
        "TRANSCRIPTS_DIR": os.path.join(base, "transcripts"),
        "GLOSSARY_FILE": os.path.join(base, "glossary.json"),
        "ROLES_FILE": os.path.join(base, "roles.json"),
        "SETTINGS_FILE": os.path.join(base, "settings.json"),
        "SESSIONS_DIR": os.path.join(base, "sessions"),
        "SESSIONS_INDEX_FILE": os.path.join(base, "sessions", "sessions_index.json"),
        "CURRENT_SESSION_FILE": os.path.join(base, "sessions", "current_session.json"),
        "LOGS_DIR": os.path.join(base, "logs"),
        "AUDIT_FILE": os.path.join(base, "logs", "audit.log"),
        "INCIDENTS_DIR": os.path.join(base, "incidents"),
        "BACKUPS_DIR": os.path.join(base, "backups"),
        "PERSIST_PROBE_FILE": os.path.join(base, ".persist_probe"),
        "LOG_TO_FILE": False,
    }
    for attr, val in mappings.items():
        if mp is not None:
            mp.setattr(cfg_cls, attr, val)
        else:
            setattr(cfg_cls, attr, val)


def _sync_loaded_configs(mp, cfg_cls):
    for mod_name in _CONFIG_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "Config"):
            mp.setattr(mod, "Config", cfg_cls)


# BUG-06: توجيه كل مسارات التشغيل لمجلد مؤقت منذ لحظة collection الأولى (قبل استيراد telegram_bot/key_manager في أي ملف اختبار)
_BOOT_TMP = tempfile.mkdtemp(prefix="gvt_pytest_boot_")
atexit.register(shutil.rmtree, _BOOT_TMP, ignore_errors=True)
import config as _boot_config  # noqa: E402
_apply_tmp_paths(None, _boot_config.Config, _BOOT_TMP)

ENV_KEYS = [
    "GEMINI_API_KEYS", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_USERNAME", "TELEGRAM_CHAT_ID", "RECORDINGS_DIR",
    "PORT", "SERVER_PORT", "HOST", "WEB_ACCESS_TOKEN", "DATA_DIR", "MAX_UPLOAD_MB",
    "LOCAL_POLISH_CMD", "LOCAL_POLISH_TIMEOUT",
]


@pytest.fixture
def fresh_env(monkeypatch, tmp_path):
    """بيئة نظيفة + مسار keys.json مؤقت. يعيد تحميل config و key_manager."""
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    import config
    importlib.reload(config)
    # نضبط الأساس المؤقت قبل monkeypatch حتى لا يعود الكلاس الجديد لمسارات الجذر عند teardown
    _apply_tmp_paths(None, config.Config, _BOOT_TMP)
    _apply_tmp_paths(monkeypatch, config.Config, tmp_path)
    aud = sys.modules.get("audit")
    if aud is not None:
        aud.set_actor(None)
    f = sys.modules.get("fsm")
    if f is not None:
        f.clear()
    asst = sys.modules.get("assistant")
    if asst is not None:
        asst.reset()        # مرحلة 7: وضع /chat حالة واحدة في الذاكرة
    inc = sys.modules.get("incidents")
    if inc is not None:
        inc.reset_state()
        inc.set_notifier(None)
    # هذه الوحدات تحمل مرجعها الخاص لـ Config (from config import Config) — بعد reload(config) يصبح قديماً
    _sync_loaded_configs(monkeypatch, config.Config)
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
def _runtime_dirs_in_tmp(monkeypatch, tmp_path):
    """عزل كل ملفات التشغيل داخل tmp_path حتى في الاختبارات التي لا تستخدم fresh_env (glossary/keys/settings/incidents/logs...)."""
    import config
    _apply_tmp_paths(monkeypatch, config.Config, tmp_path)
    _sync_loaded_configs(monkeypatch, config.Config)
    for mod_name in ("settings", "prompts", "sessions"):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            mod.reset_cache()
    gl = sys.modules.get("glossary")
    if gl is not None:
        gl._singleton = None
    inc = sys.modules.get("incidents")
    if inc is not None:
        inc.reset_state()
    yield
    gl = sys.modules.get("glossary")
    if gl is not None:
        gl._singleton = None
    for mod_name in ("settings", "prompts", "sessions"):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            mod.reset_cache()


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
    monkeypatch.setattr(text_assist, "polish_gemini", _stub_polish)
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
