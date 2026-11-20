# -*- coding: utf-8 -*-
"""
tests/test_p3_03_ki04_ki05.py — اختبارات حزمة P3-03 (KI-04 و KI-05).

- KI-04: تخصيص أيقونات المحركات عبر display.icons في pipeline_defaults و _format_icon_label و pipeline.example.json.
- KI-05: توحيد سقف الاقتطاع لسياق المالك في مصدره المعماري الصارم (memory.get_owner_context بالبايت)
         وتمرير max_owner_chars=None من delivery لمنع القص المزدوج أو إتلاف أحرف UTF-8.
"""

import json
import os

import delivery
import pipeline_defaults
import text_assist


def test_format_icon_label_custom_icons_ki_04():
    """KI-04 (P3-03): قراءة display.icons المخصصة أولاً قبل الأسماء الثابتة."""
    custom_icons = {"ollama_gpu": "🐋", "my_flash": "✨"}

    # 1) مزود مخصص بأيقونة محددة
    assert delivery._format_icon_label("ollama_gpu", "ollama_gpu", icons=custom_icons) == "🐋 ollama_gpu"
    assert delivery._format_icon_label("نموذج تجريبي", "my_flash", icons=custom_icons) == "✨ نموذج تجريبي"

    # 2) مزود جديد غير مدرج في icons يأخذ 🤖 افتراضياً
    assert delivery._format_icon_label("ollama_gpu", "ollama_gpu", icons={}) == "🤖 ollama_gpu"
    assert delivery._format_icon_label("unknown_prov", "unknown_prov") == "🤖 unknown_prov"

    # 3) المزودات القياسية تحتفظ بأيقوناتها كـ fallback عند غياب التخصيص
    assert delivery._format_icon_label("فلاش", "flash_lite", icons={}) == "⚡ فلاش"
    assert delivery._format_icon_label("شلبي", "shelby", icons={}) == "🦙 شلبي"
    assert delivery._format_icon_label("سبك", "synth", icons={}) == "🏆 سبك"

    # 4) الحفاظ على الأيقونة القائمة إذا كان الوسم يحتوي بالفعل على إيموجي
    assert delivery._format_icon_label("⚡ مسودة جاهزة", "flash_lite", icons=custom_icons) == "⚡ مسودة جاهزة"


def test_pipeline_defaults_has_empty_icons_dict_ki_04():
    """KI-04 (P3-03): DEFAULT_DISPLAY يحتوي على قاموس icons فارغ افتراضياً."""
    assert "icons" in pipeline_defaults.DEFAULT_DISPLAY
    assert pipeline_defaults.DEFAULT_DISPLAY["icons"] == {}


def test_pipeline_example_json_has_icons_example_ki_04():
    """KI-04 (P3-03): ملف القالب pipeline.example.json يعرض أمثلة لضبط icons."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    example_path = os.path.join(root, "pipeline.example.json")
    with open(example_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    disp = data.get("display") or {}
    assert "icons" in disp, "حقل icons مفقود في display داخل pipeline.example.json"
    assert disp["icons"].get("flash_lite") == "⚡"
    assert disp["icons"].get("shelby") == "🦙"
    assert disp["icons"].get("_example_groq") == "🤖"


def test_inject_system_context_max_owner_chars_none_preserves_full_context_ki_05():
    """KI-05 (P3-03): تمرير max_owner_chars=None يحقن كامل سياق المالك دون أي قص بالحروف."""
    base_sys = "برومبت النظام"
    long_owner = "أحمد مهندس برمجيات متخصص في بايثون وهندسة النظم " * 20
    res = text_assist.inject_system_context(
        base_sys,
        owner_context=long_owner,
        mode="polish",
        max_owner_chars=None,
    )
    assert long_owner.strip() in res
    assert "<owner_context>" in res


def test_delivery_source_has_no_raw_max_owner_chars_limit_ki_05():
    """KI-05 (P3-03): التأكد من إزالة max_owner_chars المربوط بالبايت في delivery واستبداله بـ None."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    delivery_path = os.path.join(root, "delivery.py")
    with open(delivery_path, "r", encoding="utf-8") as f:
        src = f.read()

    # التأكد من عدم استخدام inject_max_bytes كحروف max_owner_chars في delivery
    assert 'max_owner_chars = int(mem_cfg.get("inject_max_bytes"' not in src
    assert "max_owner_chars=None" in src
