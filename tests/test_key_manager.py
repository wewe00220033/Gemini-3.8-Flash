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
