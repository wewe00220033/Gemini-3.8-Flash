# -*- coding: utf-8 -*-
"""
أداة التفريغ الصوتي الذكية الفورية - مدعومة بـ Gemini Free Tier مع ميزة تدوير المفاتيح التلقائي (Key Pool)
- تقرأ قائمة المفاتيح تلقائياً من keys.txt
- تستخدم موديل Gemini 3.8 Flash فائق الذكاء كأولوية أولى
- تدوّر المفاتيح أوتوماتيكياً (Auto-Failover) عند الوصول للحد الأقصى (429)
- تنسخ النص المفرغ للحافظة (Clipboard) تلقائياً
- تحفظ نسخة نصية بجانب الملف
"""

import sys
import os
import glob
import json
import base64
import subprocess
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(SCRIPT_DIR, "keys.txt")
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

def load_keys():
    keys = {}
    
    # 1. قراءة من ملف .env إن وجد
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    keys[k.strip()] = v.strip()

    # 2. قراءة من ملف keys.txt المحلي إن وجد
    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    keys[k.strip()] = v.strip()

    # 3. متغيرات البيئة من نظام التشغيل أو لوحة الاستضافة
    for env_var in ["GEMINI_API_KEYS", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME", "TELEGRAM_CHAT_ID", "RECORDINGS_DIR"]:
        val = os.environ.get(env_var)
        if val:
            keys[env_var] = val.strip()

    return keys

def get_api_keys_list(keys_dict):
    keys_str = keys_dict.get("GEMINI_API_KEYS") or keys_dict.get("GEMINI_API_KEY", "")
    return [k.strip() for k in keys_str.split(",") if k.strip()]

def copy_to_clipboard(text):
    try:
        process = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
            stdin=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        process.communicate(input=text.encode("utf-8"))
        return True
    except Exception as e:
        print(f"تعذر النسخ للحافظة تلقائياً: {e}")
        return False

def get_latest_recording(recordings_dir):
    if not os.path.exists(recordings_dir):
        return None
    patterns = ["*.m4a", "*.mp3", "*.wav", "*.ogg", "*.aac", "*.flac"]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(os.path.join(recordings_dir, pattern)))
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]

def transcribe_audio(audio_path, api_keys):
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"الملف غير موجود: {audio_path}")

    ext = os.path.splitext(audio_path)[1].lower()
    mime_map = {
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".mp3": "audio/mp3",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".aac": "audio/aac",
        ".flac": "audio/flac"
    }
    mime_type = mime_map.get(ext, "audio/mp4")

    print(f"⏳ جاري قراءة الملف الصوتي: {os.path.basename(audio_path)} ...")
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    b64_data = base64.b64encode(audio_bytes).decode("utf-8")

    prompt = (
        "أنت مفرغ صوتي محترف فائق الدقة والذكاء. فرغ هذا التسجيل الصوتي بدقة متناهية كلمة بكلمة إلى نص عربي سليم.\n"
        "القواعد الصارمة الإلزامية:\n"
        "1. المصطلحات التقنية والبرمجية وأسماء الأدوات والخدمات تكتب باللغة الإنجليزية بأحرفها اللاتينية الأصلية القياسية (مثل GitHub, AI, Gist, IP, API, Python, Windows, Groq).\n"
        "2. اكتب الكلمات العربية بضبط إملائي ونحوي سليم دون إسقاط أي حرف أو كلمة ودون أي تخمين زائد.\n"
        "3. أخرج النص المفرغ فقط، دون أي مقدمات أو خاتمة أو شروحات."
    )

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
                        "text": prompt
                    }
                ]
            }
        ]
    }

    # الموديلات فائقة الذكاء أولاً (3.8 ثم 3.6 ثم 3.5 Transcribe المتخصص) عبر المفاتيح
    high_tier_models = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-transcribe"]
    for model in high_tier_models:
        for idx, key in enumerate(api_keys, 1):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                print(f"🚀 جاري الإرسال لـ ({model}) بالمفتاح #{idx} ...")
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    part = data["candidates"][0]["content"]["parts"][0]
                    if "text" in part:
                        return part["text"].strip()
                    elif "audioTranscription" in part:
                        return part["audioTranscription"].get("text", "").strip()
                elif resp.status_code == 429:
                    print(f"⚠️ {model} على المفتاح #{idx} وصل للحد الأقصى (429)، جاري تجربة التالي...")
                    continue
                else:
                    print(f"⚠️ تنبيه من {model} #{idx} (Status {resp.status_code}): {resp.text[:120]}")
            except Exception as e:
                print(f"⚠️ خطأ اتصال في {model} #{idx}: {e}")
                continue

    # لو كل المفاتيح خلصت حصتها في 3.8 Flash، ننتقل للموديل الاحتياطي
    print("🔄 كل مفاتيح 3.8 Flash مستهلكة، جاري التحويل للموديل الاحتياطي (gemini-3.5-flash-lite)...")
    for key in api_keys:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={key}"
        try:
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            continue

    raise RuntimeError("فشلت كافة المحاولات عبر جميع المفاتيح والموديلات المتاحة.")

def main():
    keys = load_keys()
    api_keys = get_api_keys_list(keys)
    if not api_keys:
        print("❌ خطأ: لم يتم العثور على أي مفاتيح في ملف keys.txt")
        sys.exit(1)

    recordings_dir = keys.get("RECORDINGS_DIR", r"C:\Users\USER\Documents\تسجيلات الصوت")

    if len(sys.argv) > 1:
        target_file = sys.argv[1]
    else:
        target_file = get_latest_recording(recordings_dir)
        if not target_file:
            print(f"❌ لم يتم العثور على أي ملفات صوتية في المجلد:\n{recordings_dir}")
            sys.exit(1)

    print("=" * 60)
    print(f"🎙️ تفريغ الملف: {os.path.basename(target_file)}")
    print(f"📂 المسار: {target_file}")
    print(f"🔑 عدد المفاتيح المتوفرة في الـ Pool: {len(api_keys)}")
    print("=" * 60)

    try:
        transcription = transcribe_audio(target_file, api_keys)
        
        txt_path = os.path.splitext(target_file)[0] + ".txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(transcription)

        copied = copy_to_clipboard(transcription)

        print("\n" + "=" * 60)
        print("✅ تم التفريغ بنجاح:")
        print("=" * 60)
        print(transcription)
        print("=" * 60)
        if copied:
            print("📋 ✨ تم نسخ النص تلقائياً إلى الحافظة (Clipboard)! تقدر تعمل Paste في أي مكان الآن.")
        print(f"💾 تم حفظ نسخة نصية في: {txt_path}")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"\n❌ حدث خطأ أثناء التفريغ: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
