# -*- coding: utf-8 -*-
"""
محرك التفريغ الصوتي المتعدد الوسائط (Core Multimodal Transcription Engine)
- يعمل حصرياً على الثلاثي الفائز: 3.8 Flash ➔ 3.6 Flash ➔ 3.5 Transcribe
- مستبعد منه موديل Lite نهائياً لضمان أعلى جودة ونبرة
- تدوير ذكي للمفاتيح النشطة وإدارة التهدئة عند كود 429
"""

import os
import base64
import requests
from config import Config
from key_manager import key_manager

def transcribe_audio_bytes(audio_bytes, mime_type="audio/mp4"):
    """
    تفريغ بايتات الصوت مباشرة عبر أفضل الموديلات المتاحة
    مع الفشل الآمن التلقائي (Auto-Failover)
    """
    if not audio_bytes:
        raise ValueError("بيانات الصوت فارغة!")

    b64_data = base64.b64encode(audio_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_data
                        }
                    },
                    {
                        "text": Config.SYSTEM_PROMPT
                    }
                ]
            }
        ]
    }

    last_error = None

    if not key_manager.data.get("keys"):
        raise RuntimeError("لا توجد أي مفاتيح API مسجلة في المنظومة! أرسل مفتاح Gemini في الشات أو اضبط GEMINI_API_KEYS.")

    tried_any = False
    # حلقة الموديلات (3.8 ثم 3.6 ثم 3.5 Transcribe) — لكل موديل نقرأ المفاتيح المتاحة له الآن
    for model in Config.MODELS:
        for key_entry in key_manager.get_active_keys(model=model):
            tried_any = True
            key_str = key_entry.get("key")
            key_name = key_entry.get("name", "Project")
            # المفتاح في Header (توصية Google) وليس في الـ URL حتى لا يتسرب للـ logs/proxies
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {"x-goog-api-key": key_str, "Content-Type": "application/json"}

            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=Config.REQUEST_TIMEOUT)

                if resp.status_code == 200:
                    text = _extract_text(resp.json())
                    if text:
                        key_manager.record_success(key_str, model=model)
                        return text, model, key_name
                    last_error = f"{model} رجّع رداً بدون نص"

                elif resp.status_code == 429:
                    retry_after = _parse_retry_delay(resp)
                    print(f"⚠️ كوتا 429 على [{key_name}] لموديل [{model}] -> قفل هذا الموديل على هذا المفتاح وتجربة التالي...")
                    key_manager.mark_key_exhausted(key_str, model=model, retry_after=retry_after)
                    continue
                else:
                    last_error = f"Status {resp.status_code}: {resp.text[:150]}"
            except Exception as e:
                last_error = str(e)
                continue

    if not tried_any:
        wait = key_manager.earliest_availability()
        if wait:
            mins = max(1, wait // 60)
            raise RuntimeError(
                f"كل المفاتيح في فترة تهدئة أو استنفدت كوتاها اليومية. "
                f"أقرب مفتاح يعود للعمل بعد {mins} دقيقة (تصفير الكوتا: {key_manager.next_reset_display()})."
            )

    raise RuntimeError(f"فشلت كافة المحاولات عبر جميع الموديلات والمفاتيح. آخر خطأ: {last_error}")


def _extract_text(data):
    """يستخرج النص من رد Gemini (حقل text أو audioTranscription)."""
    try:
        part = data["candidates"][0]["content"]["parts"][0]
    except (KeyError, IndexError, TypeError):
        return ""
    if "text" in part:
        return (part.get("text") or "").strip()
    if "audioTranscription" in part:
        return (part["audioTranscription"].get("text") or "").strip()
    return ""


def _parse_retry_delay(resp):
    """
    يحاول قراءة مدة الانتظار من رد 429:
    - Header `Retry-After` (ثوانٍ)
    - body: error.details[].retryDelay مثل "37s" (Google RetryInfo)
    يرجع ثوانٍ أو None.
    """
    try:
        ra = resp.headers.get("Retry-After") if hasattr(resp, "headers") and resp.headers else None
        if ra and str(ra).strip().isdigit():
            return int(ra)
    except Exception:
        pass
    try:
        body = resp.json()
        for d in (body.get("error", {}).get("details") or []):
            rd = d.get("retryDelay")
            if isinstance(rd, str) and rd.endswith("s"):
                return int(float(rd[:-1]))
    except Exception:
        pass
    return None


def transcribe_audio_file(file_path):
    """تفريغ ملف صوتي من القرص المحلي"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"الملف غير موجود: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".mp3": "audio/mp3",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".webm": "audio/webm",
        ".aac": "audio/aac",
        ".flac": "audio/flac"
    }
    mime_type = mime_map.get(ext, "audio/mp4")

    with open(file_path, "rb") as f:
        audio_bytes = f.read()

    return transcribe_audio_bytes(audio_bytes, mime_type)
