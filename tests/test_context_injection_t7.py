# -*- coding: utf-8 -*-
"""
اختبارات حقن السياق الموحد وحارس الصدى وحماية سياق المالك (T7 — DEC-P2-03/08).
"""
import pytest
import guards
import sessions
import text_assist
import prompts


def test_inject_system_context_polish_summary():
    """التحقق من حقن ملخص الجلسة في نمط التنظيم (polish)."""
    base_sys = "أنت منظم نصوص خبير."
    summary = "الجلسة تتحدث عن خوارزميات البحث السريع."
    res = text_assist.inject_system_context(
        base_sys,
        context_summary=summary,
        mode="polish",
    )
    assert base_sys in res
    assert summary in res
    assert "نظّم النص الجديد فقط" in res
    assert "<owner_context>" not in res


def test_inject_system_context_synthesis_summary():
    """التحقق من حقن ملخص الجلسة في نمط السبك (synthesis)."""
    base_sys = "أنت سبّاك نصوص عبقري."
    summary = "مناقشة إعدادات الخوادم والشبكات."
    res = text_assist.inject_system_context(
        base_sys,
        context_summary=summary,
        mode="synthesis",
    )
    assert base_sys in res
    assert summary in res
    assert "اسبك النص الجديد فقط" in res
    assert "<owner_context>" not in res


def test_inject_system_context_owner_context_isolation():
    """التحقق من عزل سياق المالك بوسوم واضحة وتنبيه حاكم."""
    base_sys = "أنت مساعد ذكي."
    owner_ctx = "الاسم: زيزو، المهنة: مهندس معماري سحابي، التركيز: الأداء والاستقرار."
    res = text_assist.inject_system_context(
        base_sys,
        owner_context=owner_ctx,
        mode="polish",
    )
    assert base_sys in res
    assert "<owner_context>" in res
    assert "</owner_context>" in res
    assert owner_ctx in res
    assert "تنبيه سياق المالك" in res
    assert "يُحظر تماماً استدعاء أو إضافة أو دمج أي أحداث أو معلومات منها" in res


def test_inject_system_context_both_contexts():
    """التحقق من حقن سياق الجلسة وسياق المالك معاً بتنسيق متسق."""
    base_sys = "برومبت أساسي"
    res = text_assist.inject_system_context(
        base_sys,
        context_summary="ملخص الجلسة السابق",
        owner_context="سياق المالك الحصري",
        mode="synthesis",
    )
    assert "ملخص الجلسة السابق" in res
    assert "<owner_context>\nسياق المالك الحصري\n</owner_context>" in res


def test_inject_system_context_max_owner_chars_truncation():
    """التحقق من قص سياق المالك عند تجاوز الحد المسموح max_owner_chars."""
    base_sys = "برومبت"
    long_owner = "أ" * 500
    res = text_assist.inject_system_context(
        base_sys,
        owner_context=long_owner,
        mode="polish",
        max_owner_chars=100,
    )
    assert "أ" * 100 in res
    assert "أ" * 101 not in res


def test_inject_system_context_invalid_mode_raises():
    """التحقق من رفض أي نمط غير معروف برمي ValueError."""
    with pytest.raises(ValueError, match="Unknown mode"):
        text_assist.inject_system_context("برومبت", mode="arbitrary_mode")


def test_synthesis_user_content_untouched():
    """التحقق من أن user_content في السبك يظل ناصعاً ومستقلاً تماماً عن حقن سياق النظام."""
    raw = "نص صوتي خام للتجربة"
    drafts_list = [("فلاش", "مسودة فلاش للتجربة"), ("شلبي", "مسودة شلبي للتجربة")]
    p_sys, p_usr = prompts.compose_synthesis_prompt(
        raw_text=raw,
        drafts=drafts_list,
        role_id="general",
    )
    injected_sys = text_assist.inject_system_context(
        p_sys,
        context_summary="ملخص سابق",
        owner_context="سياق خاص بالمالك",
        mode="synthesis",
    )
    assert "<owner_context>" in injected_sys
    # التحقق من أن user_content لم يتأثر نهائياً بحقن سياق النظام
    assert p_usr == prompts.compose_synthesis_prompt(raw_text=raw, drafts=drafts_list, role_id="general")[1]
    assert "<owner_context>" not in p_usr
    assert "ملخص سابق" not in p_usr
    assert raw in p_usr
    for lbl, txt in drafts_list:
        assert lbl in p_usr
        assert txt in p_usr


def test_text_assist_wrappers_accept_owner_context():
    """التحقق من توافق كافة دوال text_assist القديمة والجديدة مع معامل owner_context=None."""
    # فحص أن الدوال تقبل المعامل دون TypeError
    import inspect
    for fn in (text_assist.polish_gemini, text_assist.polish_local, text_assist.polish,
               text_assist.synthesize, text_assist.synthesize_ex):
        sig = inspect.signature(fn)
        assert "owner_context" in sig.parameters
        assert sig.parameters["owner_context"].default is None


def test_guards_echo_ratio_and_detection():
    """التحقق من حساب حارس الصدى في guards.py وتوافق sessions.is_context_echo معه."""
    context = "هذا سياق المالك المرجعي المليء بالتفاصيل الخاصة بالمشروع والتصميم المعماري المتين"
    # صدى كامل بنسبة 100%
    echo_text = "هذا سياق المالك المرجعي المليء بالتفاصيل الخاصة بالمشروع والتصميم المعماري المتين"
    assert guards.context_echo_ratio(echo_text, context) == 1.0
    assert guards.is_context_echo(echo_text, context, ratio=0.60) is True
    assert sessions.is_context_echo(echo_text, context) is True

    # كلام جديد غير مرتبط
    different_text = "اليوم ذهبنا إلى حديقة الحيوان ورأينا الفيل والزرافة والأسد يأكلون بشهية"
    assert guards.context_echo_ratio(different_text, context) < 0.20
    assert guards.is_context_echo(different_text, context, ratio=0.60) is False
    assert sessions.is_context_echo(different_text, context) is False

    # نص قصير جداً (< 6 كلمات) لا يُعتبر صدى لتفادي الإنذارات الكاذبة
    short_echo = "سياق المالك المرجعي"
    assert guards.is_context_echo(short_echo, context, ratio=0.60) is False

    # سياق فارغ
    assert guards.is_context_echo(echo_text, "", ratio=0.60) is False
