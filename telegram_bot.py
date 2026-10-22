# -*- coding: utf-8 -*-
"""
بوت تليجرام للتفريغ الصوتي الفوري عبر Gemini 3.8 Flash مع تدوير المفاتيح (Key Pool)
"""

import sys
import os
import time
import base64
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

    # 3. متغيرات البيئة من نظام التشغيل أو لوحة الاستضافة (Hosting Environment Variables) - أعلى أولوية
    for env_var in ["GEMINI_API_KEYS", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME", "TELEGRAM_CHAT_ID", "RECORDINGS_DIR"]:
        val = os.environ.get(env_var)
        if val:
            keys[env_var] = val.strip()

    return keys

def get_api_keys_list(keys_dict):
    keys_str = keys_dict.get("GEMINI_API_KEYS") or keys_dict.get("GEMINI_API_KEY", "")
    return [k.strip() for k in keys_str.split(",") if k.strip()]

def transcribe_audio_bytes(audio_bytes, mime_type, api_keys):
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

    # تجربة الموديلات الذكية (3.8 ثم 3.6 ثم 3.5 Transcribe المتخصص) عبر المفاتيح
    for model in ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-transcribe"]:
        for key in api_keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                resp = requests.post(url, json=payload, timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    part = data["candidates"][0]["content"]["parts"][0]
                    if "text" in part:
                        return part["text"].strip()
                    elif "audioTranscription" in part:
                        return part["audioTranscription"].get("text", "").strip()
                elif resp.status_code == 429:
                    continue
            except Exception:
                continue

    # لو خلصت الـ 20 في كل المفاتيح، نرجع لـ 3.5 Flash Lite
    for key in api_keys:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={key}"
        try:
            resp = requests.post(url, json=payload, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            continue

    raise RuntimeError("فشلت المحاولات عبر جميع المفاتيح المتاحة.")

def main():
    keys = load_keys()
    bot_token = keys.get("TELEGRAM_BOT_TOKEN")
    api_keys = get_api_keys_list(keys)
    allowed_chat_id = keys.get("TELEGRAM_CHAT_ID", "1124247595")

    if not bot_token or not api_keys:
        print("❌ خطأ: تأكد من وجود TELEGRAM_BOT_TOKEN و GEMINI_API_KEYS في ملف keys.txt")
        sys.exit(1)

    print("=" * 60)
    print("🤖 جاري تشغيل بوت التليجرام للتفريغ الصوتي الفوري...")
    print(f"👤 البوت: {keys.get('TELEGRAM_BOT_USERNAME')}")
    print(f"🔑 عدد مفاتيح الـ Pool: {len(api_keys)}")
    print(f"🔒 مخصص للمستخدم: {allowed_chat_id}")
    print("=" * 60)

    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset

            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            res = requests.get(url, params=params, timeout=40)
            if res.status_code != 200:
                time.sleep(2)
                continue

            updates = res.json().get("result", [])
            for up in updates:
                offset = up["update_id"] + 1
                msg = up.get("message")
                if not msg:
                    continue

                chat_id = str(msg["chat"]["id"])
                if allowed_chat_id and chat_id != str(allowed_chat_id):
                    continue

                voice = msg.get("voice") or msg.get("audio") or msg.get("document")
                if voice:
                    file_id = voice["file_id"]
                    mime_type = voice.get("mime_type", "audio/ogg")

                    requests.post(
                        f"https://api.telegram.org/bot{bot_token}/sendChatAction",
                        json={"chat_id": chat_id, "action": "typing"}
                    )

                    print(f"🎙️ تم استلام تسجيل صوتي من {chat_id}...")

                    f_info = requests.get(f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}").json()
                    file_path = f_info.get("result", {}).get("file_path")
                    if not file_path:
                        continue

                    download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                    audio_bytes = requests.get(download_url).content

                    try:
                        text = transcribe_audio_bytes(audio_bytes, mime_type, api_keys)
                        print(f"✅ تم التفريغ: {text[:50]}...")
                        reply_text = f"```text\n{text}\n```"
                        requests.post(
                            f"https://api.telegram.org/bot{bot_token}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": reply_text,
                                "parse_mode": "MarkdownV2",
                                "reply_to_message_id": msg["message_id"]
                            }
                        )
                    except Exception as e:
                        print(f"❌ خطأ: {e}")
                        requests.post(
                            f"https://api.telegram.org/bot{bot_token}/sendMessage",
                            json={"chat_id": chat_id, "text": f"❌ عذراً حدث خطأ: {e}"}
                        )
                elif msg.get("text") == "/start":
                    welcome = (
                        "أهلاً بك يا بطل! 🎙️⚡\n\n"
                        "أنا بوت التفريغ الصوتي الفوري عبر Gemini 3.8 Flash.\n"
                        "ابعتلي أي فويس نوت في أي وقت، وهفرغهولك فوراً بنص جاهز للنسخ بلمسة واحدة! 🚀"
                    )
                    requests.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": chat_id, "text": welcome}
                    )

        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    main()
