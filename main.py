# -*- coding: utf-8 -*-
"""
نقطة الانطلاق الرئيسية الموحدة 360° (Master Unified Runner)
- يشغل خادم الويب والواجهة الصوتية في خيط خلفي (Web Service)
- يشغل بوت التليجرام في خيط خلفي (Telegram Bot)
- Watchdog: يراقب الاثنين ويعيد تشغيل أيهما يقع، ويبلغك على تليجرام
- Startup Ping: رسالة على تليجرام عند الإقلاع (تعرف لو الاستضافة عملت Restart)
- مهيأ للعمل محلياً أو على خوادم الاستضافة (24/7 Hosting)
"""

import sys
import time
import logging
import threading
from config import Config
from key_manager import key_manager
import web_service
import telegram_bot


class _PrintToLog:
    """يحوّل print() في كل الوحدات إلى سجلات بطابع زمني بدون تغيير الرسائل العربية الحالية."""
    def __init__(self, logger, level):
        self._logger, self._level, self._buf = logger, level, ""

    def write(self, text):
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line)

    def flush(self):
        if self._buf.strip():
            self._logger.log(self._level, self._buf)
        self._buf = ""

    def isatty(self):
        return False


def _setup_logging():
    """
    سجلات بطابع زمني ومستوى (LOG_LEVEL=DEBUG|INFO|WARNING)، تُكتب لـ stderr الأصلي.
    print() في كل الملفات يمر عبرها تلقائياً — مفيد على الاستضافة لمعرفة متى حصل كل شيء.
    """
    level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.__stderr__)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    sys.stdout = _PrintToLog(logging.getLogger("app"), logging.INFO)
    sys.stderr = _PrintToLog(logging.getLogger("app"), logging.ERROR)


def _bot_thread():
    t = threading.Thread(target=telegram_bot.main, daemon=True, name="telegram-bot")
    t.start()
    return t


def _startup_ping(bot_token, chat_id):
    """إشعار إقلاع مختصر على تليجرام."""
    if not (Config.NOTIFY_ON_START and bot_token and chat_id):
        return
    try:
        now = time.time()
        keys = key_manager.data.get("keys", [])
        available = sum(
            1 for k in keys if any(key_manager.is_model_available(k, m, now) for m in Config.MODELS)
        )
        remaining = sum(
            v for k in keys for m, v in key_manager.remaining_today(k).items()
            if key_manager.is_model_available(k, m, now)
        )
        msg = (
            "🚀 المنظومة اشتغلت الآن.\n"
            f"🔑 مفاتيح: {len(keys)} (متاح: {available})\n"
            f"⚡ رصيد متاح اليوم: {remaining} ريكورد\n"
            f"🌐 الويب على المنفذ {Config.WEB_PORT}\n"
            f"🕛 تصفير الكوتا: {key_manager.next_reset_display()} ({Config.DISPLAY_TZ})"
        )
        telegram_bot.send_telegram_message(bot_token, chat_id, msg)
    except Exception as e:
        print(f"⚠️ تعذر إرسال إشعار الإقلاع: {e}")


def main():
    # على الاستضافة stdout غالباً pipe → بدون line buffering لا يظهر شيء في الكونسول إلا عند الامتلاء
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
        sys.stderr.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass
    _setup_logging()

    print("=" * 70)
    print("🚀 بدء تشغيل منظومة التفريغ الصوتي الذكي 360° (Gemini Multi-Model)")
    print("=" * 70)

    bot_token, _, chat_id = key_manager.get_telegram_config()

    # 1. خادم الويب — الربط بالمنفذ يتم هنا (في الخيط الرئيسي) كي يُكتشف فشل المنفذ فوراً لا بصمت (G27)
    web_t = None
    web_error = None
    try:
        web_t = web_service.start_web_server_thread()
        print(f"🌐 واجهة الويب جاهزة للاستخدام: http://{Config.WEB_HOST}:{Config.WEB_PORT}  (health: /health)")
    except Exception as e:
        web_error = f"{type(e).__name__}: {e}"
        print(f"⚠️ تحذير: تعذر تشغيل خادم الويب على {Config.WEB_HOST}:{Config.WEB_PORT}: {web_error} — البوت يكمل بلا ويب.")

    # 2. بوت تليجرام
    bot_t = None
    if bot_token:
        bot_t = _bot_thread()
        time.sleep(1.5)
        _startup_ping(bot_token, chat_id)
        if web_error and chat_id:
            # G27: إشعار مرة واحدة فقط — المالك كان يظن أن الويب يعمل لأن اللوج وحده يعرف
            try:
                telegram_bot.send_telegram_message(
                    bot_token, chat_id,
                    f"⚠️ الويب لم يشتغل على المنفذ {Config.WEB_PORT}: {web_error}\nالبوت شغال طبيعي. /health غير متاح حتى يُصلح المنفذ."
                )
            except Exception as e:
                print(f"⚠️ تعذر إرسال إشعار فشل الويب: {e}")
    else:
        print("ℹ️ لم يتم العثور على توكن تليجرام، المنظومة تعمل في وضع الويب فقط.")

    # 3. Watchdog
    web_restarts = bot_restarts = 0
    try:
        while True:
            time.sleep(Config.WATCHDOG_INTERVAL)

            if web_t is not None and not web_t.is_alive():
                web_restarts += 1
                print(f"🩺 Watchdog: خادم الويب توقف — إعادة تشغيل (#{web_restarts})...")
                try:
                    web_t = web_service.start_web_server_thread()
                except Exception as e:
                    print(f"⚠️ فشل إعادة تشغيل الويب: {e}")

            if bot_t is not None and not bot_t.is_alive():
                bot_restarts += 1
                print(f"🩺 Watchdog: بوت تليجرام توقف — إعادة تشغيل (#{bot_restarts})...")
                bot_t = _bot_thread()
                if bot_token and chat_id and Config.NOTIFY_ON_START:
                    telegram_bot.send_telegram_message(
                        bot_token, chat_id,
                        f"🩺 Watchdog أعاد تشغيل البوت (#{bot_restarts}). آخر خطأ: {telegram_bot.STATE.get('last_error')}"
                    )
    except KeyboardInterrupt:
        print("\n👋 تم إيقاف المنظومة بنجاح.")


if __name__ == "__main__":
    main()
