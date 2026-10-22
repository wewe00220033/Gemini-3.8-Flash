# -*- coding: utf-8 -*-
"""
نقطة الانطلاق الرئيسية الموحدة 360° (Master Unified Runner)
- يشغل خادم الويب والواجهة الصوتية في الخلفية (Web Service)
- يشغل بوت التليجرام الفوري في الواجهة الرئيسية (Telegram Bot)
- مهيأ للعمل محلياً أو على خوادم الاستضافة (24/7 Hosting)
"""

import sys
import time
from config import Config
from key_manager import key_manager
from web_service import start_web_server_thread
import telegram_bot

def main():
    print("=" * 70)
    print("🚀 بدء تشغيل منظومة التفريغ الصوتي الذكي 360° (Gemini Multi-Model)")
    print("=" * 70)

    # 1. إطلاق خادم الويب الصوتي
    try:
        start_web_server_thread()
        print(f"🌐 واجهة الويب جاهزة للاستخدام: http://{Config.WEB_HOST}:{Config.WEB_PORT}")
    except Exception as e:
        print(f"⚠️ تحذير: تعذر تشغيل خادم الويب: {e}")

    # 2. إطلاق بوت التليجرام
    bot_token, _, _ = key_manager.get_telegram_config()
    if bot_token:
        try:
            telegram_bot.main()
        except KeyboardInterrupt:
            print("\n👋 تم إيقاف المنظومة بنجاح.")
    else:
        print("ℹ️ لم يتم العثور على توكن تليجرام، المنظومة تعمل في وضع الويب فقط.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n👋 تم إيقاف المنظومة بنجاح.")

if __name__ == "__main__":
    main()
