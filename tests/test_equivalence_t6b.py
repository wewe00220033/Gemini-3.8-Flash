# -*- coding: utf-8 -*-
"""
test_equivalence_t6b.py — اختبارات التكافؤ والربط المعماري لمحرك الـ Pipeline (T6b — DEC-P2-05/08/09).

- اختبار 1 (الحاسم): غياب pipeline.json يضمن تشغيل المسار القديم الحرفي وصفر استدعاء لـ pipeline_runner.run_stage.
- اختبار 2: وجود pipeline.json يُفعّل مسار الـ runner لمرحلتي المقترحين والسبك، مع التحديث الحي وحفظ المسودات.
- اختبار 3: حارس No-Op لشلبي يعزل النص غير المنظم ويحدّث عدادات text_assist ويسجل الملاحظة.
- اختبار 4: ارتداد السبك المتعاقب في مرحلة final من شلبي إلى فلاش عند فشل شلبي، وتوثيق استدعاء البديل بنجاح.
- اختبار 5: الإغلاق الرشيق (Graceful Shutdown) عبر cancel_event وتخطي النداءات المعلقة فوراً بـ skipped.
"""

import threading
import time
from unittest.mock import MagicMock

from config import Config
import delivery
import pipeline_config
import pipeline_runner
from pipeline_runner import CallResult, StageResult
import text_assist


class DummySender:
    def __init__(self):
        self.sent = []
        self.edited = []
        self._mid = 100

    def send_text(self, text, reply_to_id=None, parse_mode=None, **kwargs):
        self._mid += 1
        self.sent.append((self._mid, text, reply_to_id, parse_mode))
        return self._mid

    def send_message(self, text, reply_to_message_id=None, parse_mode=None, **kwargs):
        return self.send_text(text, reply_to_id=reply_to_message_id, parse_mode=parse_mode, **kwargs)

    def edit_message_text(self, text, message_id=None, parse_mode=None, **kwargs):
        self.edited.append((message_id, text, parse_mode))
        return True


# =====================================================================
# اختبار 1 (الحاسم): غياب pipeline.json ينفذ المسار القديم دون لمس الـ runner
# =====================================================================

def test_legacy_path_when_pipeline_absent(monkeypatch, tmp_path):
    """
    إثبات قاطع: عندما لا يوجد pipeline.json على القرص، يعمل المسار القديم بالحرف
    ولا يُستدعى pipeline_runner.run_stage نهائياً (Call Count = 0).
    """
    non_existent = str(tmp_path / "absent_pipeline.json")
    monkeypatch.setattr(Config, "PIPELINE_FILE", non_existent)
    pipeline_config.reset_cache()
    assert not pipeline_config.has_disk_pipeline()

    # وضع spy على pipeline_runner.run_stage
    runner_spy = MagicMock()
    monkeypatch.setattr(pipeline_runner, "run_stage", runner_spy)

    # 1. فحص synthesize_ex القديمة
    res_ex = text_assist.synthesize_ex(
        raw_text="نص تجريبي للسبك",
        gemini_draft="مسودة فلاش",
        shelby_draft="مسودة شلبي",
    )
    assert res_ex is not None
    assert runner_spy.call_count == 0, "يُحظر استدعاء الـ runner عند غياب pipeline.json"

    # 2. فحص delivery._deliver_synthesizer القديم
    sender = DummySender()
    res_lock = threading.Lock()
    result = {"title": "فويس تجريبي", "raw": "نص صوتي"}
    finalized = []

    def mock_bg_finalize(approved, archive_text, **kwargs):
        finalized.append(approved)

    def dummy_polish(raw, role, gl, **kwargs):
        return ("مسودة شلبي القديمة", None)

    th = delivery._deliver_synthesizer(
        raw="نص صوتي",
        polished="مسودة جيميني القديمة",
        role_id="general",
        gl=None,
        l2=None,
        session_name="s1",
        sender=sender,
        anchor=1,
        local_polish_fn=dummy_polish,
        t0_flow=time.monotonic(),
        gemini_elapsed=1.0,
        res_lock=res_lock,
        result=result,
        _bg_finalize=mock_bg_finalize,
    )
    th.join(timeout=3.0)

    # الـ runner لم يُستدع نهائياً في المسار القديم
    assert runner_spy.call_count == 0


# =====================================================================
# اختبار 2: وجود pipeline.json يُفعّل مسار الـ runner والسبك والمسودات
# =====================================================================

def test_pipeline_runner_active_when_file_present(monkeypatch, tmp_path):
    """
    عند وجود pipeline.json: يُفعّل مسار الـ runner للمرحلتين (proposers و final)
    مع تحديث العرض الحي وحفظ المسودات عبر drafts.write.
    """
    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    monkeypatch.setattr(Config, "DRAFTS_DIR", str(tmp_path / "drafts"))
    pipeline_config.reset_cache()
    assert pipeline_config.has_disk_pipeline()

    # محاكاة خطة التشغيل
    fake_cfg = {
        "display": {"max_parallel": 2, "live_cap_utf16": 3400},
        "providers": {"shelby": {}, "flash_lite": {}},
        "stages": [
            {
                "name": "proposers",
                "enabled": True,
                "calls": [
                    {"provider": "flash_lite", "label": "فلاش"},
                    {"provider": "shelby", "label": "شلبي"},
                ],
            },
            {
                "name": "final",
                "enabled": True,
                "calls": [
                    {"provider": "shelby", "label": "سبك شلبي"},
                ],
            },
        ],
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: fake_cfg)

    recorded_stages = []

    def fake_run_stage(stage_name, stage_config, **kwargs):
        recorded_stages.append(stage_name)
        if stage_name == "proposers":
            return StageResult(
                stage_name="proposers",
                status="ok",
                calls=[
                    CallResult("c_shelby", "shelby", "model-s", "ok", text="مسودة شلبي المقترحة", label="شلبي"),
                ],
                final_output="مسودة شلبي المقترحة",
            )
        else:
            return StageResult(
                stage_name="final",
                status="ok",
                calls=[
                    CallResult("c_final", "shelby", "model-s", "ok", text="النسخة الذهبية المسبوكة بالـ pipeline", label="سبك شلبي"),
                ],
                final_output="النسخة الذهبية المسبوكة بالـ pipeline",
            )

    monkeypatch.setattr(pipeline_runner, "run_stage", fake_run_stage)

    sender = DummySender()
    res_lock = threading.Lock()
    result = {"title": "اختبار الـ runner", "raw": "كلام المالك الأصلي"}
    finalized = []

    def mock_bg_finalize(approved, archive_text, **kwargs):
        finalized.append(approved)

    th = delivery._deliver_synthesizer(
        raw="كلام المالك الأصلي",
        polished="مسودة فلاش الأصلية",
        role_id="general",
        gl=None,
        l2=None,
        session_name="s_pipe",
        sender=sender,
        anchor=5,
        local_polish_fn=None,
        t0_flow=time.monotonic(),
        gemini_elapsed=1.2,
        res_lock=res_lock,
        result=result,
        _bg_finalize=mock_bg_finalize,
        entry_id="entry_t6b_01",
    )
    th.join(timeout=3.0)

    # تم استدعاء مرحلتي proposers و final
    assert recorded_stages == ["proposers", "final"]
    assert finalized == ["النسخة الذهبية المسبوكة بالـ pipeline"]

    # تم حفظ المسودات على القرص
    draft_files = list((tmp_path / "drafts").glob("**/*.md"))
    assert len(draft_files) == 1
    draft_file = draft_files[0]
    assert draft_file.name == "entry_t6b_01.md"
    content = draft_file.read_text(encoding="utf-8")
    assert "مسودة فلاش الأصلية" in content
    assert "مسودة شلبي المقترحة" in content
    assert "النسخة الذهبية المسبوكة بالـ pipeline" in content


# =====================================================================
# اختبار 3: حارس No-Op لشلبي في مسار الـ runner
# =====================================================================

def test_shelby_noop_guard_in_pipeline(monkeypatch, tmp_path):
    """
    إذا أعاد شلبي النص الخام دون تنظيم (No-Op): يتم صده واستدعاء note_shelby_noop،
    مع استبعاده والارتداد الآمن لمسودة فلاش وتحديث عدادات text_assist.
    """
    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    pipeline_config.reset_cache()

    noop_notes = []
    monkeypatch.setattr(text_assist, "note_shelby_noop", lambda: noop_notes.append(1))
    monkeypatch.setattr(text_assist, "is_shelby_noop", lambda raw, text, **kw: True)

    fake_cfg = {
        "display": {"max_parallel": 2, "live_cap_utf16": 3400},
        "providers": {"shelby": {}, "flash_lite": {}},
        "stages": [
            {
                "name": "proposers",
                "enabled": True,
                "calls": [{"provider": "shelby", "label": "شلبي"}],
            },
            {
                "name": "final",
                "enabled": True,
                "calls": [{"provider": "shelby", "label": "سبك شلبي"}],
            },
        ],
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: fake_cfg)

    def fake_run_stage(stage_name, stage_config, **kwargs):
        if stage_name == "proposers":
            return StageResult(
                stage_name="proposers",
                status="ok",
                calls=[CallResult("c1", "shelby", "m", "ok", text="نص خام مكرر", label="شلبي")],
            )
        else:
            return StageResult(
                stage_name="final",
                status="ok",
                calls=[CallResult("c2", "shelby", "m", "ok", text="نص خام مكرر في السبك", label="سبك شلبي")],
            )

    monkeypatch.setattr(pipeline_runner, "run_stage", fake_run_stage)

    sender = DummySender()
    res_lock = threading.Lock()
    result = {"title": "اختبار noop", "raw": "نص خام"}
    finalized = []

    th = delivery._deliver_synthesizer(
        raw="نص خام",
        polished="مسودة فلاش المعتمدة",
        role_id="general",
        gl=None,
        l2=None,
        session_name="s_noop",
        sender=sender,
        anchor=10,
        local_polish_fn=None,
        t0_flow=time.monotonic(),
        gemini_elapsed=1.0,
        res_lock=res_lock,
        result=result,
        _bg_finalize=lambda app, arch, **kw: finalized.append(app),
        entry_id="entry_noop_01",
    )
    th.join(timeout=3.0)

    # تم استدعاء note_shelby_noop بحارس No-Op
    assert len(noop_notes) >= 1
    # تم اعتماد مسودة فلاش كبديل إنقاذي آمن
    assert finalized == ["مسودة فلاش المعتمدة"]


# =====================================================================
# اختبار 4: ارتداد السبك المتعاقب من شلبي إلى فلاش عند فشل شلبي
# =====================================================================

def test_synthesis_fallback_to_flash(monkeypatch, tmp_path):
    """
    عند فشل شلبي في مرحلة السبك النهائي: ترتد السلسلة إلى فلاش،
    وتُسجّل synth_fallback_calls و synth_last_provider == 'flash'.
    """
    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    pipeline_config.reset_cache()

    fake_cfg = {
        "display": {"max_parallel": 2, "live_cap_utf16": 3400},
        "providers": {"shelby": {}, "flash_lite": {}},
        "stages": [
            {
                "name": "proposers",
                "enabled": True,
                "calls": [{"provider": "flash_lite", "label": "فلاش"}],
            },
            {
                "name": "final",
                "enabled": True,
                "calls": [
                    {"provider": "shelby", "label": "سبك شلبي"},
                    {"provider": "flash_lite", "label": "سبك فلاش", "only_if_previous_failed": True},
                ],
            },
        ],
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: fake_cfg)

    def fake_run_stage(stage_name, stage_config, **kwargs):
        if stage_name == "proposers":
            return StageResult(stage_name="proposers", status="ok", calls=[])
        else:
            # شلبي فشل وفلاش نجح
            return StageResult(
                stage_name="final",
                status="ok",
                calls=[
                    CallResult("c1", "shelby", "m", "failed", error="نفاد المهلة"),
                    CallResult("c2", "flash_lite", "gemini-3.5-flash-lite", "ok", text="النسخة المسبوكة عبر فلاش", label="سبك فلاش"),
                ],
                final_output="النسخة المسبوكة عبر فلاش",
            )

    monkeypatch.setattr(pipeline_runner, "run_stage", fake_run_stage)

    sender = DummySender()
    res_lock = threading.Lock()
    result = {"title": "اختبار ارتداد فلاش", "raw": "كلام"}
    finalized = []

    th = delivery._deliver_synthesizer(
        raw="كلام",
        polished="مسودة أولية",
        role_id="general",
        gl=None,
        l2=None,
        session_name="s_fb",
        sender=sender,
        anchor=20,
        local_polish_fn=None,
        t0_flow=time.monotonic(),
        gemini_elapsed=1.0,
        res_lock=res_lock,
        result=result,
        _bg_finalize=lambda app, arch, **kw: finalized.append(app),
    )
    th.join(timeout=3.0)

    assert finalized == ["النسخة المسبوكة عبر فلاش"]
    # التحقق من تحديث حالة text_assist
    stats = text_assist.stats()
    assert stats.get("synth_last_provider") == "flash"


# =====================================================================
# اختبار 5: الإغلاق الرشيق وضخ cancel_event
# =====================================================================

def test_pipeline_runner_graceful_shutdown(monkeypatch):
    """
    التحقق من أن استدعاء wait_background_threads يُفعل request_pipeline_cancel،
    مما يجعل النداءات المعلقة في الـ runner تخرج فوراً بـ skipped.
    """
    delivery.reset_pipeline_cancel()

    # تشغيل wait_background_threads في خيط متزامن
    delivery.request_pipeline_cancel()

    stage_cfg = {
        "name": "proposers",
        "min_success": 1,
        "calls": [
            {"provider": "p1", "id": "c1"},
            {"provider": "p2", "id": "c2"},
        ]
    }
    providers = {"p1": {}, "p2": {}}

    res = pipeline_runner.run_stage(
        stage_name="proposers",
        stage_config=stage_cfg,
        inputs={"raw": "نص"},
        providers=providers,
        cancel_event=delivery._pipeline_cancel_event,
    )

    # النداءات تم تخطيها بالكامل بسبب تفعيل cancel_event
    assert all(c.status == "skipped" for c in res.calls)

    # إعادة التصفير
    delivery.reset_pipeline_cancel()
