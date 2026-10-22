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
    active_keys_entries = key_manager.get_active_keys()

    if not active_keys_entries:
        raise RuntimeError("لا توجد أي مفاتيح API مسجلة في المنظومة!")

    # حلقة الموديلات (3.8 ثم 3.6 ثم 3.5 Transcribe)
    for model in Config.MODELS:
        # حلقة المفاتيح النشطة
        for key_entry in active_keys_entries:
            key_str = key_entry.get("key")
            key_name = key_entry.get("name", "Project")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key_str}"

            try:
                # print(f"🚀 إرسال لـ [{model}] عبر [{key_name}]...")
                resp = requests.post(url, json=payload, timeout=Config.REQUEST_TIMEOUT)

                if resp.status_code == 200:
                    data = resp.json()
                    part = data["candidates"][0]["content"]["parts"][0]
                    text = ""
                    if "text" in part:
                        text = part["text"].strip()
                    elif "audioTranscription" in part:
                        text = part["audioTranscription"].get("text", "").strip()

                    if text:
                        # تسجيل نجاح المفتاح
                        key_manager.record_success(key_str)
                        return text, model, key_name

                elif resp.status_code == 429:
                    print(f"⚠️ كوتا 429 على المفتاح [{key_name}] لموديل [{model}] -> جاري وضعه في التهدئة وتجربة التالي...")
                    key_manager.mark_key_exhausted(key_str)
                    continue
                else:
                    last_error = f"Status {resp.status_code}: {resp.text[:150]}"
            except Exception as e:
                last_error = str(e)
                continue

    raise RuntimeError(f"فشلت كافة المحاولات عبر جميع الموديلات والمفاتيح. آخر خطأ: {last_error}")

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
