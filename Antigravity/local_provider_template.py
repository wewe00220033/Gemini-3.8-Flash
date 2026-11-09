#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
local_provider_template.py — قالب استرشادي لمزود التنظيم النصي المحلي المستقل (Black-Box CLI)

📌 تعليمات الاستخدام والأمان:
1. انسخ هذا الملف وضعه في أي مسار خارجي على جهازك بعيداً عن مستودع المشروع تماماً (مثلاً: D:/private/my_polish.py).
2. ضع بداخله كعكات التصفح (Cookies)، الجلسات (Sessions)، أو بيانات الاعتماد الخاصة بمزودك الخارجي.
3. عدّل ملف .env داخل المشروع ليشير لأمر تشغيل هذا الملف:
   LOCAL_POLISH_CMD=python D:/private/my_polish.py
   LOCAL_POLISH_TIMEOUT=180.0
   COMPARE_MODE=synthesizer
4. البوت سيرسل النص الخام ونصوص التوجيه عبر stdin، والسكربت يعيد النص المنظم فقط عبر stdout بترميز UTF-8.
"""

import sys

# فرض ترميز UTF-8 لدعم العامية المصرية والرموز التعبيرية بدون مشاكل على أنظمة ويندوز
if sys.platform == "win32":
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def execute_custom_provider(raw_text: str, system_prompt: str = "") -> str:
    """
    هنا تضع منطق الاتصال بمزودك الخارجي:
    - إرسال طلب HTTP بالتوكنات والكوكيز الخاصة بك.
    - أو استدعاء جلسة تصفح محلية.
    - أو التخاطب مع نموذج محلي (Ollama / Local LLM).
    """
    # مثال توضيحي: محاكاة إرجاع النص منظم بنجاح
    # يمكنك استبدال هذا الجزء بالاتصال الحقيقي بمزودك
    
    # رسالة تنظيم تجريبية توضح نجاح الاستدعاء المحلي
    polished_output = raw_text.strip()
    return polished_output


def _split_payload(input_data: str) -> tuple[str, str]:
    """يفصل توجيه النظام عن النص الخام وفق بروتوكول ===USER_INPUT===."""
    marker = "===USER_INPUT==="
    if marker in input_data:
        sys_part, _, raw_part = input_data.partition(marker)
        return raw_part.strip(), sys_part.strip()
    return input_data, ""


def main():
    try:
        # قراءة النص الممرر من البوت عبر مدخل الأوامر stdin وفصل البرومبت عن النص الخام
        input_data = sys.stdin.read()
        if not input_data.strip():
            sys.exit(0)

        raw_text, system_prompt = _split_payload(input_data)
        if not raw_text.strip():
            sys.exit(0)

        # استدعاء المزود الخارجي بالنص الخام وتوجيه النظام مفصولين
        result = execute_custom_provider(raw_text, system_prompt=system_prompt)

        # إخراج النص المنظم مباشرة عبر stdout ليقرأه البوت
        sys.stdout.write(result)
        sys.stdout.flush()
        sys.exit(0)

    except Exception as e:
        # عند حدوث أي خطأ، نكتب التفاصيل في stderr ليقرأها البوت ويسجلها في السجلات
        sys.stderr.write(f"Local Provider Error: {e}\n")
        sys.stderr.flush()
        sys.exit(1)


if __name__ == "__main__":
    main()

