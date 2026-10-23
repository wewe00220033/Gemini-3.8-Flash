# -*- coding: utf-8 -*-
"""اختبارات KeyManager: استخراج المفاتيح، الإضافة، التهدئة، إعادة التنشيط، دمج البيئة."""
import json
import os
import time

VALID_AIZA = "AIzaSy" + "A" * 33          # 39 حرف — صيغة Google الحالية
VALID_AQ = "AQ." + "b" * 45


# ---------- extract_key_from_text ----------

def test_extract_plain_aiza(fresh_env):
    key, name = fresh_env.KeyManager.extract_key_from_text(VALID_AIZA)
    assert key == VALID_AIZA and name is None


def test_extract_with_arabic_name(fresh_env):
    key, name = fresh_env.KeyManager.extract_key_from_text(f"مشروع 3: {VALID_AIZA}")
    assert key == VALID_AIZA and name == "مشروع 3"


def test_extract_aq_with_name_equals(fresh_env):
    key, name = fresh_env.KeyManager.extract_key_from_text(f"Project X = {VALID_AQ}")
    assert key == VALID_AQ and name == "Project X"


def test_extract_rejects_plain_text(fresh_env):
    assert fresh_env.KeyManager.extract_key_from_text("hello world") == (None, None)


def test_extract_rejects_too_short(fresh_env):
    assert fresh_env.KeyManager.extract_key_from_text("AIzaSy" + "A" * 32) == (None, None)


# ---------- add_key ----------

def test_add_key_new_and_duplicate(km):
    entry, is_new = km.add_key(VALID_AIZA, "P1")
    assert is_new and entry["name"] == "P1" and entry["status"] == "active"
    entry2, is_new2 = km.add_key(VALID_AIZA, "Renamed")
    assert not is_new2 and entry2["name"] == "Renamed"
    assert len(km.data["keys"]) == 1


def test_add_key_reactivates_exhausted(km):
    km.add_key(VALID_AIZA)
    km.mark_key_exhausted(VALID_AIZA)
    assert km.data["keys"][0]["status"] == "exhausted"
    km.add_key(VALID_AIZA)
    assert km.data["keys"][0]["status"] == "active"


def test_add_key_persists_to_disk(km, fresh_env):
    km.add_key(VALID_AIZA, "Disk")
    with open(fresh_env.Config.KEYS_JSON_FILE, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["keys"][0]["name"] == "Disk"


def test_save_sets_restrictive_permissions(km, fresh_env):
    """P3: keys.json يحوي أسراراً → صلاحيات 600 (يُتخطى على Windows)."""
    if os.name == "nt":
        import pytest
        pytest.skip("POSIX permissions only")
    km.add_key(VALID_AIZA)
    mode = os.stat(fresh_env.Config.KEYS_JSON_FILE).st_mode & 0o777
    assert mode == 0o600, f"keys.json permissions are {oct(mode)}, expected 0o600"


# ---------- cooldown lifecycle ----------

def test_mark_exhausted_sets_cooldown(km, fresh_env):
    km.add_key(VALID_AIZA)
    before = time.time()
    km.mark_key_exhausted(VALID_AIZA)
    k = km.data["keys"][0]
    assert k["status"] == "exhausted"
    assert k["exhausted_until"] >= before + fresh_env.Config.KEY_COOLDOWN_SECONDS - 1


def test_get_active_keys_filters_exhausted(km):
    km.add_key(VALID_AIZA, "A")
    km.add_key(VALID_AQ, "B")
    km.mark_key_exhausted(VALID_AIZA)
    assert [k["name"] for k in km.get_active_keys()] == ["B"]


def test_exhausted_key_reactivates_after_cooldown(km):
    km.add_key(VALID_AIZA)
    km.mark_key_exhausted(VALID_AIZA)
    km.data["keys"][0]["exhausted_until"] = time.time() - 1
    active = km.get_active_keys()
    assert len(active) == 1 and active[0]["status"] == "active"


def test_all_exhausted_behaviour(km):
    """P14: السلوك الحالي يرجع كل المفاتيح حتى المستهلكة. سيُشدَّد في المرحلة 2 (بند 2.7)."""
    km.add_key(VALID_AIZA)
    km.mark_key_exhausted(VALID_AIZA)
    assert isinstance(km.get_active_keys(), list)


# ---------- record_success ----------

def test_record_success_increments(km):
    km.add_key(VALID_AIZA)
    km.record_success(VALID_AIZA)
    km.record_success(VALID_AIZA)
    assert km.data["keys"][0]["success_count"] == 2
    assert km.data["keys"][0]["last_used"]


# ---------- env bootstrap / merge ----------

def test_bootstrap_from_env_dedupes(fresh_env, monkeypatch):
    """بدون keys.json: مفاتيح البيئة تُقرأ وتُزال المكررات."""
    monkeypatch.setenv("GEMINI_API_KEYS", f"{VALID_AIZA},{VALID_AQ},{VALID_AIZA}")
    if os.path.exists(fresh_env.Config.KEYS_JSON_FILE):
        os.remove(fresh_env.Config.KEYS_JSON_FILE)   # الـ singleton عند الاستيراد أنشأه فارغاً
    mgr = fresh_env.KeyManager()
    assert [k["key"] for k in mgr.data["keys"]] == [VALID_AIZA, VALID_AQ]


def test_env_keys_merged_when_json_exists(fresh_env, monkeypatch):
    """P29: مفتاح جديد في البيئة يجب أن يُدمج حتى لو keys.json موجود (بند 1.7)."""
    mgr = fresh_env.KeyManager()
    mgr.add_key(VALID_AIZA, "Old")
    monkeypatch.setenv("GEMINI_API_KEYS", f"{VALID_AIZA},{VALID_AQ}")
    mgr2 = fresh_env.KeyManager()         # إعادة تشغيل
    keys = [k["key"] for k in mgr2.data["keys"]]
    assert VALID_AQ in keys, "new env key ignored because keys.json exists (P29)"
    assert mgr2.data["keys"][0]["name"] == "Old", "existing state must be preserved"


def test_status_summary_masks_keys(km):
    km.add_key(VALID_AIZA, "Masked")
    summary = km.get_status_summary()
    assert VALID_AIZA not in summary
    assert VALID_AIZA[:8] in summary and VALID_AIZA[-4:] in summary


# ---------- المرحلة 2: تهدئة لكل (مفتاح، موديل) + عدّاد يومي ----------

M38, M36, M35 = "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-transcribe"


def test_per_model_exhaustion_keeps_other_models_available(km):
    """P15: 429 على 3.8 يجب ألا يقفل 3.6 و 3.5 على نفس المفتاح."""
    km.add_key(VALID_AIZA, "A")
    km.mark_key_exhausted(VALID_AIZA, model=M38)
    assert km.get_active_keys(model=M38) == []
    assert [k["name"] for k in km.get_active_keys(model=M36)] == ["A"]
    assert [k["name"] for k in km.get_active_keys(model=M35)] == ["A"]


def test_all_models_exhausted_marks_key_exhausted(km):
    km.add_key(VALID_AIZA, "A")
    for m in (M38, M36, M35):
        km.mark_key_exhausted(VALID_AIZA, model=m)
    assert km.data["keys"][0]["status"] == "exhausted"
    assert km.get_active_keys(model=M38) == []


def test_all_exhausted_returns_empty_not_all(km):
    """P14 (بند 2.7): لا محاولة يائسة تجدد القفل — قائمة فاضية + وقت أقرب عودة."""
    km.add_key(VALID_AIZA, "A")
    km.mark_key_exhausted(VALID_AIZA)
    assert km.get_active_keys(model=M38) == []
    wait = km.earliest_availability()
    assert wait is not None and wait > 0


def test_retry_after_overrides_default_cooldown(km, fresh_env):
    km.add_key(VALID_AIZA, "A")
    km.mark_key_exhausted(VALID_AIZA, model=M38, retry_after=42)
    until = km.data["keys"][0]["exhausted_models"][M38]
    assert until - time.time() <= 43
    assert until - time.time() < fresh_env.Config.KEY_COOLDOWN_SECONDS


def test_per_model_cooldown_expires(km):
    km.add_key(VALID_AIZA, "A")
    km.mark_key_exhausted(VALID_AIZA, model=M38, retry_after=1)
    km.data["keys"][0]["exhausted_models"][M38] = time.time() - 1
    assert [k["name"] for k in km.get_active_keys(model=M38)] == ["A"]
    assert M38 not in km.data["keys"][0].get("exhausted_models", {})


def test_daily_usage_counter_and_quota_block(km, fresh_env):
    """P32 (بند 2.15): عدّاد يومي لكل (مفتاح، موديل) يمنع الاستخدام بعد نفاد الكوتا."""
    km.add_key(VALID_AIZA, "A")
    quota = fresh_env.Config.MODEL_DAILY_QUOTA[M38]
    for _ in range(quota):
        km.record_success(VALID_AIZA, model=M38)
    assert km.remaining_today(km.data["keys"][0])[M38] == 0
    assert km.get_active_keys(model=M38) == []
    assert km.get_active_keys(model=M36)            # الموديل الآخر لا يتأثر


def test_daily_usage_keeps_only_today(km):
    km.add_key(VALID_AIZA, "A")
    km.data["keys"][0]["daily_usage"] = {"2000-01-01": {M38: 5}}
    km.record_success(VALID_AIZA, model=M38)
    usage = km.data["keys"][0]["daily_usage"]
    assert "2000-01-01" not in usage and len(usage) == 1


def test_legacy_keys_json_without_new_fields_still_works(km):
    """توافق خلفي: مفتاح بالبنية القديمة (بدون exhausted_models/daily_usage)."""
    km.data["keys"] = [{"id": 1, "key": VALID_AIZA, "name": "Legacy", "status": "active",
                        "exhausted_until": 0, "success_count": 3, "last_used": None}]
    assert [k["name"] for k in km.get_active_keys(model=M38)] == ["Legacy"]
    assert km.remaining_today(km.data["keys"][0])[M38] > 0


def test_remove_and_rename_key(km):
    km.add_key(VALID_AIZA, "A")
    km.add_key(VALID_AQ, "B")
    assert km.rename_key("a", "Alpha")["name"] == "Alpha"
    removed = km.remove_key("Alpha")
    assert removed and removed["key"] == VALID_AIZA
    assert km.remove_key("nope") is None
    assert [k["name"] for k in km.data["keys"]] == ["B"]


def test_status_summary_is_plain_and_shows_reset(km):
    km.add_key(VALID_AIZA, "A")
    s = km.get_status_summary()
    assert "**" not in s and "`" not in s          # لا Markdown — آمن للإرسال
    assert "تصفير الكوتا" in s and "3.8 Flash" in s


# ---------- المرحلة 3 ----------

def test_extract_key_requires_word_boundary(fresh_env):
    """P27: مفتاح ملتصق بأحرف إضافية لا يُقتطع بصمت."""
    glued = "AIzaSy" + "A" * 40           # أطول من 39 بـ 7 أحرف
    assert fresh_env.KeyManager.extract_key_from_text(glued) == (None, None)
    inside = f"prefix{VALID_AIZA}"
    assert fresh_env.KeyManager.extract_key_from_text(inside) == (None, None)
    spaced = f"key: {VALID_AIZA} ."
    assert fresh_env.KeyManager.extract_key_from_text(spaced)[0] == VALID_AIZA


def test_dotenv_loader(tmp_path, monkeypatch):
    """3.3: .env يُقرأ بدون مكتبات ولا يكتب فوق البيئة الموجودة."""
    import config
    env = tmp_path / ".env"
    env.write_text('# تعليق\nFOO_TEST=bar\nQUOTED_TEST="with space"\nexport EXP_TEST=1\nEXISTING_TEST=from_file\n', encoding="utf-8")
    monkeypatch.delenv("FOO_TEST", raising=False)
    monkeypatch.delenv("QUOTED_TEST", raising=False)
    monkeypatch.delenv("EXP_TEST", raising=False)
    monkeypatch.setenv("EXISTING_TEST", "from_env")
    n = config._load_dotenv(str(env))
    assert n == 3
    import os as _os
    assert _os.environ["FOO_TEST"] == "bar"
    assert _os.environ["QUOTED_TEST"] == "with space"
    assert _os.environ["EXP_TEST"] == "1"
    assert _os.environ["EXISTING_TEST"] == "from_env"
    for k in ("FOO_TEST", "QUOTED_TEST", "EXP_TEST"):
        monkeypatch.delenv(k, raising=False)
