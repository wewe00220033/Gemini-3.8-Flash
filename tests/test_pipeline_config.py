# -*- coding: utf-8 -*-
"""
اختبارات T3a (DEC-P2-08 / DEC-P2-09): pipeline_config.py معزولة بلا شبكة وبلا sleep.
"""

import json
import logging
import os
import shutil

import config
import pipeline_config
import settings

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def test_default_cloud_only_mirrors_current_single_polish(fresh_env):
    """الحالة 1: غياب pipeline.json وغياب LOCAL_POLISH_CMD => مقترِح فلاش فقط وfinal معطلة."""
    assert not pipeline_config.has_disk_pipeline()
    cfg = pipeline_config.get()
    stages = {s["name"]: s for s in cfg["stages"]}
    assert stages["proposers"]["enabled"] is True
    assert [c["provider"] for c in stages["proposers"]["calls"]] == ["flash_lite"]
    assert stages["polishers"]["enabled"] is False
    assert stages["final"]["enabled"] is False
    # 40ث فلاش + 20ث هامش = 60ث
    assert pipeline_config.pipeline_deadline_sec(cfg) == 60.0
    # تأكيد النسخ العميق (تعديل الناتج لا يلوث استدعاءً لاحقاً)
    cfg["display"]["mode"] = "mutated"
    assert pipeline_config.get()["display"]["mode"] == "single_live"


def test_default_with_local_cmd_mirrors_synthesizer_off_and_merged(fresh_env, monkeypatch):
    """الحالات 2 و3 و4: وجود LOCAL_POLISH_CMD مع synthesizer / off / merged-split + تكافؤ 420.0ث بالحساب."""
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "python fake_shelby.py")

    # الحالة 2: synthesizer (الافتراضي) => proposers [flash_lite, shelby] + final [shelby -> flash_lite]
    settings.set_compare_mode("synthesizer")
    cfg_syn = pipeline_config.get()
    st_syn = {s["name"]: s for s in cfg_syn["stages"]}
    assert [c["provider"] for c in st_syn["proposers"]["calls"]] == ["flash_lite", "shelby"]
    assert st_syn["polishers"]["enabled"] is False
    assert st_syn["final"]["enabled"] is True
    assert st_syn["final"]["calls"][0]["provider"] == "shelby" and st_syn["final"]["calls"][0]["priority"] is True
    assert st_syn["final"]["calls"][1]["provider"] == "flash_lite" and st_syn["final"]["calls"][1]["only_if_previous_failed"] is True
    # تكافؤ حسابي صريح بلا ثابت: max(40, 180)=180 + (180 + 40)=220 + هامش 20 = 420.0 بالضبط
    assert pipeline_config.stage_deadline_sec(st_syn["proposers"], cfg_syn["providers"]) == 180.0
    assert pipeline_config.stage_deadline_sec(st_syn["final"], cfg_syn["providers"]) == 220.0
    assert pipeline_config.pipeline_deadline_sec(cfg_syn) == 420.0

    # الحالة 3: off => proposers [shelby -> flash_lite fallback] وfinal معطلة
    settings.set_compare_mode("off")
    cfg_off = pipeline_config.get()
    st_off = {s["name"]: s for s in cfg_off["stages"]}
    assert [c["provider"] for c in st_off["proposers"]["calls"]] == ["shelby", "flash_lite"]
    assert st_off["proposers"]["calls"][1]["only_if_previous_failed"] is True
    assert st_off["final"]["enabled"] is False

    # الحالة 4: merged / split => مسودتان متوازيتان بدون سبك + وسم legacy_compare_mode
    settings.set_compare_mode("merged")
    cfg_mrg = pipeline_config.get()
    st_mrg = {s["name"]: s for s in cfg_mrg["stages"]}
    assert cfg_mrg["display"]["legacy_compare_mode"] == "merged"
    assert [c["provider"] for c in st_mrg["proposers"]["calls"]] == ["flash_lite", "shelby"]
    assert st_mrg["polishers"]["enabled"] is False and st_mrg["final"]["enabled"] is False


def test_real_pipeline_example_loads_and_strips_underscore_examples(fresh_env, monkeypatch):
    """القالب الحقيقي pipeline.example.json يُحمَّل كما هو ويتجاهل المزودات المبدوءة بـ _example_."""
    monkeypatch.setenv("LOCAL_POLISH_CMD", "python local_runner.py")
    shutil.copyfile(os.path.join(ROOT, "pipeline.example.json"), config.Config.PIPELINE_FILE)
    assert pipeline_config.has_disk_pipeline()
    cfg = pipeline_config.get(force=True)
    assert set(cfg["providers"].keys()) == {"flash_lite", "shelby"}
    assert "_example_groq" not in cfg["providers"]
    assert "_example_ollama" not in cfg["providers"]
    assert cfg["providers"]["shelby"]["_unresolved"] == []
    assert pipeline_config.pipeline_deadline_sec(cfg) == 420.0


def test_env_expansion_only_in_providers_and_unresolved_tracking(fresh_env, monkeypatch, caplog):
    """فك ${VAR} و${VAR:-default} في providers فقط، ورصد _unresolved وقت التحميل مع تحذير، وعدم مساس speakers."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_live_secret_value")
    monkeypatch.delenv("MISSING_PY_BIN", raising=False)
    monkeypatch.delenv("MISSING_DS_KEY", raising=False)

    doc = {
        "version": 1,
        "speakers": {"enabled": True, "tag_regex": "^${NOT_EXPANDED}(\\d+):"},
        "providers": {
            "groq_fast": {
                "type": "openai_compat",
                "base_url": "${GROQ_BASE:-https://api.groq.com/openai/v1}",
                "api_key": "${GROQ_API_KEY}",
                "model": "llama-3.3-70b-versatile",
            },
            "broken_cli": {
                "type": "local_cli",
                "command": "${MISSING_PY_BIN} worker.py",
            },
            "broken_ds": {
                "type": "openai_compat",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "${MISSING_DS_KEY}",
                "model": "",
            },
        },
    }
    with open(config.Config.PIPELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)

    with caplog.at_level(logging.WARNING, logger="alsami.pipeline_config"):
        cfg = pipeline_config.get(force=True)

    # speakers لا يُفك فيها ${...}
    assert cfg["speakers"]["tag_regex"] == "^${NOT_EXPANDED}(\\d+):"
    # groq_fast سليم بالكامل
    assert cfg["providers"]["groq_fast"]["api_key"] == "gsk_live_secret_value"
    assert cfg["providers"]["groq_fast"]["base_url"] == "https://api.groq.com/openai/v1"
    assert cfg["providers"]["groq_fast"]["_unresolved"] == []
    # broken_cli يكتشف MISSING_PY_BIN رغم بقاء " worker.py" غير فارغ
    assert cfg["providers"]["broken_cli"]["_unresolved"] == ["MISSING_PY_BIN"]
    # broken_ds يكتشف MISSING_DS_KEY وكذلك حقل model الفارغ
    assert cfg["providers"]["broken_ds"]["_unresolved"] == ["MISSING_DS_KEY", "model"]
    # التحذير سُجّل وقت التحميل ولم يسرّب المفتاح السليم
    joined_logs = "\n".join(r.getMessage() for r in caplog.records)
    assert "MISSING_PY_BIN" in joined_logs and "MISSING_DS_KEY" in joined_logs
    assert "gsk_live_secret_value" not in joined_logs


def test_repeat_expansion_and_max_cap(fresh_env, caplog):
    """تفكيك repeat إلى نداءات مرقّمة #1..#k وسقف MAX_CALL_REPEAT بدون temperature_step."""
    doc = {
        "stages": [
            {
                "name": "proposers",
                "enabled": True,
                "calls": [
                    {"provider": "flash_lite", "label": "فلاش", "repeat": 3, "temperature": 0.4},
                    {"provider": "flash_lite", "label": "ضخم", "repeat": 99},
                ],
            }
        ]
    }
    with open(config.Config.PIPELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)

    with caplog.at_level(logging.WARNING, logger="alsami.pipeline_config"):
        cfg = pipeline_config.get(force=True)

    calls = cfg["stages"][0]["calls"]
    assert len(calls) == 3 + pipeline_config.MAX_CALL_REPEAT
    first_three = calls[:3]
    assert [c["label"] for c in first_three] == ["فلاش #1", "فلاش #2", "فلاش #3"]
    assert [c["repeat_index"] for c in first_three] == [1, 2, 3]
    assert all(c["repeat_total"] == 3 and c["temperature"] == 0.4 and "repeat" not in c for c in first_three)
    assert any("تقليص repeat" in r.getMessage() for r in caplog.records)


def test_lazy_reload_keeps_previous_valid_on_corrupt_and_reverts_on_delete(fresh_env, caplog):
    """فحص (st_mtime_ns, st_size) بلا sleep: التالف يُبقي النسخة الصالحة بتحذير واحد لكل بصمة، والحذف يعيد الافتراضي."""
    p = config.Config.PIPELINE_FILE

    with open(p, "w", encoding="utf-8") as f:
        f.write('{"display": {"mode": "per_output"}}')
    assert pipeline_config.get(force=True)["display"]["mode"] == "per_output"

    # تعديل فوري بدون force=True يرجع النسخة المخبأة بسبب فاصل التهدئة (throttled)
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"display": {"mode": "mutated_throttled"}}')
    assert pipeline_config.get(force=False)["display"]["mode"] == "per_output"

    # كتابة JSON تالف بحجم مختلف مع force=True => يبقى "per_output" ويُسجَّل تحذير واحد فقط مهما تكرر get()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="alsami.pipeline_config"):
        with open(p, "w", encoding="utf-8") as f:
            f.write("{corrupt json payload   ")
        assert pipeline_config.get(force=True)["display"]["mode"] == "per_output"
        assert pipeline_config.get(force=True)["display"]["mode"] == "per_output"
    corrupt_warnings = [r for r in caplog.records if "تعذر تحميل pipeline.json" in r.getMessage()]
    assert len(corrupt_warnings) == 1

    # إصلاح الملف بحجم جديد => يتحدث فوراً بـ force=True
    with open(p, "w", encoding="utf-8") as f:
        f.write('{"display": {"mode": "single_live", "max_parallel": 6}}')
    reloaded = pipeline_config.get(force=True)
    assert reloaded["display"]["mode"] == "single_live" and reloaded["display"]["max_parallel"] == 6

    # حذف الملف من القرص => يعود للخط الافتراضي المدمج
    os.remove(p)
    after_del = pipeline_config.get(force=True)
    assert after_del["display"]["max_parallel"] == 4


def test_settings_overlap_warning_when_pipeline_json_present(fresh_env, caplog):
    """وجود pipeline.json مع compare_mode/local_polish_timeout في settings.json يسجل تحذيراً واحداً دون لمس panel.py."""
    settings.set_compare_mode("split")
    settings.set_value("local_polish_timeout", 95.0)
    with open(config.Config.PIPELINE_FILE, "w", encoding="utf-8") as f:
        json.dump({"display": {"mode": "single_live"}}, f)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="alsami.pipeline_config"):
        cfg = pipeline_config.get(force=True)
        # استدعاء ثانٍ من الكاش لا يكرر التحذير
        pipeline_config.get()

    assert cfg["display"]["mode"] == "single_live"
    overlap_logs = [r.getMessage() for r in caplog.records if "تجاهل المفاتيح المتداخلة" in r.getMessage()]
    assert len(overlap_logs) == 1
    assert "compare_mode" in overlap_logs[0] and "local_polish_timeout" in overlap_logs[0]


def test_dynamic_deadlines_and_queue_waiters_scale_with_repeats_and_no_runtime_imports(fresh_env, monkeypatch):
    """توسّع السقف الزمني وسعة الطابور مع repeat، وإثبات صفر استيراد لـ pipeline_config في ملفات التشغيل الأخرى."""
    monkeypatch.setenv("LOCAL_POLISH_CMD", "python local_runner.py")
    doc = {
        "stages": [
            {
                "name": "proposers",
                "enabled": True,
                "calls": [{"provider": "shelby", "label": "شلبي", "repeat": 4}],
            },
            {
                "name": "final",
                "enabled": True,
                "calls": [{"provider": "shelby", "label": "سبك شلبي", "priority": True}],
            },
        ]
    }
    with open(config.Config.PIPELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)

    cfg = pipeline_config.get(force=True)
    # 4 * 180ث في المرحلة الأولى + 180ث في النهائية + 20ث هامش = 920.0ث (> 420ث القديمة)
    assert pipeline_config.pipeline_deadline_sec(cfg) == 920.0
    # 5 نداءات على طابور gpu × BOT_WORKERS(2) + 2 = 12 (> 8 القديمة)
    assert pipeline_config.queue_max_waiters("gpu", cfg) == 12

    # شرط قبول T3a/T4 (DEC-P2-04/DEC-P2-08): صفر استيراد لـ pipeline_config إلا في delivery.py فقط (المصرح له في T4)
    for fn in os.listdir(ROOT):
        if fn.endswith(".py") and fn not in ("pipeline_config.py", "pipeline_defaults.py", "delivery.py"):
            src = open(os.path.join(ROOT, fn), encoding="utf-8").read()
            assert "pipeline_config" not in src and "pipeline_defaults" not in src, f"{fn} يستورد T3a قبل أوانه"


# =====================================================================
# اختبارات T6b-fix2: Fallback المقيد بالقائمة البيضاء لسمات Config (DEC-P2-08)
# =====================================================================

def test_whitelisted_config_fallback_when_env_empty(fresh_env, monkeypatch):
    """
    التحقق من أن المتغيرات المصرح بها في _CONFIG_FALLBACK_VARS (مثل LOCAL_POLISH_CMD)
    تتفكك تلقائياً من Config عند غيابها من os.environ وتبقى _unresolved فارغة (T6b-fix2 — DEC-P2-08).
    """
    monkeypatch.delenv("LOCAL_POLISH_CMD", raising=False)
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "python3 .local/shelby_polish.py")

    doc = {
        "version": 1,
        "providers": {
            "shelby": {
                "type": "local_cli",
                "command": "${LOCAL_POLISH_CMD}",
            }
        },
        "stages": [],
    }
    with open(config.Config.PIPELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f)

    cfg = pipeline_config.get(force=True)
    shelby_cfg = cfg["providers"]["shelby"]
    assert shelby_cfg["command"] == "python3 .local/shelby_polish.py"
    assert shelby_cfg.get("_unresolved") == []


def test_non_whitelisted_config_attributes_are_not_leaked(fresh_env, monkeypatch):
    """
    اختبار الأمان: التحقق من أن أي متغير خارج القائمة البيضاء (مثل WEB_ACCESS_TOKEN أو KEYS_JSON_FILE)
    لا يُسحب من Config ويتفكك إلى فارغ ويُدرج في _unresolved (T6b-fix2 — DEC-P2-08).
    """
    monkeypatch.delenv("WEB_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(config.Config, "WEB_ACCESS_TOKEN", "super_secret_token_123")

    doc = {
        "version": 1,
        "providers": {
            "custom_api": {
                "type": "openai_compat",
                "base_url": "https://api.example.com/v1",
                "api_key": "${WEB_ACCESS_TOKEN}",
            }
        },
        "stages": [],
    }
    with open(config.Config.PIPELINE_FILE, "w", encoding="utf-8") as f:
        json.dump(doc, f)

    cfg = pipeline_config.get(force=True)
    custom_cfg = cfg["providers"]["custom_api"]
    assert custom_cfg["api_key"] == ""
    assert "WEB_ACCESS_TOKEN" in custom_cfg.get("_unresolved", [])
