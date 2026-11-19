# -*- coding: utf-8 -*-
"""
اختبارات T2 (DEC-P2-08 / DEC-P2-09): عقد EngineResult والمشغّلات الأربعة في engines.py بلا شبكة.
"""

import json
import os
import subprocess
import threading

import pytest
import requests

import config
import engines
import interactions_client
import pipeline_config

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


class _FakeResp:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text or (json.dumps(payload, ensure_ascii=False) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_engine_result_validation_and_unresolved_env(fresh_env):
    """EngineResult يرفض أي حالة خارج الأربع، وcomplete يرفض _unresolved ومشغّل مجهول بـ fatal."""
    with pytest.raises(ValueError):
        engines.EngineResult(status="retry")

    ok_res = engines.EngineResult(status="ok", text="مرحباً", provider="p1", model="m1")
    assert ok_res.ok is True
    assert engines.EngineResult(status="ok", text="", provider="p1").ok is False

    # التحقق من ملاحظة T3a-2: شلبي المدمج بلا LOCAL_POLISH_CMD يحتفظ بـ ["LOCAL_POLISH_CMD"] حتى مع pipeline.json
    with open(config.Config.PIPELINE_FILE, "w", encoding="utf-8") as f:
        json.dump({"display": {"mode": "single_live"}}, f)
    cfg = pipeline_config.get(force=True)
    shelby_cfg = cfg["providers"]["shelby"]
    assert shelby_cfg["_unresolved"] == ["LOCAL_POLISH_CMD"]
    res_unres = engines.complete("shelby", shelby_cfg, "sys", "usr")
    assert res_unres.status == "fatal" and "LOCAL_POLISH_CMD" in res_unres.error

    res_unknown = engines.complete("x", {"type": "magic_protocol"}, "sys", "usr")
    assert res_unknown.status == "fatal" and "magic_protocol" in res_unknown.error


def test_gemini_driver_reuses_generate_and_maps_outcomes(km, monkeypatch):
    """GeminiApiDriver يلفّ text_assist.generate ويترجم النجاح و429 المؤقتة واليومية والموديل المحظور."""
    monkeypatch.setattr(engines.text_assist, "key_manager", km)
    km.add_key("AIzaSy" + "A" * 33, name="k1")
    prov = {"type": "gemini_api", "model": "gemini-3.5-flash-lite", "timeout_s": 20}

    # 1) ممنوع استخدام موديل تفريغ صوتي
    bad_mod = engines.complete("flash_lite", {**prov, "model": "gemini-3.6-flash"}, "sys", "usr")
    assert bad_mod.status == "fatal"

    # 2) نجاح اعتيادي
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: _FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "  نص منظم سليم  "}]}}]}),
    )
    ok = engines.complete("flash_lite", prov, "sys", "usr")
    assert ok.ok and ok.text == "نص منظم سليم"

    # 3) 429 بالدقيقة مع retryDelay -> retry_same + retry_after_s
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: _FakeResp(
            429,
            {"error": {"message": "Rate limit per minute", "details": [{"retryDelay": "14s"}]}},
        ),
    )
    rl = engines.complete("flash_lite", prov, "sys", "usr")
    assert rl.status == "retry_same" and rl.retry_after_s == 14.0

    # 4) إعادة مفتاح جديد ثم 429 يومي -> try_next
    km.add_key("AIzaSy" + "B" * 33, name="k2")
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: _FakeResp(429, {"error": {"message": "Quota exceeded per day"}}),
    )
    daily = engines.complete("flash_lite", prov, "sys", "usr")
    assert daily.status == "try_next"


def test_openai_compat_thinking_list_content_and_starvation_fatal(fresh_env):
    """OpenAICompatDriver: تجريد <think>، محتوى list، واستنزاف التوكنات في التفكير يرجع fatal."""
    prov = {
        "type": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1/",
        "api_key": "gsk_" + "z" * 32,
        "model": "deepseek-r1-distill-llama-70b",
        "extra_body": {"top_p": 0.9, "messages": "must_not_override"},
    }
    captured = {}

    def _ok_think(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _FakeResp(200, {"choices": [{"message": {"content": "<think>\nتفكير داخلي\n</think>\n\nالنتيجة النهائية"}}]})

    r1 = engines.complete("groq", prov, "sys_p", "usr_p", post_fn=_ok_think)
    assert r1.ok and r1.text == "النتيجة النهائية"
    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["body"]["top_p"] == 0.9
    assert isinstance(captured["body"]["messages"], list) and len(captured["body"]["messages"]) == 2

    # محتوى على شكل قائمة كتل (يتجاهل كتلة thinking ويأخذ text فقط)
    r2 = engines.complete(
        "groq",
        prov,
        "sys",
        "usr",
        post_fn=lambda *a, **kw: _FakeResp(
            200,
            {"choices": [{"message": {"content": [{"type": "thinking", "text": "سر"}, {"type": "text", "text": "النص الصافي"}]}}]},
        ),
    )
    assert r2.ok and r2.text == "النص الصافي"

    # Reasoning Starvation (أ): reasoning_content ممتلئ وcontent فارغ -> fatal
    r3 = engines.complete(
        "groq",
        prov,
        "sys",
        "usr",
        post_fn=lambda *a, **kw: _FakeResp(
            200,
            {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "تفكير طويل جداً..."}}]},
        ),
    )
    assert r3.status == "fatal" and "max_tokens" in r3.error

    # Reasoning Starvation (ب): <think> مفتوح مبتور بلا إغلاق بسبب انتهاء التوكنات -> لا يتسرب التفكير ويرجع fatal
    r4 = engines.complete(
        "groq",
        prov,
        "sys",
        "usr",
        post_fn=lambda *a, **kw: _FakeResp(
            200,
            {"choices": [{"finish_reason": "length", "message": {"content": "<think>سلسلة تفكير لم تكتمل بعد"}}]},
        ),
    )
    assert r4.status == "fatal"


def test_openai_compat_429_always_retry_same_except_daily_without_header(fresh_env):
    """429 مع retry_after (حتى >30ث أو بصيغة Groq 1m2.5s) ترجع retry_same؛ و429 يومية بلا ترويسة ترجع try_next."""
    secret_key = "gsk_" + "S" * 36
    prov = {
        "type": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1/chat/completions",
        "api_key": secret_key,
        "model": "llama-3.3-70b-versatile",
    }

    r_long = engines.complete(
        "groq",
        prov,
        "s",
        "u",
        post_fn=lambda *a, **kw: _FakeResp(
            429,
            {"error": {"message": "Rate limit reached"}},
            headers={"x-ratelimit-reset-tokens": "1m2.5s"},
        ),
    )
    assert r_long.status == "retry_same" and r_long.retry_after_s == 62.5

    r_daily = engines.complete(
        "groq",
        prov,
        "s",
        "u",
        post_fn=lambda *a, **kw: _FakeResp(429, {"error": {"message": "Insufficient_quota: daily limit reached"}}),
    )
    assert r_daily.status == "try_next" and r_daily.retry_after_s is None

    r_auth = engines.complete(
        "groq",
        prov,
        "s",
        "u",
        post_fn=lambda *a, **kw: _FakeResp(401, {"error": {"message": f"Invalid api_key: {secret_key}"}}),
    )
    assert r_auth.status == "fatal" and secret_key not in (r_auth.error or "")


def test_local_cli_formats_argv_limits_timeout_kill_and_shared_queue(fresh_env, monkeypatch):
    """LocalCliDriver: صيغ الإدخال الثلاث، سقف argv على Windows وPOSIX، قتل العملية عند المهلة، ومشاركة طابور gpu."""
    seen = {}

    class _FakeProc:
        def __init__(self, args, **kw):
            seen["args"] = args
            seen["kw"] = kw
            self.pid = 99991
            self.returncode = 0

        def communicate(self, input=None, timeout=None):
            seen["input"] = input
            seen["timeout"] = timeout
            if seen.get("hang"):
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            return ("مخرج محلي سليم\n", "")

        def kill(self):
            seen["killed"] = True

    killed_pids = []
    monkeypatch.setattr(engines.text_assist, "_kill_process_tree", lambda p: killed_pids.append(p.pid))

    # 1) stdin_json
    r_json = engines.complete(
        "local_j",
        {"type": "local_cli", "command": "python worker.py", "input_format": "stdin_json", "queue": "gpu"},
        "sys_txt",
        "usr_txt",
        process_factory=_FakeProc,
    )
    assert r_json.ok and r_json.text == "مخرج محلي سليم"
    assert json.loads(seen["input"]) == {"system": "sys_txt", "user": "usr_txt"}

    # 2) argv صغير يعمل، وargv ضخم يتجاوز سقف Windows (30000) وسقف POSIX (120KB بايت) يُرفض بـ fatal
    r_argv_ok = engines.complete(
        "local_a",
        {"type": "local_cli", "command": "python worker.py", "input_format": "argv", "queue": "gpu"},
        "sys_short",
        "usr_short",
        process_factory=_FakeProc,
    )
    assert r_argv_ok.ok and seen["args"][-2:] == ["sys_short", "usr_short"]

    r_win_big = engines.complete(
        "local_a",
        {"type": "local_cli", "command": "python worker.py", "input_format": "argv"},
        "sys",
        "ك" * 31000,
        process_factory=_FakeProc,
        _platform_name="nt",
    )
    assert r_win_big.status == "fatal" and "argv" in r_win_big.error

    # 70 ألف حرف عربي = 140KB UTF-8 (> 120KB سقف POSIX) رغم أنه بالمحارف < 120K
    r_posix_big = engines.complete(
        "local_a",
        {"type": "local_cli", "command": "python worker.py", "input_format": "argv"},
        "sys",
        "ك" * 70000,
        process_factory=_FakeProc,
        _platform_name="posix",
    )
    assert r_posix_big.status == "fatal" and "argv" in r_posix_big.error

    # 3) TimeoutExpired -> يقتل شجرة العمليات ويرجع retry_same
    seen["hang"] = True
    r_to = engines.complete(
        "local_j",
        {"type": "local_cli", "command": "python worker.py", "timeout_s": 5},
        "s",
        "u",
        process_factory=_FakeProc,
    )
    assert r_to.status == "retry_same" and killed_pids == [99991]
    seen["hang"] = False

    # 4) مشاركة نفس منسق الطابور "gpu" بين local_cli وopenai_compat (Ollama) وحساب lock_wait مع التكرار
    q = engines.get_queue_lock("gpu")
    assert q.acquire(timeout=0.1) is True
    try:
        r_busy = engines.complete(
            "ollama_gpu",
            {
                "type": "openai_compat",
                "base_url": "http://127.0.0.1:11434/v1",
                "api_key": "ollama",
                "model": "qwen2.5:7b",
                "queue": "gpu",
                "timeout_s": 0.05,
                "lock_wait_s": 0.02,
            },
            "s",
            "u",
            post_fn=lambda *a, **kw: _FakeResp(200, {"choices": [{"message": {"content": "x"}}]}),
        )
        assert r_busy.status == "try_next" and "مشغول" in r_busy.error
    finally:
        q.release()

    assert engines._effective_lock_wait({"lock_wait_s": 45.0}, 180.0, {"expected_queue_calls": 4}) == 540.0


def test_interactions_driver_ignores_feature_plan_and_keeps_legacy_run_intact(km, monkeypatch):
    """InteractionsDriver يعمل حتى مع FEATURE_PLAN=False وباسم وكيل مخصص، بينما run() المباشرة تحترم FEATURE_PLAN."""
    monkeypatch.setattr(interactions_client, "key_manager", km)
    monkeypatch.setattr(config.Config, "FEATURE_PLAN", False)
    km.add_key("AIzaSy" + "C" * 33, name="k_ag")

    # الاستدعاء القديم المباشر لـ interactions_client.run بدون معاملات يظل محترماً لـ FEATURE_PLAN=False
    legacy_txt, legacy_err = interactions_client.run("اختبار")
    assert legacy_txt is None and "FEATURE_PLAN=0" in legacy_err

    # بينما المشغّل الموحد يتجاوز FEATURE_PLAN ويمرر اسم الوكيل المخصص
    sent_payload = {}

    def _fake_post(url, json=None, headers=None, timeout=None):
        sent_payload.update(json or {})
        return _FakeResp(200, {"outputs": [{"text": "خطة مسبوكة بنجاح"}]})

    res = engines.complete(
        "ag_custom",
        {"type": "interactions_api", "agent": "custom-agent-v2", "timeout_s": 30},
        "تعليمات",
        "مدخل",
        post_fn=_fake_post,
    )
    assert res.ok and res.text == "خطة مسبوكة بنجاح"
    assert sent_payload["agent"] == "custom-agent-v2"


def test_session_send_guard_blocks_external_hosts_including_localhost_spoof_and_zero_runtime_imports(fresh_env):
    """حارس Session.send يمنع أي اتصال خارجي حقيقي عبر netsession (بما فيه localhost.evil.com) وصفر استيراد مبكر."""
    for bad_url in ("https://api.groq.com/openai/v1", "http://localhost.evil.com/v1"):
        res = engines.complete(
            "spoof",
            {"type": "openai_compat", "base_url": bad_url, "api_key": "gsk_" + "k" * 20, "model": "m"},
            "s",
            "u",
        )
        # Session.send يرفع AssertionError فيلتقطه المشغّل كـ fatal (دون فتح أي مقبس شبكي)
        assert res.status == "fatal" and "AssertionError" in (res.error or "")

    # تأكيد التوازي الآمن على BoundedSemaphore بدون سباقات
    results = []

    def _worker():
        r = engines.complete(
            "groq_conc",
            {"type": "openai_compat", "base_url": "https://api.groq.com/openai/v1", "api_key": "gsk_" + "p" * 20, "model": "m", "max_concurrent": 2},
            "s",
            "u",
            post_fn=lambda *a, **kw: _FakeResp(200, {"choices": [{"message": {"content": "ok"}}]}),
        )
        results.append(r.ok)

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 5

    # شرط قبول T2: صفر استيراد لـ engines أو engines_openai من أي ملف تشغيلي آخر في الجذر
    for fn in os.listdir(ROOT):
        if fn.endswith(".py") and fn not in ("engines.py", "engines_openai.py"):
            src = open(os.path.join(ROOT, fn), encoding="utf-8").read()
            assert "import engines" not in src and "from engines" not in src, f"{fn} يستورد engines قبل أوانه"


def test_get_semaphore_replaces_on_cap_change_ki_01():
    """KI-01 (P3-02): مفتاح get_semaphore هو provider_name فقط، وتغيّر cap يستبدل السيمافور دون تراكم."""
    engines.reset_state()
    sem1 = engines.get_semaphore("prov_x", max_concurrent=2)
    assert len(engines._semaphores) == 1
    # استدعاء بنفس السعة يعيد نفس الكائن
    sem1_again = engines.get_semaphore("prov_x", max_concurrent=2)
    assert sem1_again is sem1
    # استدعاء بسعة مختلفة يستبدل السيمافور ولا يزيد عدد العناصر في القاموس
    sem2 = engines.get_semaphore("prov_x", max_concurrent=3)
    assert sem2 is not sem1
    assert len(engines._semaphores) == 1

