# -*- coding: utf-8 -*-
"""
guards.py — صمامات الأمان والحراس الحتمية لمنظومة التنظيم والسبك (T6c — DEC-P2-08/09).
"""

import re
from typing import List, Optional, Any

_HINDI_TO_ARABIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_NUM_RE = re.compile(r"\d+")
_LATIN_RE = re.compile(r"[A-Za-z]{2,}")


def normalize_digits(text: str) -> str:
    """تحويل الأرقام المشرقية/الهندية (٠-٩) إلى أرقام عربية قياسية (0-9)."""
    return (text or "").translate(_HINDI_TO_ARABIC)


def extract_numbers(text: str) -> set:
    """استخراج كافة الأرقام بعد تطبيع الأرقام الهندية."""
    norm = normalize_digits(text)
    return set(_NUM_RE.findall(norm))


def extract_latin_words(text: str) -> set:
    """استخراج الكلمات اللاتينية بطول حرفين على الأقل (حساسة لحالة الأحرف وموحدة بصيغة lower)."""
    if not text:
        return set()
    return {w.lower() for w in _LATIN_RE.findall(text)}


def numbers_latin_subset_check(
    golden_text: str,
    raw_text: str,
    drafts: Optional[List[Any]] = None,
    glossary: Optional[Any] = None,
) -> List[str]:
    """
    يفحص أن الأرقام والكلمات اللاتينية الواردة في النسخة الذهبية هي مجموعة جزئية (Subset)
    من المصادر الموثوقة: (الخام ∪ كل المسودات ∪ بدائل القاموس).
    يرجع قائمة بالعناصر المستحدثة (الهلوسة المحتملة).
    """
    if not golden_text:
        return []

    # 1. تجميع المصادر الموثوقة
    source_texts = [raw_text or ""]
    if drafts:
        for d in drafts:
            if isinstance(d, tuple) and len(d) >= 2:
                source_texts.append(str(d[1] or ""))
            elif isinstance(d, str):
                source_texts.append(d)

    # إضافة مصطلحات القاموس كأصل مشروع
    if glossary is not None:
        ents_getter = getattr(glossary, "entries", None)
        ents = ents_getter() if callable(ents_getter) else (ents_getter or getattr(glossary, "terms", None) or [])
        if isinstance(ents, list):
            for item in ents:
                if isinstance(item, dict):
                    if item.get("status") == "confirmed" or "status" not in item:
                        source_texts.append(str(item.get("replacement") or item.get("term") or ""))
                elif isinstance(item, str):
                    source_texts.append(item)
        elif isinstance(ents, dict):
            for k, v in ents.items():
                source_texts.append(str(k))
                source_texts.append(str(v))

    # 2. استخراج الأرقام من المصادر ومن الذهبية
    source_numbers = set()
    for st in source_texts:
        source_numbers.update(extract_numbers(st))

    golden_numbers = extract_numbers(golden_text)

    # 3. استخراج الكلمات اللاتينية من المصادر ومن الذهبية
    source_latin = set()
    for st in source_texts:
        source_latin.update(extract_latin_words(st))

    golden_latin_raw = _LATIN_RE.findall(golden_text)

    unexpected = []
    seen = set()
    # فحص الأرقام المستحدثة
    for num in sorted(golden_numbers):
        if num not in source_numbers and num not in seen:
            unexpected.append(num)
            seen.add(num)

    # فحص الكلمات اللاتينية المستحدثة
    for word in golden_latin_raw:
        w_lower = word.lower()
        if w_lower not in source_latin and w_lower not in seen:
            unexpected.append(word)
            seen.add(w_lower)

    return unexpected


def context_echo_ratio(output_text: str, context_text: str) -> float:
    """
    يحسب نسبة صدى السياق: نسبة ثلاثيات الكلمات (tri-grams) من الناتج الموجودة في السياق (DEC-P2-03/08).
    نصوص قصيرة جداً (< 6 كلمات ناتج أو < 3 كلمات سياق) ترجع 0.0 لتجنب الإنذارات الكاذبة.
    """
    if not output_text or not context_text:
        return 0.0
    norm = lambda t: re.sub(r"[^\w\s]", " ", t).split()
    out_w, ctx_w = norm(output_text), norm(context_text)
    if len(out_w) < 6 or len(ctx_w) < 3:
        return 0.0
    ctx_tri = {tuple(ctx_w[i:i + 3]) for i in range(len(ctx_w) - 2)}
    tris = [tuple(out_w[i:i + 3]) for i in range(len(out_w) - 2)]
    if not tris:
        return 0.0
    hit = sum(1 for t in tris if t in ctx_tri)
    return hit / len(tris)


def is_context_echo(output_text: str, context_text: str, ratio: Optional[float] = None) -> bool:
    """
    يفحص ما إذا كان الناتج يمثل صدى للسياق بنسبة تتجاوز العتبة (الافتراضي 0.60 من Config).
    """
    from config import Config
    cutoff = Config.CONTEXT_ECHO_RATIO if ratio is None else ratio
    return context_echo_ratio(output_text, context_text) >= cutoff

