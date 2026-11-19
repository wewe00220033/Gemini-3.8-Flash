# -*- coding: utf-8 -*-
"""
guards.py — صمامات الأمان والحراس الحتمية لمنظومة التنظيم والسبك (T6c — DEC-P2-08/09).
"""

import difflib
import re
from typing import List, Optional, Any, Iterable

_HINDI_TO_ARABIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_NUM_RE = re.compile(r"\d+")
_LATIN_RE = re.compile(r"[A-Za-z]{2,}")
_LEGACY_SPEAKER_RE = re.compile(r"(?m)^\s*((?:المتكلم|Speaker)\s*\d+:)")


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


def is_context_echo(output_text: str, context_text: str, ratio: float) -> bool:
    """
    يفحص ما إذا كان الناتج يمثل صدى للسياق بنسبة تتجاوز العتبة ratio الممررة صراحة من المستدعي (DEC-P2-03/08).
    دالة نقية 100% بلا استيرادات داخلية وبلا ثوابت جامدة.
    """
    return context_echo_ratio(output_text, context_text) >= float(ratio)


def strip_single_speaker(text: str, tag_regex: str = r"^🗣️(\d+):") -> str:
    """
    إذا كان عدد وسوم المتكلمين الفريدة في النص <= 1: يجرد جميع وسوم المتكلم من بدايات الأسطر (T9 — DEC-P2-01).
    إذا كان النص متعدد المتكلمين (>= 2): يترك النص كما هو دون أي تغيير.
    يدعم وسوم نمط tag_regex والوسوم التقليدية (المتكلم 1: / Speaker 1:).
    دالة نقية 100% بدون استيرادات خارجية.
    """
    if not text:
        return text or ""
    try:
        pattern = re.compile(tag_regex, re.MULTILINE)
    except re.error:
        pattern = re.compile(r"^🗣️(\d+):", re.MULTILINE)

    matches = pattern.findall(text)
    if matches:
        unique_speakers = set(matches)
        if len(unique_speakers) <= 1:
            strip_pattern = re.compile(rf"(?m){pattern.pattern}\s*")
            text = strip_pattern.sub("", text)

    legacy_matches = _LEGACY_SPEAKER_RE.findall(text)
    if legacy_matches:
        unique_legacy = set(m.strip() for m in legacy_matches)
        if len(unique_legacy) <= 1:
            text = _LEGACY_SPEAKER_RE.sub("", text)

    return text


def forbid_new_speaker_tags(golden_text: str, raw_text: str, tag_regex: str = r"^🗣️(\d+):") -> List[str]:
    """
    يتحقق من أن وسوم المتكلمين في النسخة الذهبية مجموعة جزئية من وسوم الخام (T9 — DEC-P2-01).
    يرصد أيضاً الوسوم التقليدية (المتكلم 1: / Speaker 1:) لو استُحدثت في الذهبية (T9-fix2).
    يرجع قائمة بالوسوم الجديدة غير المصرح بها (إن وُجدت).
    دالة نقية 100% بدون استيرادات خارجية.
    """
    if not golden_text or not raw_text:
        return []

    try:
        pattern = re.compile(tag_regex, re.MULTILINE)
    except re.error:
        pattern = re.compile(r"^🗣️(\d+):", re.MULTILINE)

    raw_tags = set(pattern.findall(raw_text))
    golden_tags = set(pattern.findall(golden_text))

    new_tags = golden_tags - raw_tags
    formatted = []
    for t in sorted(new_tags):
        s = str(t)
        if s.startswith("🗣️"):
            formatted.append(s)
        else:
            formatted.append(f"🗣️{s}:")

    # رصد الوسوم التقليدية المستحدثة التي لم ترد في الخام
    raw_legacy = set(m.strip() for m in _LEGACY_SPEAKER_RE.findall(raw_text))
    golden_legacy = set(m.strip() for m in _LEGACY_SPEAKER_RE.findall(golden_text))
    new_legacy = golden_legacy - raw_legacy
    for lt in sorted(new_legacy):
        if lt not in formatted:
            formatted.append(lt)

    return formatted


def text_similarity_ratio(text1: str, text2: str) -> float:
    """
    يحسب نسبة التشابه بين نصين (0.0 إلى 1.0) باستخدام difflib.SequenceMatcher.
    دالة نقية 100% (T9-fix2 — DEC-P2-08/09).
    """
    if not text1 or not text2:
        return 0.0
    t1 = " ".join(str(text1).strip().split())
    t2 = " ".join(str(text2).strip().split())
    if t1 == t2:
        return 1.0
    return difflib.SequenceMatcher(None, t1, t2).ratio()


def is_duplicate_draft(text: str, existing_texts: Iterable[str], threshold: float = 0.98) -> bool:
    """
    يتحقق مما إذا كان النص مطابقاً أو شديد الشبه بنسبة >= threshold بأي من النصوص السابقة (T9-fix2).
    دالة نقية 100%.
    """
    if not text or not existing_texts or float(threshold) <= 0.0:
        return False
    thresh = float(threshold)
    for prev in existing_texts:
        if prev and text_similarity_ratio(text, prev) >= thresh:
            return True
    return False


