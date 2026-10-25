# -*- coding: utf-8 -*-
"""
المساعد النصي (Text Assist) — مواصفة 02 §1 عمود 3 و07 (المرحلة 3c).

`gemini-3.5-flash-lite` مسموح **نصياً فقط** (تنظيم/تسمية/أدوار) بأقصى تفكير — **ممنوع في التفريغ**
(اختبار `test_lite_never_used_for_transcription` يحرس التقاطع).

- `polish(raw_text, role)`: نص خام → نص منظم عبر طبقة B (prompts.compose_prompt). لا صوت أبداً.
- يدور على كل المفاتيح المتاحة؛ 429 يومي على lite يُسجَّل في `exhausted_models` للمفتاح (موديل مستقل بحصته).
- فشل التنظيم لا يرفع استثناءً للمنادي العادي: `polish()` ترجع (None, reason) — الخام لا يتأثر (07 §2.2).
- Spike (17): flash-lite يستخدم `thinkingConfig.thinkingBudget` لا `thinkingLevel`. لو رجع 400 بسبب thinkingConfig
  نعيد المحاولة مرة واحدة بدونه (TEXT_ASSIST_THINKING_FALLBACK) ونسجّل ذلك — لا نخمّن الصيغة بصمت.
"""

import time
import threading

import requests

from config import Config
from key_manager import key_manager
import prompts
import transcriber

_lock = threading.RLock()
_state = {"thinking_ok": None, "calls": 0, "ok": 0, "fail": 0, "last_error": None}


def model_name():
    return Config.TEXT_ASSIST_MODEL


def assert_not_transcription_model():
    """حارس: موديل المساعد النصي لا يجوز أن يكون ضمن موديلات التفريغ ولا أن يحوي 'lite' موديلٌ للتفريغ."""
    if model_name() in Config.MODELS:
        raise RuntimeError(f"TEXT_ASSIST_MODEL={model_name()} موجود في Config.MODELS — Lite ممنوع للتفريغ")
    for m in Config.MODELS:
        if "lite" in m.lower():
            raise RuntimeError(f"موديل Lite ({m}) داخل سلسلة التفريغ — ممنوع بقرار المالك")


def _payload(system_text, user_text, with_thinking=True):
    payload = {
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": [{"parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.2},
    }
    if with_thinking and Config.TEXT_ASSIST_THINKING_BUDGET:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": int(Config.TEXT_ASSIST_THINKING_BUDGET)}
    return payload


def generate(system_text, user_text, timeout=None):
    """
    نداء نصي واحد عبر أفضل مفتاح متاح لموديل المساعد. يرجع (text, None) أو (None, reason).
    لا يلمس قواطع موديلات التفريغ (model_circuit) — موديل مستقل.
    """
    model = model_name()
    timeout = timeout or (Config.GEMINI_CONNECT_TIMEOUT, Config.TEXT_ASSIST_TIMEOUT)
    keys = key_manager.get_active_keys(model=model)
    if not keys:
        return None, "لا مفتاح متاح للمساعد النصي الآن"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last_reason = "فشل غير معروف"
    for key_entry in keys:
        key_str, key_name = key_entry.get("key"), key_entry.get("name", "Project")
        with_thinking = _state["thinking_ok"] is not False
        for attempt in range(2):
            with _lock:
                _state["calls"] += 1
            t0 = time.time()
            try:
                resp = requests.post(url, json=_payload(system_text, user_text, with_thinking),
                                     headers={"x-goog-api-key": key_str, "Content-Type": "application/json"},
                                     timeout=timeout)
            except requests.exceptions.RequestException as e:
                last_reason = f"شبكة: {type(e).__name__}"
                break
            att = transcriber.classify_response(resp)
            elapsed = time.time() - t0
            if att.outcome == transcriber.SUCCESS:
                with _lock:
                    _state["ok"] += 1
                    if with_thinking:
                        _state["thinking_ok"] = True
                key_manager.record_success(key_str, model=model)
                print(f"✨ [{model.replace('gemini-', '')}] [{key_name}] polish في {elapsed:.1f} ث")
                return att.text, None
            # thinkingConfig غير مقبول؟ (400 يذكر thinking) → إعادة بدون تفكير مرة واحدة
            if att.outcome == transcriber.BAD_REQUEST and with_thinking and "thinking" in (att.detail or "").lower():
                with _lock:
                    _state["thinking_ok"] = False
                print(f"ℹ️ المساعد النصي: {model} رفض thinkingConfig ({att.detail[:80]}) — إعادة بدون تفكير")
                with_thinking = False
                continue
            if att.outcome == transcriber.KEY_EXHAUSTED_DAILY:
                key_manager.mark_key_exhausted(key_str, model=model, daily=True)
                last_reason = "حصة المساعد النصي اليومية نفدت على هذا المفتاح"
            elif att.outcome == transcriber.KEY_RATE_LIMITED:
                key_manager.mark_key_exhausted(key_str, model=model, retry_after=att.retry_after)
                last_reason = "تهدئة 429 للمساعد النصي"
            elif att.outcome == transcriber.KEY_INVALID:
                key_manager.mark_key_invalid(key_str, reason=att.detail)
                last_reason = "مفتاح مرفوض"
            else:
                last_reason = f"{att.outcome} ({att.status})"
                if att.outcome in transcriber.MODEL_OUTCOMES or att.outcome in (transcriber.BAD_REQUEST, transcriber.BLOCKED):
                    with _lock:
                        _state["fail"] += 1
                        _state["last_error"] = last_reason
                    return None, last_reason     # مشكلة موديل/طلب: مفاتيح أخرى لن تساعد
            break   # مشكلة مفتاح → المفتاح التالي
    with _lock:
        _state["fail"] += 1
        _state["last_error"] = last_reason
    return None, last_reason


def polish(raw_text, role_id=None, glossary=None):
    """
    يحوّل الخام إلى منظم (طبقة B). يرجع (polished, None) أو (None, reason). لا يلمس الخام ولا الصوت.
    القاموس (scope polished) يُطبَّق على الناتج.
    """
    if not (raw_text or "").strip():
        return None, "نص فارغ"
    role_id = role_id or Config.DEFAULT_ROLE
    system_text = prompts.compose_prompt("polish", role_id)
    text, reason = generate(system_text, raw_text)
    if text is None:
        return None, reason
    if glossary is not None:
        text, _n = glossary.apply(text, target="polished")
    return text.strip(), None


def stats():
    with _lock:
        return dict(_state)
