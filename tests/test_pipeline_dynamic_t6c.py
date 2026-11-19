# -*- coding: utf-8 -*-
"""
tests/test_pipeline_dynamic_t6c.py — اختبارات خط التنظيم الديناميكي والحارس الحتمي (T6c — DEC-P2-08/09).
تغطي:
1. حلقة الـ Pipeline العامة ثلاثية المراحل بأسماء ومحركات عشوائية دون أي تبعية لأسماء ثابتة.
2. برومبت polish_multi واستقباله للمسودات بنجاح.
3. الحارس الحتمي للأرقام والكلمات اللاتينية المستحدثة (Extrinsic Hallucination Guard).
4. نمط per_output وإرسال رسائل صامتة منفصلة لكل مرحلة مع رسالة ذهبية نهائية مسموعة.
5. تصفية show_stages للعرض الحي.
6. التوافق العكسي 100% لدالة compose_synthesis_prompt.
7. تفكيك repeat: 2 إلى مسميات #1 و #2.
8. تطبيق القاموس فورياً على كل مخرج مرحلي قبل العرض والمرحلة التالية.
"""

import threading
import time

from config import Config
import delivery
import guards
import pipeline_config
import pipeline_runner
from pipeline_runner import CallResult, StageResult
import prompts


class DummySender:
    def __init__(self):
        self.sent = []
        self.edited = []
        self.sent_messages = []
        self._mid = 100

    def send_text(self, text, reply_to_id=None, parse_mode=None, **kwargs):
        self._mid += 1
        self.sent.append((self._mid, text, reply_to_id, parse_mode))
        self.sent_messages.append({
            "mid": self._mid,
            "text": text,
            "reply_to": reply_to_id,
            "parse_mode": parse_mode,
            "disable_notification": kwargs.get("disable_notification", False),
        })
        return self._mid

    def send_message(self, chat_id=None, text=None, parse_mode=None, disable_notification=False, reply_to_message_id=None, **kwargs):
        self._mid += 1
        self.sent.append((self._mid, text, reply_to_message_id, parse_mode))
        self.sent_messages.append({
            "mid": self._mid,
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification,
            "reply_to": reply_to_message_id,
        })
        return type("SentMsg", (), {"message_id": self._mid})()

    def edit_message_text(self, text, chat_id=None, message_id=None, parse_mode=None, reply_markup=None, **kwargs):
        self.edited.append((message_id, text, parse_mode))
        return True


# 1. اختبار حلقة المراحل العامة بأسماء ومحركات عشوائية
def test_generic_3_stage_loop_arbitrary_names(monkeypatch, tmp_path):
    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    pipeline_config.reset_cache()

    custom_cfg = {
        "display": {"max_parallel": 2, "live_cap_utf16": 3400},
        "providers": {"eng_alpha": {}, "eng_beta": {}},
        "stages": [
            {
                "name": "stage_a",
                "enabled": True,
                "calls": [{"provider": "eng_alpha", "label": "ألفا"}],
            },
            {
                "name": "stage_b",
                "enabled": True,
                "prompt": "polish_multi",
                "inputs": ["raw", "stage_a"],
                "calls": [{"provider": "eng_beta", "label": "بيتا"}],
            },
            {
                "name": "stage_c",
                "enabled": True,
                "prompt": "synthesis",
                "inputs": ["raw", "stage_b"],
                "calls": [{"provider": "eng_alpha", "label": "ألفا_نهائي"}],
            },
        ],
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: custom_cfg)

    recorded_stages = []

    def fake_run_stage(stage_name, stage_config, inputs, **kwargs):
        recorded_stages.append((stage_name, inputs))
        return StageResult(
            stage_name=stage_name,
            status="ok",
            calls=[
                CallResult(
                    call_id=f"c_{stage_name}",
                    provider="eng_alpha",
                    model="custom_model",
                    status="ok",
                    text=f"output_{stage_name}",
                    label=f"lbl_{stage_name}",
                    elapsed_s=1.5,
                )
            ],
            final_output=f"output_{stage_name}",
        )

    monkeypatch.setattr(pipeline_runner, "run_stage", fake_run_stage)

    sender = DummySender()
    res_lock = threading.Lock()
    result = {"title": "اختبار عام", "raw": "نص أصلي"}
    finalized = []

    th = delivery._deliver_synthesizer(
        raw="نص أصلي",
        polished="مسودة مسبقة",
        role_id="general",
        gl=None,
        l2=None,
        session_name="sess",
        sender=sender,
        anchor=1,
        local_polish_fn=None,
        t0_flow=time.monotonic(),
        gemini_elapsed=0.5,
        res_lock=res_lock,
        result=result,
        _bg_finalize=lambda app, arch, **kw: finalized.append(app),
    )
    th.join(timeout=3.0)

    assert len(recorded_stages) == 3
    assert recorded_stages[0][0] == "stage_a"
    assert recorded_stages[1][0] == "stage_b"
    assert recorded_stages[2][0] == "stage_c"
    assert finalized == ["output_stage_c"]


# 2. اختبار برومبت مرحلة polish_multi واستقبال المسودات
def test_polish_multi_receives_drafts_in_prompt():
    raw = "السلام عليكم ورحمة الله"
    drafts_list = [("مسودة 1", "وعليكم السلام"), ("مسودة 2", "أهلاً وسهلاً")]
    sys_prompt, user_content = prompts.compose_polish_multi_prompt(
        raw_text=raw,
        drafts=drafts_list,
        role_id="general",
    )
    assert "أصل التفريغ الصوتي الخام" in user_content
    assert "مسودة مقترحة 1 (مسودة 1)" in user_content
    assert "وعليكم السلام" in user_content
    assert "أهلاً وسهلاً" in user_content
    assert "لديك 2 مسودات لنفس النص الخام" in user_content


# 3. اختبار الحارس الحتمي للأرقام والكلمات اللاتينية المستحدثة
def test_deterministic_numbers_latin_subset_guard():
    raw = "سعر القطعة ٢٥ دولار واسم النظام Linux"
    drafts = ["سعر القطعة 25 دولار مع نظام Linux"]
    golden_ok = "القطعة بسعر 25 دولار على نظام Linux"
    warns_ok = guards.numbers_latin_subset_check(golden_ok, raw, drafts)
    assert warns_ok == []

    # إدخال هلوسة رقمية وكلمة لاتينية جديدة لم ترد في الخام ولا المسودات
    golden_hallucinated = "القطعة بسعر 99 دولار على نظام Docker"
    warns_bad = guards.numbers_latin_subset_check(golden_hallucinated, raw, drafts)
    assert "99" in warns_bad
    assert "Docker" in warns_bad


# 4. اختبار نمط per_output وإرسال رسائل صامتة منفصلة
def test_per_output_sends_silent_messages_plus_audible_final(monkeypatch, tmp_path):
    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    pipeline_config.reset_cache()

    custom_cfg = {
        "display": {"mode": "per_output", "max_parallel": 1},
        "providers": {"p1": {}},
        "stages": [
            {
                "name": "s1",
                "enabled": True,
                "calls": [{"provider": "p1", "label": "مسودة 1"}],
            },
            {
                "name": "s2",
                "enabled": True,
                "calls": [{"provider": "p1", "label": "سبك"}],
            },
        ],
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: custom_cfg)

    def fake_run_stage(stage_name, stage_config, **kwargs):
        return StageResult(
            stage_name=stage_name,
            status="ok",
            calls=[
                CallResult(
                    call_id=f"c_{stage_name}",
                    provider="p1",
                    model="m1",
                    status="ok",
                    text=f"text_{stage_name}",
                    label=f"lbl_{stage_name}",
                    elapsed_s=1.0,
                )
            ],
            final_output=f"text_{stage_name}",
        )

    monkeypatch.setattr(pipeline_runner, "run_stage", fake_run_stage)

    sender = DummySender()
    res_lock = threading.Lock()
    result = {"title": "اختبار per_output", "raw": "خام"}
    finalized = []

    th = delivery._deliver_synthesizer(
        raw="خام",
        polished=None,
        role_id="general",
        gl=None,
        l2=None,
        session_name="sess",
        sender=sender,
        anchor=555,
        local_polish_fn=None,
        t0_flow=time.monotonic(),
        gemini_elapsed=0.5,
        res_lock=res_lock,
        result=result,
        _bg_finalize=lambda app, arch, **kw: finalized.append(app),
    )
    th.join(timeout=3.0)

    # يجب أن تُرسل رسائل صامتة (disable_notification=True)
    silent_msgs = [m for m in sender.sent_messages if m["disable_notification"] is True]
    assert len(silent_msgs) >= 2


# 5. اختبار تصفية show_stages للعرض الحي
def test_show_stages_filters_live_preview(monkeypatch, tmp_path):
    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    pipeline_config.reset_cache()

    custom_cfg = {
        "display": {"mode": "single_live", "show_stages": ["proposers", "final"]},
        "providers": {"p1": {}},
        "stages": [
            {
                "name": "proposers",
                "enabled": True,
                "calls": [{"provider": "p1", "label": "مقترح"}],
            },
            {
                "name": "polishers",
                "enabled": True,
                "calls": [{"provider": "p1", "label": "منقح"}],
            },
            {
                "name": "final",
                "enabled": True,
                "calls": [{"provider": "p1", "label": "نهائي"}],
            },
        ],
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: custom_cfg)

    updated_interims = []
    orig_update = delivery.LiveMessageController.update_interim

    def mock_update(self, text, force=False):
        updated_interims.append(text)
        return orig_update(self, text, force=force)

    monkeypatch.setattr(delivery.LiveMessageController, "update_interim", mock_update)

    def fake_run_stage(stage_name, stage_config, **kwargs):
        return StageResult(
            stage_name=stage_name,
            status="ok",
            calls=[
                CallResult(
                    call_id=f"c_{stage_name}",
                    provider="p1",
                    model="m1",
                    status="ok",
                    text=f"content_{stage_name}",
                    label=f"lbl_{stage_name}",
                    elapsed_s=1.0,
                )
            ],
            final_output=f"content_{stage_name}",
        )

    monkeypatch.setattr(pipeline_runner, "run_stage", fake_run_stage)

    sender = DummySender()
    res_lock = threading.Lock()
    result = {"title": "اختبار show_stages", "raw": "خام"}

    th = delivery._deliver_synthesizer(
        raw="خام",
        polished=None,
        role_id="general",
        gl=None,
        l2=None,
        session_name="sess",
        sender=sender,
        anchor=1,
        local_polish_fn=None,
        t0_flow=time.monotonic(),
        gemini_elapsed=0.5,
        res_lock=res_lock,
        result=result,
        _bg_finalize=lambda app, arch, **kw: None,
    )
    th.join(timeout=3.0)

    # العرض الحي يحتوي على proposers ولكن polishers تم تخطيه من show_stages
    assert any("content_proposers" in html for html in updated_interims)
    # لا توجد رسالة حية تحتوي على content_polishers وحدها في live_blocks
    assert not any("lbl_polishers" in html for html in updated_interims)


# 6. اختبار التوافق العكسي التام لدالة compose_synthesis_prompt
def test_backward_compatibility_compose_synthesis_prompt():
    raw = "نص التفريغ الأصلي"
    g_draft = "مسودة جيميني"
    s_draft = "مسودة شلبي"

    # المسار القديم
    sys_old, usr_old = prompts.compose_synthesis_prompt(
        raw_text=raw,
        gemini_draft=g_draft,
        shelby_draft=s_draft,
        role_id="general",
    )

    assert "أصل التفريغ الصوتي الخام" in usr_old
    assert "=== 💡 اقتراح مسودة 1 (فلاش) ===" in usr_old
    assert "=== 💡 اقتراح مسودة 2 (شلبي) ===" in usr_old
    assert "المطلوب: نظّم أصل التفريغ الصوتي الخام" in usr_old


# 7. اختبار تفكيك repeat: 2 إلى #1 و #2
def test_repeat_expansion_labels():
    doc = {
        "version": 1,
        "stages": [
            {
                "name": "proposers",
                "enabled": True,
                "calls": [
                    {"provider": "flash_lite", "label": "فلاش", "repeat": 2},
                ],
            }
        ],
    }
    parsed = pipeline_config._parse_pipeline_doc(doc)
    calls = parsed["stages"][0]["calls"]
    assert len(calls) == 2
    assert calls[0]["label"] == "فلاش #1"
    assert calls[1]["label"] == "فلاش #2"


# 8. تطبيق القاموس فورياً على كل مخرج مرحلي
def test_glossary_applied_immediately_to_each_stage(monkeypatch, tmp_path):
    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    pipeline_config.reset_cache()

    custom_cfg = {
        "display": {"mode": "single_live"},
        "providers": {"p1": {}},
        "stages": [
            {
                "name": "stage_1",
                "enabled": True,
                "calls": [{"provider": "p1", "label": "مرحلة 1"}],
            },
            {
                "name": "final",
                "enabled": True,
                "calls": [{"provider": "p1", "label": "نهائي"}],
            },
        ],
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: custom_cfg)

    class DummyGlossary:
        def apply(self, text, target="polished"):
            return text.replace("غلط", "صح"), 1

    gl = DummyGlossary()

    def fake_run_stage(stage_name, stage_config, **kwargs):
        return StageResult(
            stage_name=stage_name,
            status="ok",
            calls=[
                CallResult(
                    call_id=f"c_{stage_name}",
                    provider="p1",
                    model="m1",
                    status="ok",
                    text="كلمة غلط تماماً",
                    label=f"lbl_{stage_name}",
                    elapsed_s=1.0,
                )
            ],
            final_output="كلمة غلط تماماً",
        )

    monkeypatch.setattr(pipeline_runner, "run_stage", fake_run_stage)

    sender = DummySender()
    res_lock = threading.Lock()
    result = {"title": "اختبار القاموس", "raw": "خام"}
    finalized = []

    th = delivery._deliver_synthesizer(
        raw="خام",
        polished=None,
        role_id="general",
        gl=gl,
        l2=None,
        session_name="sess",
        sender=sender,
        anchor=1,
        local_polish_fn=None,
        t0_flow=time.monotonic(),
        gemini_elapsed=0.5,
        res_lock=res_lock,
        result=result,
        _bg_finalize=lambda app, arch, **kw: finalized.append(app),
    )
    th.join(timeout=3.0)

    # يجب أن يتم استبدال "غلط" بـ "صح" في المخرج النهائي المسلم
    assert finalized == ["كلمة صح تماماً"]
