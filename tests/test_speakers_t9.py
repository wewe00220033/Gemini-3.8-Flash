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
    """التحقق من مشروطية القاعدة 8 في الدستور التحريري الموحد."""
    # عند التعطيل: القاعدة القديمة بالحرف
    common_disabled = prompts.get_polish_common(speakers_enabled=False)
    assert common_disabled == prompts._POLISH_COMMON
    assert "إذا كان متكلماً واحداً لا تضف أي تسمية" in common_disabled

    # عند التفعيل: القاعدة الجديدة الحاكمة لحظر الاستحداث
    common_enabled = prompts.get_polish_common(speakers_enabled=True)
    assert common_enabled != prompts._POLISH_COMMON
    assert "انسخ وسوم المتكلمين الواردة في النص الخام كما هي بالضبط" in common_enabled
    assert "ممنوع منعاً باتاً اختلاق أو إنشاء وسم متكلم جديد" in common_enabled


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

