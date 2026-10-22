# 🎙️ منظومة التفريغ الصوتي الذكي - Gemini Multi-Model AI

> **نظام متكامل وفائق السرعة لتفريغ الصوت بدقة خيالية، يدعم العامية المصرية، يحافظ على المصطلحات التقنية بالإنجليزية، ويعمل محلياً وعلى خوادم الاستضافة (24/7 Hosting) مجاناً 100%!**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Google Gemini API](https://img.shields.io/badge/Gemini-3.8%20%7C%203.6%20%7C%203.5-orange.svg)](https://aistudio.google.com/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot%20Ready-blue.svg)](https://telegram.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## 🌟 مميزات المنظومة

1. **دقة سمعية فائقة (Native Multimodal Audio):**
   - يفهم نبرة الصوت والعامية المصرية بدون اختلاق أو تخمين زائف.
   - يكتب المصطلحات البرمجية والتقنية بأحرفها الإنجليزية اللاتينية القياسية (`GitHub, AI, Python, Gist, IP, API`).
2. **المصفوفة الهندسية الثلاثية (The Winning Trio):**
   - **`gemini-3.8-flash`:** الموديل الأذكى عالمياً في فهم السياق والنبرة.
   - **`gemini-3.6-flash`:** التوأم فائق السرعة بنفس الدقة.
   - **`gemini-3.5-transcribe`:** محرك جوجل التخصصي للتفريغ الصوتي المباشر.
3. **ميزة تدوير المفاتيح التلقائي (Key Pool & Auto-Failover):**
   - يدعم إدخال قائمة مفاتيح من مشاريع متعددة لنفس الحساب.
   - عند وصول أي مفتاح للحد الأقصى (429)، ينتقل فوراً للمفتاح التالي بدون توقف.
4. **طريقتين للاستخدام:**
   - 📱 **من الموبايل:** عبر بوت تليجرام خفيف وسريع، يرسل التفريغ داخل كود بلوك يُنسخ بلمسة واحدة.
   - 💻 **من الكمبيوتر:** عبر سكريبت يفرغ أحدث تسجيل وينسخه تلقائياً في الـ Clipboard (الحافظة).

---

## 📐 الهيكلية الهندسية (Failover Architecture)

```mermaid
graph TD
    A[🎙️ فويس نوت من تليجرام أو ريكورد محلي] --> B[🔑 اختيار مفتاح من Key Pool]
    B --> C{1. فحص Gemini 3.8 Flash}
    C -->|200 OK| S[✅ تم التفريغ بنجاح + نسخ فوري]
    C -->|429 / مشغول| D{2. فحص Gemini 3.6 Flash}
    D -->|200 OK| S
    D -->|429 / مشغول| E{3. فحص Gemini 3.5 Transcribe}
    E -->|200 OK| S
    E -->|الكل مشغول| F[🔄 التبديل للمفتاح التالي في الـ Pool]
    F -->|لو كل المفاتيح انتهت| G{4. صمام الأمان: 3.5 Flash Lite}
    G -->|500 طلب يومياً| S
```

---

## 🚀 دليل النشر على خوادم الاستضافة (Deployment on Bot-Hosting / VPS)

المنظومة مهيأة للعمل على منصات الاستضافة المجانية والمدفوعة مثل **[bot-hosting.net](https://bot-hosting.net/)** أو Railway أو Render.

### 1. ضبط متغيرات البيئة (Environment Variables):
في لوحة تحكم الاستضافة (Hosting Settings / Variables)، أضف المتغيرات التالية:

| المتغير (Variable) | القيمة (Value) | الوصف |
| :--- | :--- | :--- |
| **`GEMINI_API_KEYS`** | `key1,key2,key3...` | مفاتيح Google AI Studio مفصولة بفواصل للتدوير |
| **`TELEGRAM_BOT_TOKEN`** | `8158611188:AAGbrz...` | توكن بوت التليجرام من `@BotFather` |
| **`TELEGRAM_BOT_USERNAME`** | `@Zizo00ZIZO_bot` | يوزرنيم البوت |
| **`TELEGRAM_CHAT_ID`** | `1124247595` | الـ ID الخاص بك لقصر البوت عليك فقط ومنع التطفل |

### 2. أمر التشغيل (Startup Command):
```bash
python main.py
```

---

## 💻 التشغيل المحلي على جهاز الكمبيوتر

1. انسخ ملف `.env.example` إلى `keys.txt` أو `.env` وضع مفاتيحك الخاصة.
2. ثبت المكتبات المطلوبة:
   ```bash
   pip install -r requirements.txt
   ```
3. للتشغيل المباشر:
   - لتشغيل البوت: شغل `تشغيل_بوت_تليجرام_للتفريغ.bat`
   - لتفريغ أحدث تسجيل على الكمبيوتر ونسخه للحافظة: شغل `تفريغ_أحدث_تسجيل_صوتي.bat`

---

## 📊 رخصة المشروع (License)
هذا المشروع مفتوح المصدر ومتاح تحت ترخيص [MIT License](LICENSE).
