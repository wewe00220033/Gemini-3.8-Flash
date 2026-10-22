# -*- coding: utf-8 -*-
"""
بوت تليجرام للتفريغ الصوتي الذكي وإدارة المفاتيح 360° (@Zizo00ZIZO_bot)
- استقبال الفويس نوت والتفريغ الفوري بكود بلوك قابل للنسخ
- كشف المفاتيح التلقائي وإضافتها للـ Key Pool بمجرد إرسالها نصياً في الشات
- أوامر الفحص السريع: /status لعرض الرصيد وحالة المفاتيح
"""

import sys
import time
import requests
from config import Config
from key_manager import key_manager
from transcriber import transcribe_audio_bytes

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def send_telegram_message(token, chat_id, text, reply_to_id=None, parse_mode="Markdown"):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_to_id:
        payload["reply_to_message_id"] = reply_to_id
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"⚠️ فشل إرسال رسالة لتليجرام: {e}")

def main():
    bot_token, bot_user, allowed_chat_id = key_manager.get_telegram_config()

    if not bot_token:
        print("❌ خطأ: لم يتم العثور على TELEGRAM_BOT_TOKEN في keys.json أو متغيرات البيئة!")
        return

    print("=" * 65)
    print("🤖 جاري تشغيل بوت التليجرام للتفريغ الصوتي الفوري...")
    print(f"👤 البوت: {bot_user}")
    print(f"🔒 مخصص للمستخدم: {allowed_chat_id}")
    print("=" * 65)

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

                # التحقق الأمني من الـ Chat ID
                if allowed_chat_id and chat_id != str(allowed_chat_id):
                    print(f"⛔ رسالة محجوبة من مستخدم غير مصرح له: {chat_id}")
                    continue

                msg_text = msg.get("text", "")
                voice = msg.get("voice") or msg.get("audio") or msg.get("document")

                # ==============================================================
                # 1. معالجة التسجيلات الصوتية (Voice / Audio)
                # ==============================================================
                if voice:
                    file_id = voice["file_id"]
                    mime_type = voice.get("mime_type", "audio/ogg")

                    # إشعار الكتابة (Typing Action)
                    requests.post(
                        f"https://api.telegram.org/bot{bot_token}/sendChatAction",
                        json={"chat_id": chat_id, "action": "typing"}
                    )

                    print(f"🎙️ تم استلام تسجيل صوتي من {chat_id}...")

                    # تحميل الملف الصوتي
                    f_info = requests.get(f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}").json()
                    file_path = f_info.get("result", {}).get("file_path")
                    if not file_path:
                        continue

                    download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                    audio_bytes = requests.get(download_url).content

                    # التفريغ بالثلاثي الفائز
                    try:
                        text, model, key_name = transcribe_audio_bytes(audio_bytes, mime_type)
                        print(f"✅ تم التفريغ عبر [{model}] بالمفتاح [{key_name}]: {text[:40]}...")

                        # الإرسال في كود بلوك للنسخ بلمسة واحدة في تليجرام
                        reply = f"```text\n{text}\n```\n⚡ *الموديل:* `{model}` | 🔑 `{key_name}`"
                        send_telegram_message(bot_token, chat_id, reply, reply_to_id=msg["message_id"])

                    except Exception as e:
                        print(f"❌ خطأ تفريغ: {e}")
                        send_telegram_message(bot_token, chat_id, f"❌ عذراً، فشل التفريغ: {e}", reply_to_id=msg["message_id"])

                # ==============================================================
                # 2. معالجة النصوص والمفاتيح والأوامر (Text & Dynamic Key Detection)
                # ==============================================================
                elif msg_text:
                    # فحص إذا كان المستخدم يرسل مفتاح Gemini جديد
                    extracted_key, extracted_name = key_manager.extract_key_from_text(msg_text)
                    if extracted_key:
                        entry, is_new = key_manager.add_key(extracted_key, extracted_name)
                        status_str = "إضافة مفتاح جديد" if is_new else "تحديث المفتاح"
                        reply = (
                            f"🎉 *تم بنجاح {status_str} لمصفوفة المفاتيح (Key Pool)!*\n\n"
                            f"📌 *اسم المشروع:* `{entry.get('name')}`\n"
                            f"🔑 *المفتاح:* `{extracted_key[:8]}...{extracted_key[-4:]}`\n"
                            f"🟢 *الحالة:* `نشط جاهز للاستخدام`\n"
                            f"⚡ *رصيدك اليومي زاد بمعدل 65 ريكورد إضافي!* 🚀"
                        )
                        send_telegram_message(bot_token, chat_id, reply, reply_to_id=msg["message_id"])
                        continue

                    # الأوامر النصية
                    cmd = msg_text.strip().lower()
                    if cmd in ["/status", "/keys", "حالة", "المفاتيح"]:
                        summary = key_manager.get_status_summary()
                        send_telegram_message(bot_token, chat_id, summary, reply_to_id=msg["message_id"])

                    elif cmd in ["/start", "/help", "مساعدة"]:
                        guide = (
                            "👋 *أهلاً بك في منظومة التفريغ الصوتي الذكي 360°!* 🎙️⚡\n\n"
                            "• **للتفريغ:** ابعتلي أي فويس نوت أو ملف صوتي، وهفرغهولك فوراً بنص جاهز للنسخ بلمسة واحدة.\n"
                            "• **لإضافة مفتاح جديد:** ابعت مفتاح API هنا مباشرة (مع اسم أو بدونه) وهيتسجل في الـ Pool تلقائياً.\n"
                            "• **لحالة المفاتيح والرصيد:** أرسل أمر `/status`.\n\n"
                            "🛡️ *الموديلات النشطة:* `Gemini 3.8 Flash` ➔ `3.6 Flash` ➔ `3.5 Transcribe`\n"
                            "❌ *موديل Lite مستبعد نهائياً لأعلى جودة ممكنة!*"
                        )
                        send_telegram_message(bot_token, chat_id, guide, reply_to_id=msg["message_id"])

        except Exception as e:
            time.sleep(3)

if __name__ == "__main__":
    main()
