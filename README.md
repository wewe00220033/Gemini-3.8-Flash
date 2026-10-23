# 🎙️ منظومة التفريغ الصوتي الذكي 360° - Gemini Multi-Model AI

> **منظومة هندسية متكاملة (DevOps Resilient Architecture) لتفريغ الصوت بدقة خيالية، تدعم العامية المصرية، تحافظ على المصطلحات التقنية بالإنجليزية، وتعمل كـ بوت تليجرام وواجهة ويب صوتية حقيقية وخادم محلي في نفس الوقت 24/7 وبصفر تكلفة!**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Google Gemini API](https://img.shields.io/badge/Gemini-3.8%20%7C%203.6%20%7C%203.5-orange.svg)](https://aistudio.google.com/)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot%20Ready-blue.svg)](https://telegram.org/)
[![Web UI](https://img.shields.io/badge/Web%20UI-Real%20Audio-green.svg)](http://localhost:8080)
[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

---

## 🌟 مميزات المنظومة 360°

1. **دقة سمعية فائقة (Native Multimodal Audio):**
   - يفهم نبرة الصوت والعامية المصرية بدون اختلاق أو تخمين زائف.
   - يكتب المصطلحات البرمجية والتقنية بأحرفها الإنجليزية القياسية (`GitHub, AI, Python, Gist, IP, API, DevOps, Docker`).
2. **الثلاثي الفائز المعتمد فقط (Strict Top 3 Models - لا وجود لـ Lite نهائياً):**
   - 🥇 **`gemini-3.8-flash`:** الموديل الأذكى عالمياً في فهم السياق والنبرة (20 طلب/مشروع).
   - 🥈 **`gemini-3.6-flash`:** التوأم فائق السرعة بنفس الدقة (20 طلب/مشروع).
   - 🥉 **`gemini-3.5-transcribe`:** محرك جوجل التخصصي للتفريغ الصوتي المباشر (25 طلب/مشروع).
   - 🚫 **موديل Lite مستبعد ومحذوف نهائياً** لضمان أعلى جودة بدون تشويه.
3. **مصفوفة المفاتيح الذكية (`keys.json` & Auto-Cooldown):**
   - إدارة ديناميكية لحالة كل مفتاح (`active` أو `exhausted`).
   - عند وصول أي مفتاح لكود 429، يتم وضعه في فترة تهدئة (Cooldown) تلقائياً، والتحويل الفوري للمفتاح التالي بدون أي توقف.
   - يعاد تنشيط المفتاح تلقائياً بمجرد انتهاء فترة التهدئة.
4. **كشف المفاتيح التلقائي في الشات (Auto-Key Detection):**
   - ابعت أي مفتاح Gemini جديد في شات التليجرام (مع اسم مشروع أو بدونه)، البوت يكتشفه ويسجله في `keys.json` فوراً ويزيد رصيدك 65 ريكورد يومياً!
5. **واجهتان متزامنتان في تطبيق واحد (Dual Interface):**
   - 📱 **Telegram Bot:** تفريغ الفويس نوت بكود بلوك يُنسخ بلمسة واحدة + كشف المفاتيح + أمر `/status`.
   - 🌐 **Web UI:** واجهة ويب عصرية بها تسجيل صوتي حقيقي (MediaRecorder Blob مثل تليجرام تماماً) + سحب وإفلات للملفات + إدارة المفاتيح.
   - 💻 **Desktop Tool:** أداة لتفريغ أحدث تسجيل والنسخ التلقائي للحافظة (Clipboard).

---

## 📐 الهيكلية الهندسية للمنظومة (DevOps Architecture)

```mermaid
graph TD
    UserVoice[🎙️ فويس نوت من تليجرام أو تسجيل ويب حقيقي أو ملف] --> Engine[⚙️ Master Transcriber Engine]
    Engine --> KeyPool[(🔑 Dynamic Key Pool - keys.json)]
    KeyPool --> Model1{1. تجربة Gemini 3.8 Flash}
    Model1 -->|200 OK| Done[✅ إخراج النص + نسخ للحافظة]
    Model1 -->|429 Quota| Model2{2. تجربة Gemini 3.6 Flash}
    Model2 -->|200 OK| Done
    Model2 -->|429 Quota| Model3{3. تجربة Gemini 3.5 Transcribe}
    Model3 -->|200 OK| Done
    Model3 -->|الكل مستهلك| Rotate[🔄 التبديل التلقائي للمفتاح التالي في keys.json]
    Rotate --> Model1
```

---

## 🚀 دليل النشر على خوادم الاستضافة (Bot-Hosting / Cloud)

المنظومة مصممة لتعمل بـ **Python Standard Library (Pure Requests)** بدون تعقيدات أو أطر عمل ثقيلة.

### 1. ضبط متغيرات البيئة (Hosting Variables):
في لوحة تحكم الاستضافة، أضف المتغيرات التالية:

| المتغير (Variable) | القيمة | الوصف |
| :--- | :--- | :--- |
| **`GEMINI_API_KEYS`** | `AQ...Key1,AQ...Key2` | مفاتيح Google AI Studio مفصولة بفواصل |
| **`TELEGRAM_BOT_TOKEN`** | `123456789:ABCdef...` | توكن البوت من `@BotFather` |
| **`TELEGRAM_BOT_USERNAME`** | `@YourBot` | يوزرنيم البوت |
| **`TELEGRAM_CHAT_ID`** | `123456789` | **مطلوب** — لحصر البوت عليك فقط (فاضي = يرفض الجميع) |
| **`PORT`** | `8080` | منفذ واجهة الويب (أو `SERVER_PORT` تلقائياً على bot-hosting.net) |
| **`WEB_ACCESS_TOKEN`** | *(اختياري)* | توكن لواجهة الويب — فاضي = مفتوح |

### 2. أمر التشغيل:
```bash
python main.py
```
*(يقوم `main.py` بتشغيل واجهة الويب في خيط خلفي وبوت التليجرام في نفس الوقت).*

---

## 💬 أوامر بوت التليجرام

- **إرسال أي فويس نوت:** تفريغ فوري داخل كود بلوك بضغطة زر.
- **إرسال مفتاح Gemini (مثل `AQ...` أو `مشروع 3: AQ...`):** يكتشفه ويضيفه لمصفوفة المفاتيح تلقائياً.
- **`/status` أو `حالة`:** يعرض تقرير شامل بالمفاتيح النشطة، فترات التهدئة، والرصيد المتاح.
- **`/help`:** دليل الاستخدام السريع.

---

## 📊 معادلة الرصيد اليومي الفائق

$$\text{إجمالي الريكوردات اليومية} = \text{عدد المفاتيح} \times 65$$

- **مشروعين:** 130 ريكورد فائق الذكاء يومياً مجاناً.
- **5 مشاريع:** 325 ريكورد يومياً مجاناً.
- **10 مشاريع:** 650 ريكورد يومياً مجاناً.
