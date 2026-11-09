# -*- coding: utf-8 -*-
"""
اختبارات تكامل وصمود Wave 3 (B-08, B-10, Majors #1-#9):
- B-08: تقسيم UTF-16 وتجنب NUL sentinel + معالجة 429 في sendMessage / sendHtmlGetId / getUpdates.
- B-10: قالب local_provider_template.py مع ===USER_INPUT=== + كاشف No-Op بالتشابه وتحييد smart_verbatim وتعميمه.
- Major #1: local_polish_timeout كـ float في panel.py وapply_persisted_overrides.
- Major #2/#3: دقة عدادات text_assist وظهور السبك في _pipeline_text وdoctor_report.
- Major #4/#6: time.monotonic + _shelby_cli_lock timeout.
- Major #5: تنفيذ /polish في الخلفية بدون حجب خيط poller.
- Major #7: عزل keys.json التالف (.corrupt-*) + تشفير key_groups.json بـ sha256 وحذف القديم الخام.
- Major #8/#9: عزل dotenv في conftest + كاش jobs.load_state + إزالة Runtime.pool الميت.
"""

import json
import os
import subprocess
import sys
import threading
import time
import types

import pytest

import config
import delivery
import jobs
import observability
import panel
import settings
import text_assist


pytestmark = pytest.mark.usefixtures("fresh_env")


def _fake_resp(status, json_data=None, text="", headers=None):
    return types.SimpleNamespace(
        status_code=status,
        text=text or (json.dumps(json_data) if json_data else ""),
        headers=headers or {},
        json=(lambda: json_data if json_data is not None else {}),
    )


# ----------------------------------------------------------------------
# B-08: UTF-16 chunking, PUA sentinel, 429 retry
# ----------------------------------------------------------------------
def test_b08_utf16_chunking_emoji_and_nul_safe_html():
    import telegram_bot as tb

    # إيموجي (surrogate pair في UTF-16 = وحدتان لكل إيموجي)
    emoji_text = "😀" * 30  # len=30، utf16_len=60
    chunks = tb._chunk_text(emoji_text, limit=15)
    assert "".join(chunks) == emoji_text
    assert all(tb._utf16_len(c) <= 15 for c in chunks)

    # \x00 لا يُكسر وسوم HTML ولا يتمزق مع PUA sentinel
    html_body = "<pre>" + ("ألف\x00باء 😀 " * 18) + "</pre>"
    parts = tb._html_chunks(html_body, limit=80)
    assert len(parts) > 1
    for p in parts:
        assert tb._utf16_len(p) <= 80
        assert p.count("<pre>") == p.count("</pre>")
    reconstructed = "".join(p.replace("<pre>", "").replace("</pre>", "") for p in parts)
    assert "\x00" in reconstructed


def test_b08_429_retries_in_send_html_get_id_and_get_updates(monkeypatch):
    import telegram_bot as tb

    monkeypatch.setattr(tb.time, "sleep", lambda _s: None)
    calls = []

    def fake_post(url, json=None, data=None, files=None, timeout=None):
        calls.append((url, dict(json or {})))
        if len(calls) == 1:
            return _fake_resp(429, {"parameters": {"retry_after": 1}}, headers={"Retry-After": "1"})
        return _fake_resp(200, {"ok": True, "result": {"message_id": 777}})

    monkeypatch.setattr(tb, "_http_post", fake_post)
    mid = tb.send_html_get_id("TOK", "1", "<b>مرحبًا</b>")
    assert mid == 777
    assert len(calls) == 2
    assert calls[1][1].get("parse_mode") == "HTML"  # لم يسقط للنص الخام بسبب 429

    # getUpdates 429 يجب أن ينتظر retry_after عبر _wait قبل المحاولة التالية
    stop_ev = threading.Event()
    waits = []
    monkeypatch.setattr(tb, "_wait", lambda ev, sec: waits.append(sec) or (ev.set() if len(waits) >= 3 else None))
    monkeypatch.setattr(tb, "register_commands", lambda *a, **kw: None)

    class _FakeKM:
        def get_telegram_config(self):
            return ("TOK", "bot", "1")

    monkeypatch.setattr(tb, "key_manager", _FakeKM())
    get_calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        get_calls["n"] += 1
        if get_calls["n"] == 1:
            return _fake_resp(429, {"parameters": {"retry_after": 7}})
        stop_ev.set()
        return _fake_resp(200, {"ok": True, "result": []})

    monkeypatch.setattr(tb, "_http_get", fake_get)
    tb.main(stop_event=stop_ev, pool="compat-dummy")
    assert get_calls["n"] == 2
    assert any(w >= 7.0 for w in waits)


# ----------------------------------------------------------------------
# B-10: local_provider_template.py + is_shelby_noop + stats/delivery
# ----------------------------------------------------------------------
def test_b10_local_provider_template_splits_user_input():
    tpl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Antigravity", "local_provider_template.py")
    payload = "SYSTEM PROMPT SECRET INSTRUCTIONS\n\n===USER_INPUT===\nالنص الخام المطلوب تنظيمه فقط"
    res = subprocess.run(
        [sys.executable, tpl_path],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "النص الخام المطلوب تنظيمه فقط"
    assert "SYSTEM PROMPT" not in res.stdout


def test_b10_is_shelby_noop_similarity_and_smart_verbatim():
    long_raw = (
        "يا جماعه احنا النهارده عايزين نخلص شغل الباك اند كله بدري عشان نقدر "
        "نراجع ملفات الويب سيرفس وكمان نتاكد ان الاختبارات كلها ماشيه تمام من غير اي مشاكل"
    )
    # تعديل ترقيم/همزات بسيط على نص طويل -> No-Op
    long_punct = (
        "يا جماعة إحنا النهارده عايزين نخلص شغل الباك إند كله بدري عشان نقدر "
        "نراجع ملفات الويب سيرفس وكمان نتأكد ان الاختبارات كلها ماشية تمام من غير أي مشاكل."
    )
    assert text_assist.is_shelby_noop(long_raw, long_punct) is True
    # smart_verbatim مستثنى لأن النص المتطابق متوقع فيه
    assert text_assist.is_shelby_noop(long_raw, long_punct, role_id="smart_verbatim") is False
    # تنظيم فعلي باختلاف الصياغة والهيكل -> ليس No-Op
    real_polish = "## خطة العمل اليوم\n- إنهاء مهام الواجهة الخلفية مبكراً.\n- مراجعة خدمة الويب والتأكد من نجاح جميع الاختبارات."
    assert text_assist.is_shelby_noop(long_raw, real_polish) is False


def test_b10_polish_local_noop_falls_back_in_polish_and_does_not_double_count(monkeypatch, tmp_path):
    import importlib

    script = tmp_path / "noop_shelby.py"
    script.write_text(
        "import sys\n"
        "data = sys.stdin.read()\n"
        "raw = data.partition('===USER_INPUT===')[2]\n"
        "sys.stdout.write(raw.strip() + '.')\n",
        encoding="utf-8",
    )
    cmd = f'"{sys.executable}" "{script}"'
    # _no_network_polish يستبدل text_assist.polish بـ stub — نعيد تحميل الدالة الحقيقية مثل test_phase4_polish
    real_polish = importlib.reload(text_assist).polish
    monkeypatch.setattr(text_assist, "polish", real_polish)
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", cmd)
    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_CMD", cmd)
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", cmd)

    long_raw = (
        "يا جماعة إحنا عايزين نرتب أولويات المشروع النهارده قبل نهاية اليوم علشان نقدر "
        "نسلم التعديلات الجديدة في ميعادها من غير أي تأخير أو مشاكل تقنية"
    )
    monkeypatch.setattr(text_assist, "generate", lambda sys_t, usr_t, **kw: ("نسخة جيميني البديلة", None))

    noop_before = text_assist.stats()["local_noop"]
    res_p, err_p = text_assist.polish(long_raw)
    assert res_p == "نسخة جيميني البديلة" and err_p is None
    assert text_assist.stats()["local_noop"] == noop_before + 1

    # في وضع synthesizer مع استدعاء polish_local مباشرة، يتم زيادة local_noop مرة واحدة فقط (ليس مرتين)
    settings.set_compare_mode("synthesizer")
    sent_msgs = []
    edits = []
    sender = delivery.Sender(
        send_text=lambda txt, rt, pm=None, reply_markup=None: sent_msgs.append(txt) or len(sent_msgs),
        edit_text=lambda mid, txt, pm="HTML", reply_markup=None: edits.append(txt) or True,
    )
    noop_mid = text_assist.stats()["local_noop"]
    dres = delivery.deliver(
        raw_text=long_raw,
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=sender,
        reply_to_id=5,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني", None),
        local_polish_fn=None,
    )
    dres["compare_thread"].join(timeout=5)
    assert text_assist.stats()["local_noop"] == noop_mid + 1
    assert any("دون تنظيم فعلي" in e for e in edits)


# ----------------------------------------------------------------------
# Major #1 & #3: local_polish_timeout float & /status + /doctor synth metrics
# ----------------------------------------------------------------------
def test_major1_local_polish_timeout_float_validation_panel_and_overrides():
    ok, val = panel.validate_setting("local_polish_timeout", "8.5")
    assert ok and val == 8.5
    ok_int, val_int = panel.validate_setting("local_polish_timeout", "120")
    assert ok_int and isinstance(val_int, float) and val_int == 120.0
    for bad in ("nan", "inf", "-5", "0.5", "301.0", "abc"):
        ok_bad, _ = panel.validate_setting("local_polish_timeout", bad)
        assert not ok_bad

    txt, kb = panel.setting_view("local_polish_timeout")
    assert "1.0–300.0" in txt
    assert any(b["callback_data"] == "S:e:local_polish_timeout" for row in kb["inline_keyboard"] for b in row)

    panel.apply_setting("local_polish_timeout", 12.25)
    assert settings.get("local_polish_timeout") == 12.25
    config.Config.LOCAL_POLISH_TIMEOUT = 180.0
    assert panel.apply_persisted_overrides() >= 1
    assert config.Config.LOCAL_POLISH_TIMEOUT == 12.25


def test_major3_status_and_doctor_show_synthesizer_stats(monkeypatch):
    import telegram_bot as tb

    with text_assist._lock:
        saved = dict(text_assist._state)
        text_assist._state["synth_calls"] = 3
        text_assist._state["synth_ok"] = 0
        text_assist._state["synth_fail"] = 3
        text_assist._state["synth_last_error"] = "rate limit 429"
    try:
        pipe = tb._pipeline_text()
        assert "سبك ذهبي 0/3" in pipe and "فشل 3" in pipe

        lines, worst = observability.doctor_report(bot_state=tb.STATE, queue=None, watchdog=None, check_telegram=False)
        synth_lines = [ln for ln in lines if "التنظيم والسبك" in ln]
        assert synth_lines and "سبك 0/3" in synth_lines[0] and "rate limit 429" in synth_lines[0]
        assert synth_lines[0].startswith("⚠️")
        assert worst in (observability.WARN, observability.FAIL)
    finally:
        with text_assist._lock:
            text_assist._state.update(saved)


# ----------------------------------------------------------------------
# Major #4, #5, #6: monotonic clock, /polish async, _shelby_cli_lock timeout
# ----------------------------------------------------------------------
def test_major5_polish_command_runs_async_without_blocking_poller(monkeypatch):
    import telegram_bot as tb

    monkeypatch.setattr(config.Config, "PLAN_SYNC_FOR_TESTS", False)
    monkeypatch.setattr(tb.Config, "PLAN_SYNC_FOR_TESTS", False)
    sent = []
    monkeypatch.setattr(tb, "send_telegram_message", lambda tok, cid, txt, reply_to_id=None, parse_mode=None: sent.append(txt) or True)

    entered = threading.Event()
    release = threading.Event()

    def slow_polish(src, role_id, gl):
        entered.set()
        release.wait(timeout=3.0)
        return ("نص منظم غير متزامن", None)

    monkeypatch.setattr(text_assist, "polish", slow_polish)
    t0 = time.monotonic()
    tb.handle_text("T", "1", {"message_id": 99}, "/polish هذا نص تجريبي يحتاج تنظيم")
    dt = time.monotonic() - t0
    assert dt < 0.5, "/polish حجب خيط poller!"
    assert entered.wait(timeout=2.0)
    assert sent == []
    release.set()
    assert delivery.wait_background_threads(timeout=3.0) == 0
    assert any("نص منظم غير متزامن" in s for s in sent)


def test_major6_shelby_lock_timeout_and_major4_monotonic(monkeypatch, tmp_path):
    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_CMD", f'"{sys.executable}" -c "print(1)"')
    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_TIMEOUT", 0.15)
    assert text_assist._shelby_cli_lock.acquire(timeout=1.0)
    try:
        res, err = text_assist._call_local_provider("sys", "raw")
        assert res is None and "مشغول" in err and "القفل" in err
    finally:
        text_assist._shelby_cli_lock.release()

    # قفزة ساعة الجدار للوراء لا تعطل generate() لأن الحساب يستخدم time.monotonic()
    wall = {"v": 1000.0}
    mono = {"v": 10.0}
    monkeypatch.setattr(text_assist.time, "time", lambda: wall["v"])
    monkeypatch.setattr(text_assist.time, "monotonic", lambda: (mono.__setitem__("v", mono["v"] + 0.1) or mono["v"]))

    class _FakeKM:
        def get_active_keys(self, model=None):
            return [{"key": "AIzaTestKey1234567890", "name": "K"}]

        def record_success(self, k, model=None):
            pass

    monkeypatch.setattr(text_assist, "key_manager", _FakeKM())

    def _ok_post(*a, **kw):
        wall["v"] = 500.0  # قفزة NTP للخلف
        return _fake_resp(200, {"candidates": [{"content": {"parts": [{"text": "تم"}]}}]})

    monkeypatch.setattr(text_assist.requests, "post", _ok_post)
    out, reason = text_assist.generate("sys", "usr")
    assert out == "تم" and reason is None


# ----------------------------------------------------------------------
# Major #7, #8, #9: corrupt keys.json quarantine, hashed key_groups, jobs cache, no dead pool
# ----------------------------------------------------------------------
def test_major7_corrupt_keys_json_quarantined_and_legacy_groups_upgraded_to_hashes(fresh_env, monkeypatch):
    km_mod = fresh_env
    # 1) ملف keys.json تالف يجب عزله في .corrupt-* لا محوه صامتاً
    with open(km_mod.Config.KEYS_JSON_FILE, "w", encoding="utf-8") as f:
        f.write("{corrupt json !!!")
    km = km_mod.KeyManager()
    corrupt_files = [n for n in os.listdir(os.path.dirname(km_mod.Config.KEYS_JSON_FILE)) if ".corrupt-" in n]
    assert corrupt_files, "لم يُعزل ملف keys.json التالف!"

    # 2) ترقية key_groups.json قديم بمفتاح خام إلى بصمات sha256 تلقائياً
    k1 = "AIzaSyAAAAABBBBBCCCCCDDDDDEEEEEFFFFF1111"
    k2 = "AIzaSyAAAAABBBBBCCCCCDDDDDEEEEEFFFFF2222"
    km.add_key(k1, "K1")
    km.add_key(k2, "K2")
    os.makedirs(os.path.dirname(km_mod.Config.KEY_GROUPS_FILE), exist_ok=True)
    with open(km_mod.Config.KEY_GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump({"groups": {k1: "auto-1", k2: "auto-1"}}, f)

    km2 = km_mod.KeyManager()
    raw_disk = open(km_mod.Config.KEY_GROUPS_FILE, encoding="utf-8").read()
    assert k1 not in raw_disk and k2 not in raw_disk
    assert "sha256:" in raw_disk
    a2, b2 = km2.data["keys"]
    assert km2.group_of(a2) == km2.group_of(b2) == "auto-1"


def test_major9_jobs_load_state_cache_and_main_runtime_has_no_dead_pool(monkeypatch):
    import store
    import main as main_mod

    jobs._invalidate_cache()
    jobs.record("j_cache_1", "received", file_id="F1", file_unique_id="U1")
    read_calls = {"n": 0}
    orig_read = store.read_json_lines

    def counted_read(path, on_bad_line=None):
        read_calls["n"] += 1
        return orig_read(path, on_bad_line=on_bad_line)

    monkeypatch.setattr(store, "read_json_lines", counted_read)
    s1 = jobs.load_state()
    s2 = jobs.load_state()
    s3 = jobs.load_state()
    assert read_calls["n"] == 1
    assert "j_cache_1" in s1 and s2["j_cache_1"]["state"] == "received"
    s1["j_cache_1"]["events"].append("MUTATED")
    assert "MUTATED" not in s3["j_cache_1"]["events"]

    # Runtime يجب أن لا ينشئ pool تفريغ ميت
    rt = main_mod.Runtime("T", "1")
    assert not hasattr(rt, "pool")
