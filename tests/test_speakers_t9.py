# -*- coding: utf-8 -*-
"""
test_speakers_t9.py — اختبارات فصل المتكلمين وحوكمة وسوم الصوت (T9 — DEC-P2-01).
- التحقق الصارم من التكافؤ التام 100% لطبقة A عند تعطيل المتكلمين.
- التحقق من حقن كتلة المتكلمين وأمثلة Few-Shot والفاصل الحتمي عند التفعيل.
- التحقق من مشروطية القاعدة 8 في الدستور التحريري.
- التحقق من الحراس الحتمية: strip_single_speaker و forbid_new_speaker_tags.
- التحقق من تكامل transcriber.py و delivery.py.
"""

import json
import threading
import time

from config import Config
import prompts
import guards
import transcriber
import delivery
import pipeline_config


def test_equivalence_transcribe_system_prompt_disabled():
    """التحقق الحتمي: عند غياب أو تعطيل speakers، البرومبت يعيد Config.SYSTEM_PROMPT بالحرف 100%."""
    assert prompts.transcribe_system_prompt(None) == Config.SYSTEM_PROMPT
    assert prompts.transcribe_system_prompt({}) == Config.SYSTEM_PROMPT
    assert prompts.transcribe_system_prompt({"enabled": False}) == Config.SYSTEM_PROMPT
    assert prompts.compose_prompt("transcribe", speakers_cfg={"enabled": False}) == Config.SYSTEM_PROMPT


def test_transcribe_system_prompt_enabled_structure():
    """التحقق من بنية كتلة المتكلمين: القواعد + 3 أمثلة Few-Shot + الفاصل الحتمي."""
    sys_p = prompts.transcribe_system_prompt({"enabled": True})
    assert sys_p.startswith(Config.SYSTEM_PROMPT)
    assert "قواعد تمييز المتكلمين في التفريغ:" in sys_p
    assert "🗣️1:" in sys_p
    assert "🗣️2:" in sys_p
    # تحقق من الأمثلة الثلاثة:
    assert "مثال (حوار ثنائي):" in sys_p
    assert "مثال (حوار ثلاثي):" in sys_p
    assert "مثال (متحدث واحد — مونولوج):" in sys_p
    # تحقق من الفاصل الحتمي في نهاية الكتلة (تصحيح 3):
    assert sys_p.strip().endswith("انتهت قواعد المتكلمين.")


def test_layer_a_extra_len_not_ballooning():
    """التحقق من أن كتلة المتكلمين لا تستهلك سقف TRANSCRIBE_LAYER_A_MAX_CHARS المخصص للدور والقاموس."""
    extra_len_disabled = prompts.layer_a_extra_len(speakers_cfg={"enabled": False})
    extra_len_enabled = prompts.layer_a_extra_len(speakers_cfg={"enabled": True})
    assert extra_len_disabled == 0
    assert extra_len_enabled == 0


def test_get_polish_common_conditional_rule_8():
    """التحقق من مشروطية القاعدة 8 في الدستور التحريري الموحد وصمامات T9-fix."""
    # عند التعطيل: القاعدة القديمة بالحرف
    common_disabled = prompts.get_polish_common(speakers_enabled=False)
    assert common_disabled == prompts._POLISH_COMMON
    assert "إذا كان متكلماً واحداً لا تضف أي تسمية" in common_disabled

    # عند التفعيل: القاعدة الجديدة الحاكمة لحظر الاستحداث
    common_enabled = prompts.get_polish_common(speakers_enabled=True)
    assert common_enabled != prompts._POLISH_COMMON
    assert prompts.get_polish_common(True) != prompts.get_polish_common(False)
    assert "انسخ وسوم المتكلمين الواردة في النص الخام كما هي بالضبط" in common_enabled
    assert "ممنوع منعاً باتاً اختلاق أو إنشاء وسم متكلم جديد" in common_enabled
    # صمام T9-fix: القاعدة الجديدة لا تذكر رمز وسم محدد بل توجّه بالنسخ العام من الخام
    assert "🗣️" not in common_enabled


def test_rule_8_old_in_polish_common_contract():
    """التحقق من عقد صمام أمان T9-fix: وجود نص _RULE_8_OLD في _POLISH_COMMON وصحة الاستبدال."""
    assert prompts._RULE_8_OLD in prompts._POLISH_COMMON
    assert prompts._RULE_8_SPEAKERS not in prompts._POLISH_COMMON


def test_strip_single_speaker_monologue_and_dialogue():
    """التحقق الحتمي من دالة strip_single_speaker لتجريد المونولوج والحفاظ على الحوارات."""
    # 1. مونولوج مكرر بنفس الوسم -> تجريد تام
    text_mono = "🗣️1: السلام عليكم ورحمة الله.\n🗣️1: بنتكلم النهارده عن تقفيل الثغرات."
    stripped = guards.strip_single_speaker(text_mono)
    assert "🗣️1:" not in stripped
    assert "السلام عليكم ورحمة الله." in stripped
    assert "بنتكلم النهارده عن تقفيل الثغرات." in stripped

    # 2. حوار ثنائي -> بقاء الوسوم كما هي دون أي مساس
    text_dial = "🗣️1: جاهز للاجتماع؟\n🗣️2: أيوه جاهز وكل التقارير تمام."
    kept_dial = guards.strip_single_speaker(text_dial)
    assert kept_dial == text_dial

    # 3. حوار ثلاثي -> بقاء الوسوم كما هي
    text_tri = "🗣️1: أولاً.\n🗣️2: ثانياً.\n🗣️3: ثالثاً."
    assert guards.strip_single_speaker(text_tri) == text_tri

    # 4. نص بدون أي وسوم -> بقاء النص كما هو
    text_clean = "نص عادي بدون أي متكلمين."
    assert guards.strip_single_speaker(text_clean) == text_clean

    # 5. نصوص فارغة
    assert guards.strip_single_speaker("") == ""
    assert guards.strip_single_speaker(None) == ""


def test_forbid_new_speaker_tags():
    """التحقق من حارس forbid_new_speaker_tags ورصد الوسوم المخترعة في الذهبية."""
    raw = "🗣️1: مرحباً يا علي.\n🗣️2: أهلاً يا سليم."

    # حالة ذهبية مطابقة أو بدون وسوم -> لا تحذيرات
    golden_ok = "🗣️1: مرحباً يا علي.\n🗣️2: أهلاً يا سليم، كل شيء جاهز."
    assert guards.forbid_new_speaker_tags(golden_ok, raw) == []

    # حالة ذهبية أضافت متحدثاً لم يكن في الخام (🗣️3:) -> اكتشاف الوسم الجديد
    golden_fabricated = "🗣️1: مرحباً يا علي.\n🗣️2: أهلاً يا سليم.\n🗣️3: وأنا معكما."
    warns = guards.forbid_new_speaker_tags(golden_fabricated, raw)
    assert len(warns) == 1
    assert "🗣️3:" in warns[0]


def test_transcription_prompt_reads_pipeline_config(tmp_path, monkeypatch):
    """التحقق من قراءة transcriber.transcription_prompt لإعدادات speakers من pipeline_config.get()."""
    p_file = tmp_path / "custom_pipeline.json"
    doc = {
        "version": 1,
        "speakers": {"enabled": True, "tag_regex": "^🗣️(\\d+):", "strip_if_single": True}
    }
    p_file.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(pipeline_config.Config, "PIPELINE_FILE", str(p_file))
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(p_file))
    pipeline_config.reset_cache()

    prompt = transcriber.transcription_prompt()
    assert "قواعد تمييز المتكلمين في التفريغ:" in prompt
    assert "انتهت قواعد المتكلمين." in prompt


def test_prepare_texts_strips_single_speaker_before_glossary_and_archive(tmp_path, monkeypatch):
    """التحقق من أن prepare_texts تجرد وسوم المتحدث الواحد قبل القاموس وقبل الأرشفة (تصحيح 4)."""
    p_file = tmp_path / "custom_pipeline.json"
    doc = {
        "version": 1,
        "speakers": {"enabled": True, "tag_regex": "^🗣️(\\d+):", "strip_if_single": True}
    }
    p_file.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(pipeline_config.Config, "PIPELINE_FILE", str(p_file))
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(p_file))
    pipeline_config.reset_cache()

    raw_input = "🗣️1: هذا تسجيل مونولوج لشخص واحد فقط."
    raw_out, gl = delivery.prepare_texts(raw_input)
    # تجريد تام للوسم من الخام
    assert "🗣️1:" not in raw_out
    assert "هذا تسجيل مونولوج لشخص واحد فقط." in raw_out


def test_gemini_read_timeout_base_env():
    """التحقق من أن مهلة GEMINI_READ_TIMEOUT_BASE تساوي 30 افتراضياً وتقبل الضبط من البيئة (T9-fix2)."""
    import subprocess
    import sys
    assert Config.GEMINI_READ_TIMEOUT_BASE == 30

    cmd = (
        "import os; os.environ['GEMINI_READ_TIMEOUT_BASE'] = '45'; "
        "from config import Config; "
        "assert Config.GEMINI_READ_TIMEOUT_BASE == 45"
    )
    res = subprocess.run([sys.executable, "-c", cmd], capture_output=True, text=True)
    assert res.returncode == 0, f"Subprocess failed: {res.stderr}"


def test_forbid_new_speaker_tags_catches_legacy_tags():
    """التحقق من أن forbid_new_speaker_tags يرصد الوسوم التقليدية المستحدثة مثل المتكلم 1: (T9-fix2)."""
    raw = "مرحباً يا فندم، التسجيل الصوتي بدون أي وسوم."
    # حالة إضافة المتكلم 1: في الذهبية دون وجوده في الخام
    golden_with_legacy = "المتكلم 1: مرحباً يا فندم، التسجيل الصوتي بدون أي وسوم."
    warns = guards.forbid_new_speaker_tags(golden_with_legacy, raw)
    assert len(warns) == 1
    assert "المتكلم 1:" in warns[0]

    # حالة إضافة Speaker 2:
    golden_speaker = "Speaker 2: مرحبا."
    warns_spk = guards.forbid_new_speaker_tags(golden_speaker, raw)
    assert any("Speaker 2:" in w for w in warns_spk)

    # حالة وجود الوسم أصلاً في الخام -> لا تحذير
    raw_with_legacy = "المتكلم 1: مرحباً يا فندم."
    assert guards.forbid_new_speaker_tags(golden_with_legacy, raw_with_legacy) == []


def test_strip_single_speaker_legacy_tags():
    """التحقق من تجريد الوسوم التقليدية إذا كانت لمتحدث واحد فقط (T9-fix2)."""
    mono_legacy = "المتكلم 1: السلام عليكم.\nالمتكلم 1: هذا نص تجريبي."
    stripped = guards.strip_single_speaker(mono_legacy)
    assert "المتكلم 1:" not in stripped
    assert "السلام عليكم." in stripped

    # حوار بمتحدثين تقليديين -> لا تجريد
    dial_legacy = "المتكلم 1: أهلاً.\nالمتكلم 2: مرحباً بك."
    assert guards.strip_single_speaker(dial_legacy) == dial_legacy


def test_text_similarity_and_is_duplicate_draft():
    """التحقق من صمام منع تكرار المسودات text_similarity_ratio و is_duplicate_draft (T9-fix2)."""
    t1 = "هذا نص تجريبي لتنظيم الصوت بدقة عالية."
    t2 = "هذا نص تجريبي لتنظيم الصوت بدقة عالية."
    t3 = "هذا نص تجريبي لتنظيم الصوت بدقة فائقة جداً ومختلف تماماً."

    assert guards.text_similarity_ratio(t1, t2) == 1.0
    assert guards.text_similarity_ratio("", "") == 0.0
    assert guards.is_duplicate_draft(t1, [t2], threshold=0.98) is True
    assert guards.is_duplicate_draft(t3, [t1], threshold=0.98) is False
    assert guards.is_duplicate_draft(t1, [], threshold=0.98) is False


def test_pipeline_example_json_proposers_explicit_calls():
    """التحقق من أن قالب pipeline.example.json يحتوي على نداءين بحرارتين مختلفتين بدل repeat: 2 (T9-fix2)."""
    import os
    ex_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pipeline.example.json")
    with open(ex_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    proposers = next(s for s in data["stages"] if s["name"] == "proposers")
    calls = proposers["calls"]
    assert len(calls) == 3
    assert calls[0]["provider"] == "flash_lite"
    assert calls[0]["temperature"] == 0.2
    assert calls[0]["label"] == "فلاش دقيق"
    assert calls[1]["provider"] == "flash_lite"
    assert calls[1]["temperature"] == 0.7
    assert calls[1]["label"] == "فلاش موسّع"
    assert calls[2]["provider"] == "shelby"
    assert "repeat" not in calls[0]


def test_text_assist_polish_respects_speakers_config(monkeypatch):
    """التحقق من أن text_assist.polish يستقبل speakers_enabled ويمرره لبرومبت التنظيم (T9-fix2)."""
    import importlib
    import text_assist
    real_polish = importlib.reload(text_assist).polish
    monkeypatch.setattr(text_assist, "polish", real_polish)

    captured_args = {}
    orig_compose = prompts.compose_prompt

    def mock_compose(*args, **kwargs):
        captured_args["speakers_enabled"] = kwargs.get("speakers_enabled")
        return orig_compose(*args, **kwargs)

    monkeypatch.setattr(prompts, "compose_prompt", mock_compose)
    monkeypatch.setattr(text_assist.prompts, "compose_prompt", mock_compose)

    # محاكاة رد سريع من المزود المحلي
    monkeypatch.setattr(text_assist, "_call_local_provider", lambda s, u: ("نص منظم ممتاز جداً", None))
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_command")
    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_CMD", "dummy_command")

    # تمرير صريح بـ True
    text_assist.polish("نص خام طويل للتجربة والتأكد من نجاح الاستدعاء.", speakers_enabled=True)
    assert captured_args.get("speakers_enabled") is True

    # تمرير صريح بـ False
    text_assist.polish("نص خام آخر للتجربة والتأكد من نجاح الاستدعاء.", speakers_enabled=False)
    assert captured_args.get("speakers_enabled") is False


def test_deliver_passes_speakers_enabled_to_polish_fn(tmp_path, monkeypatch):
    """التحقق من أن delivery.deliver يقرأ speakers_enabled من pipeline_config ويمرره لدالة التنظيم (T9-fix2)."""
    p_file = tmp_path / "custom_pipeline.json"
    doc = {
        "version": 1,
        "speakers": {"enabled": True, "tag_regex": "^🗣️(\\d+):", "strip_if_single": True}
    }
    p_file.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(pipeline_config.Config, "PIPELINE_FILE", str(p_file))
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(p_file))
    pipeline_config.reset_cache()

    captured = {}

    def mock_polish(raw, role_id=None, glossary=None, **kwargs):
        captured["speakers_enabled"] = kwargs.get("speakers_enabled")
        return "نص منظم تجريبي", None

    class DummySender:
        def __init__(self):
            self._mid = 1
        def send_text(self, text, *a, **k):
            self._mid += 1
            return {"message_id": self._mid}
        def send_action(self, *a, **k):
            pass

    sender = DummySender()
    delivery.deliver(
        raw_text="نص خام للتجربة",
        model="gemini-3.6-flash",
        key_name="test_key",
        info={"elapsed": 1.0},
        sender=sender,
        polish_fn=mock_polish,
        archive_entry=False,
    )
    assert captured.get("speakers_enabled") is True


def test_delivery_dedupe_guard_filters_duplicate_draft(monkeypatch, tmp_path):
    """التحقق من أن delivery._bg_run_pipeline يستبعد المسودات المتطابقة عبر dedupe_ratio (T9-fix2)."""
    import pipeline_runner
    from pipeline_runner import CallResult, StageResult

    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    monkeypatch.setattr(pipeline_config.Config, "PIPELINE_FILE", str(pipe_file))
    pipeline_config.reset_cache()

    custom_cfg = {
        "display": {"max_parallel": 2, "live_cap_utf16": 3400, "style": "single_live"},
        "providers": {"prov_a": {}, "prov_b": {}},
        "stages": [
            {
                "name": "proposers",
                "enabled": True,
                "calls": [
                    {"provider": "prov_a", "label": "فلاش 1"},
                    {"provider": "prov_a", "label": "فلاش 2"},
                ],
            },
            {
                "name": "final",
                "enabled": True,
                "prompt": "synthesis",
                "inputs": ["raw", "proposers"],
                "calls": [{"provider": "prov_b", "label": "شلبي"}],
            },
        ],
        "guards": {"dedupe_ratio": 0.98, "forbid_new_speaker_tags": True},
        "speakers": {"enabled": False},
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: custom_cfg)

    inputs_seen_in_final = []

    def mock_run_stage(stage_name, stage_config, inputs, **kwargs):
        if stage_name == "proposers":
            # إرجاع مسودتين متطابقتين بالحرف
            return StageResult(
                stage_name=stage_name,
                status="ok",
                calls=[
                    CallResult(call_id="c1", provider="prov_a", model="m", status="ok", text="نص منظم مطابق تماماً", label="فلاش 1", elapsed_s=1.0),
                    CallResult(call_id="c2", provider="prov_a", model="m", status="ok", text="نص منظم مطابق تماماً", label="فلاش 2", elapsed_s=1.0),
                ],
                final_output="نص منظم مطابق تماماً",
            )
        elif stage_name == "final":
            inputs_seen_in_final.extend(inputs.get("drafts") or [])
            return StageResult(
                stage_name=stage_name,
                status="ok",
                calls=[
                    CallResult(call_id="c3", provider="prov_b", model="m", status="ok", text="النسخة النهائية المسبوكة", label="شلبي", elapsed_s=1.5),
                ],
                final_output="النسخة النهائية المسبوكة",
            )

    monkeypatch.setattr(pipeline_runner, "run_stage", mock_run_stage)

    class DummySender:
        def __init__(self):
            self.sent = []
            self.edited = []
            self.sent_messages = []
            self._mid = 10
        def send_text(self, text, reply_to_id=None, parse_mode=None, **kw):
            self._mid += 1
            return {"message_id": self._mid}
        def edit_text(self, text, message_id, **kw):
            self.edited.append((message_id, text))
            return {"message_id": message_id}

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

    # التحقق الحتمي: المسودة الثانية المكررة تم استبعادها، ووصلت مسودة واحدة فقط لمرحلة final
    labels_in_final = [lbl for lbl, txt in inputs_seen_in_final]
    assert "فلاش 1" in labels_in_final
    assert "فلاش 2" not in labels_in_final



