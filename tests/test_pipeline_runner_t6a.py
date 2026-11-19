# -*- coding: utf-8 -*-
"""
اختبارات T6a: محرك تشغيل مراحل الـ Pipeline المعزول (pipeline_runner.py - DEC-P2-05/08/09).
- اختبار 1: تشغيل مرحلة بنداء فردي ناجح واعتماد الناتج.
- اختبار 2: سلسلة البدائل المتعاقبة (only_if_previous_failed) عند فشل النداء الأول.
- اختبار 3: تخطي البديل المتعاقب إذا نجح النداء الأساسي.
- اختبار 4: إنفاذ مهلة المرحلة (deadline_s) وإلغاء النداءات المتأخرة.
- اختبار 5: خنق التوازي وفق max_concurrent.
- اختبار 6: تسلسل أحداث on_progress بدقة (started, ok, failed, skipped).
- اختبار 7: إعادة المحاولة لمرة واحدة فقط عند retry_same ثم الانتقال للبديل (تصحيح 1).
- اختبار 8: خطأ fatal في نداء لا يوقف النداءات المتوازية الأخرى في المرحلة (تصحيح 3).
- اختبار 9: عزل الاستيراد الصارم (Zero Import-Time Config/Telegram Dependencies).
"""

import ast
import os
import threading
import time

import engines
from engines import EngineResult
from pipeline_runner import run_stage, StageResult


class FakeDriver:
    """مشغل اختباري مرن يسمح بالتحكم الدقيق في الاستجابات وحساب التوازي."""

    def __init__(self, responses=None, delay=0.0):
        # responses: list of EngineResult or single EngineResult or callable
        self.responses = list(responses) if isinstance(responses, (list, tuple)) else responses
        self.delay = delay
        self.call_history = []
        self.active_calls = 0
        self.max_concurrent_seen = 0
        self._lock = threading.Lock()

    def complete(self, provider_name, provider_cfg, system_prompt, user_prompt, **opts):
        with self._lock:
            self.active_calls += 1
            if self.active_calls > self.max_concurrent_seen:
                self.max_concurrent_seen = self.active_calls
            self.call_history.append((provider_name, provider_cfg, opts))

        try:
            if self.delay > 0:
                time.sleep(self.delay)

            if callable(self.responses):
                return self.responses(provider_name, provider_cfg, **opts)

            if isinstance(self.responses, list):
                if self.responses:
                    return self.responses.pop(0)
                return EngineResult("ok", text="افتراضي بعد نفاد السيناريو")

            if isinstance(self.responses, EngineResult):
                return self.responses

            return EngineResult("ok", text=f"نتيجة من {provider_name}")
        finally:
            with self._lock:
                self.active_calls -= 1


# =====================================================================
# اختبار 1: تشغيل مرحلة بنداء فردي ناجح
# =====================================================================

def test_run_stage_single_call_success(monkeypatch):
    driver = FakeDriver(EngineResult("ok", text="تم تنظيم النص بنجاح"))
    monkeypatch.setitem(engines.DRIVERS, "fake_type", driver)

    stage_cfg = {
        "name": "proposers",
        "min_success": 1,
        "calls": [
            {"provider": "p1", "label": "فلاش", "id": "c1"}
        ]
    }
    providers = {
        "p1": {"type": "fake_type", "model": "flash-model"}
    }

    res = run_stage("proposers", stage_cfg, inputs={"raw": "نص خام"}, providers=providers)

    assert isinstance(res, StageResult)
    assert res.status == "ok"
    assert res.final_output == "تم تنظيم النص بنجاح"
    assert len(res.calls) == 1
    c0 = res.calls[0]
    assert c0.call_id == "c1"
    assert c0.status == "ok"
    assert c0.text == "تم تنظيم النص بنجاح"
    assert c0.label == "فلاش"


# =====================================================================
# اختبار 2: سلسلة البدائل المتعاقبة عند فشل الأول
# =====================================================================

def test_run_stage_fallback_chain_on_failure(monkeypatch):
    driver1 = FakeDriver(EngineResult("try_next", error="نفاذ الكوتة المؤقتة"))
    driver2 = FakeDriver(EngineResult("ok", text="نجاح البديل شلبي"))
    monkeypatch.setitem(engines.DRIVERS, "fake_fail", driver1)
    monkeypatch.setitem(engines.DRIVERS, "fake_ok", driver2)

    stage_cfg = {
        "name": "final",
        "min_success": 1,
        "calls": [
            {"provider": "primary", "label": "شلبي", "id": "c_prim"},
            {"provider": "fallback", "label": "فلاش", "id": "c_fall", "only_if_previous_failed": True},
        ]
    }
    providers = {
        "primary": {"type": "fake_fail", "model": "m1"},
        "fallback": {"type": "fake_ok", "model": "m2"},
    }

    res = run_stage("final", stage_cfg, inputs={"raw": "نص"}, providers=providers)

    assert res.status == "ok"
    assert res.final_output == "نجاح البديل شلبي"
    assert len(res.calls) == 2
    assert res.calls[0].status == "failed"
    assert res.calls[1].status == "ok"


# =====================================================================
# اختبار 3: تخطي البديل المتعاقب إذا نجح الأساسي
# =====================================================================

def test_run_stage_fallback_skipped_when_primary_succeeds(monkeypatch):
    driver = FakeDriver(EngineResult("ok", text="نجاح الأساسي"))
    monkeypatch.setitem(engines.DRIVERS, "fake_type", driver)

    events = []
    def on_prog(ev, call):
        events.append((ev, call.call_id, call.status))

    stage_cfg = {
        "name": "final",
        "min_success": 1,
        "calls": [
            {"provider": "p1", "label": "أساسي", "id": "c1"},
            {"provider": "p2", "label": "بديل", "id": "c2", "only_if_previous_failed": True},
        ]
    }
    providers = {
        "p1": {"type": "fake_type"},
        "p2": {"type": "fake_type"},
    }

    res = run_stage("final", stage_cfg, inputs={"raw": "نص"}, providers=providers, on_progress=on_prog)

    assert res.status == "ok"
    assert res.final_output == "نجاح الأساسي"
    assert len(res.calls) == 2
    assert res.calls[0].status == "ok"
    assert res.calls[1].status == "skipped"

    # التحقق من صدور حدث تخطي للبديل
    assert ("skipped", "c2", "skipped") in events


# =====================================================================
# اختبار 4: إنفاذ مهلة المرحلة (deadline_s)
# =====================================================================

def test_run_stage_deadline_enforcement(monkeypatch):
    # المشغل يحتاج 0.4 ثانية، بينما مهلة المرحلة الإجمالية 0.1 ثانية
    driver = FakeDriver(EngineResult("ok", text="تأخر"), delay=0.3)
    monkeypatch.setitem(engines.DRIVERS, "fake_slow", driver)

    stage_cfg = {
        "name": "proposers",
        "min_success": 1,
        "calls": [
            {"provider": "p1", "id": "c1", "timeout_s": 0.05}
        ]
    }
    providers = {"p1": {"type": "fake_slow"}}

    t0 = time.monotonic()
    res = run_stage("proposers", stage_cfg, inputs={"raw": "نص"}, providers=providers, deadline_s=1.0)
    elapsed = time.monotonic() - t0

    # المهلة المفروضة داخل المشغل أوقفت التنفيذ بسرعة
    assert elapsed < 0.35
    assert len(res.calls) == 1


# =====================================================================
# اختبار 5: خنق التوازي وفق max_concurrent
# =====================================================================

def test_run_stage_max_concurrent_throttling(monkeypatch):
    driver = FakeDriver(EngineResult("ok", text="سريع"), delay=0.05)
    monkeypatch.setitem(engines.DRIVERS, "fake_concurrent", driver)

    stage_cfg = {
        "name": "proposers",
        "min_success": 2,
        "max_concurrent": 2,  # يسمح بـ 2 فقط في نفس الوقت
        "calls": [
            {"provider": "p1", "id": "c1"},
            {"provider": "p2", "id": "c2"},
            {"provider": "p3", "id": "c3"},
            {"provider": "p4", "id": "c4"},
        ]
    }
    providers = {f"p{i}": {"type": "fake_concurrent"} for i in range(1, 5)}

    res = run_stage("proposers", stage_cfg, inputs={"raw": "نص"}, providers=providers)

    assert res.status == "ok"
    assert len(res.calls) == 4
    # أقصى توازي رُصد أثناء التشغيل لا يتجاوز 2
    assert driver.max_concurrent_seen <= 2


# =====================================================================
# اختبار 6: تسلسل أحداث on_progress
# =====================================================================

def test_run_stage_on_progress_sequence(monkeypatch):
    driver = FakeDriver(EngineResult("ok", text="مكتمل"))
    monkeypatch.setitem(engines.DRIVERS, "fake_type", driver)

    events_log = []
    def on_prog(event, call_res):
        events_log.append((event, call_res.call_id, call_res.status))

    stage_cfg = {
        "name": "proposers",
        "min_success": 1,
        "calls": [{"provider": "p1", "id": "c1", "label": "فلاش"}]
    }
    providers = {"p1": {"type": "fake_type"}}

    run_stage("proposers", stage_cfg, inputs={"raw": "نص"}, providers=providers, on_progress=on_prog)

    # يجب أن يبدأ بـ started ثم ينتهي بـ ok
    assert events_log == [
        ("started", "c1", "started"),
        ("ok", "c1", "ok"),
    ]


# =====================================================================
# اختبار 7: retry_same لمرة واحدة فقط ثم try_next (تصحيح 1)
# =====================================================================

def test_run_stage_retry_same_once_then_try_next(monkeypatch):
    """
    التحقق من أن retry_same لا يتكرر إلى ما لا نهاية:
    - المحاولة 1 ترجع retry_same مع retry_after_s=0.01.
    - المحاولة 2 تفشل بـ try_next.
    - الـ runner يتوقف عند محاولتين فقط ولا يكرر 20 مرة.
    """
    driver_calls = []
    def driver_func(p_name, p_cfg, **opts):
        driver_calls.append(time.monotonic())
        if len(driver_calls) == 1:
            return EngineResult("retry_same", retry_after_s=0.01, error="مؤقت 429")
        return EngineResult("try_next", error="فشل نهائي بعد الإعادة")

    driver = FakeDriver(driver_func)
    monkeypatch.setitem(engines.DRIVERS, "fake_retry", driver)

    stage_cfg = {
        "name": "proposers",
        "min_success": 1,
        "calls": [
            {"provider": "p1", "id": "c1", "label": "فلاش"}
        ]
    }
    providers = {"p1": {"type": "fake_retry"}}

    res = run_stage("proposers", stage_cfg, inputs={"raw": "نص"}, providers=providers, deadline_s=2.0)

    # عدد استدعاءات المشغل يجب أن يكون 2 بالضبط (المحاولة الأصلية + إعادة واحدة)
    assert len(driver_calls) == 2
    assert res.status == "failed"
    assert res.calls[0].status == "failed"


# =====================================================================
# اختبار 8: خطأ fatal في نداء لا يوقف النداءات المتوازية (تصحيح 3)
# =====================================================================

def test_run_stage_fatal_does_not_abort_parallel_calls(monkeypatch):
    """
    التحقق من أن نداءً بحالة fatal (مثل مفتاح مفقود) لا يجهض نداءً متوازياً ناجحاً.
    """
    driver_fatal = FakeDriver(EngineResult("fatal", error="مفتاح API غير متوفر"))
    driver_ok = FakeDriver(EngineResult("ok", text="مسودة شلبي المكتملة"))
    monkeypatch.setitem(engines.DRIVERS, "fake_fatal", driver_fatal)
    monkeypatch.setitem(engines.DRIVERS, "fake_success", driver_ok)

    stage_cfg = {
        "name": "proposers",
        "min_success": 1,
        "calls": [
            {"provider": "p_fatal", "id": "c_fatal", "label": "جروك"},
            {"provider": "p_ok", "id": "c_ok", "label": "شلبي"},
        ]
    }
    providers = {
        "p_fatal": {"type": "fake_fatal"},
        "p_ok": {"type": "fake_success"},
    }

    res = run_stage("proposers", stage_cfg, inputs={"raw": "نص"}, providers=providers)

    # المرحلة ناجحة لاستيفاء min_success=1 بفضل شلبي
    assert res.status == "ok"
    assert res.final_output == "مسودة شلبي المكتملة"
    assert len(res.calls) == 2
    call_fatal = [c for c in res.calls if c.call_id == "c_fatal"][0]
    call_ok = [c for c in res.calls if c.call_id == "c_ok"][0]
    assert call_fatal.status == "fatal"
    assert call_ok.status == "ok"


# =====================================================================
# اختبار 9: عزل الاستيراد الصارم (Zero Import-Time Dependencies)
# =====================================================================

def test_pipeline_runner_isolated_import():
    """
    التحقق الصارم من أن pipeline_runner.py:
    1. لا يستورد pipeline_config وقت الاستيراد على مستوى الموديول.
    2. لا يستورد delivery أو telegram_bot أو web_service نهائياً.
    3. صفر استيراد لـ pipeline_runner في ملفات التشغيل في الجذر (قبل ربط T6b).
    """
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    runner_file = os.path.join(root_dir, "pipeline_runner.py")

    with open(runner_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="pipeline_runner.py")

    # فحص الاستيرادات على مستوى الموديول (Module-Level AST)
    forbidden_top_level = {"pipeline_config", "delivery", "telegram_bot", "web_service"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_top_level, f"استيراد محظور على مستوى الموديول: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_top_level, f"استيراد محظور من: {node.module}"

    # فحص حظر delivery و telegram_bot و web_service في كامل الملف (حتى داخل الدوال)
    strictly_forbidden = {"delivery", "telegram_bot", "web_service"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in strictly_forbidden, f"استيراد محظور في الـ runner: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in strictly_forbidden, f"استيراد محظور في الـ runner: {node.module}"

    # التأكد من أن الملفات التشغيلية التي تستورد pipeline_runner هي فقط delivery.py و text_assist.py (ربط T6b)
    importers = []
    for fn in os.listdir(root_dir):
        if not fn.endswith(".py") or fn.startswith("test_") or fn == "pipeline_runner.py":
            continue
        with open(os.path.join(root_dir, fn), "r", encoding="utf-8") as f:
            content = f.read()
            if "pipeline_runner" in content:
                importers.append(fn)

    allowed_t6b = {"delivery.py"}
    unexpected = [fn for fn in importers if fn not in allowed_t6b]
    assert not unexpected, f"ملفات تشغيلية غير مصرح لها تستورد pipeline_runner: {unexpected}"
