# 🛠️ تقرير DevOps 360° — التشغيل على bot-hosting.net والمحلي بنفس الكود

> **السؤال:** "لو ده شغال 24/7 على الاستضافة، إيه اللي ممكن يقفل في وشنا؟"
> **المصادر:** الكود + دليل bot-hosting.net الرسمي + وثائق Telegram Bot API. لم أدخل لوحة السيرفر (Discord login).
> **سياق المالك:** المنظومة شخصية له وحده → الأولوية للـ Resilience.

## 1. خريطة المخاطر — 28 سيناريو

| الزاوية | السيناريو | الكود الحالي | البند |
|---|---|---|---|
| **🔼 الإقلاع** | `python main.py` + `requirements.txt` | ✅ | — |
| | المنفذ `SERVER_PORT` بدل `PORT` | ⚠️ `PORT` فقط | 1.8 |
| | المنفذ مشغول → الويب يقع والمنظومة تكمل بصمت | ⚠️ | 2.12 |
| | env جديدة مع وجود `keys.json` | ❌ تُتجاهل | 1.7 |
| | لا توكن تليجرام → ويب فقط | ✅ | — |
| **🔄 التشغيل** | انقطاع شبكة في `getUpdates` | ✅ | — |
| | انقطاع أثناء تحميل ملف من تليجرام | ❌ لا timeout | 2.4 |
| | Gemini 500/503 | ⚠️ مقبول | — |
| | 429 على موديل واحد | ❌ يقفل المفتاح لكل الموديلات | 2.8 |
| | كل المفاتيح في cooldown | ❌ يجدد القفل للأبد | 2.7 |
| | رسالتان متتاليتان | ⚠️ تسلسلي | 2.10 |
| | نسختان بنفس التوكن | ❌ 409 يُبتلع | 2.5 |
| **📤 المخرجات** | نص فيه `` ` `` / `*` / `_` | ❌ **تضيع بصمت** | 2.11 |
| | نص > 4096 | ❌ **تضيع بصمت** | 2.11 |
| | `document` مش صوت | ❌ يفشل بغموض | 2.6 |
| **💾 البيانات** | إعادة رفع ZIP | ❌ `keys.json` قد يُستبدل | 3.11 |
| | كتابتان متزامنتان | ❌ تلف → ضياع المفاتيح المضافة من الشات | 1.3 |
| | إعادة تشغيل الاستضافة | ✅ | — |
| **📊 المراقبة** | البوت واقع — إزاي أعرف؟ | ❌ | 2.12, 2.13 |
| | كم باقي من الكوتا؟ | ❌ تقديري | 2.15 |
| | logs بـ timestamps | ❌ | 3.1 |
| **🔐 الأمان** | الويب مفتوح | ⚠️ بقرار المالك — توكن اختياري | 1.5 |
| | `keys.json` 644 | ❌ | 1.3 |
| | مفتاح في URL | ❌ | 1.2 |
| | مفتاح وهمي يدخل الـ Pool | ❌ | 1.1 |
| **📱 المنصات** | Safari/iOS | ❌ mimeType ثابت | 3.9 |
| | Windows `.bat` | ✅ | — |
| | Linux/Mac | ⚠️ لا `start.sh` | 3.8 |

## 2. المعمارية بعد التنفيذ

```mermaid
graph TD
    subgraph Process["python main.py"]
        MAIN[main.py: logging + Startup Ping] --> WD[Watchdog]
        WD --> WEB[ThreadingHTTPServer: / /api/* /health]
        WD --> BOT[Telegram Long-Poll + ThreadPool + HTML safe send]
        WEB --> ENGINE
        BOT --> ENGINE[transcriber: 3.8→3.6→3.5 لكل مفتاح, header auth]
        ENGINE --> KM[(KeyManager: Lock + atomic save + per-(key,model) cooldown + daily_usage)]
        KM --> JSON[DATA_DIR/keys.json chmod 600]
    end
    ENV[متغيرات البيئة] -->|merge عند كل إقلاع| KM
    UPTIME[UptimeRobot اختياري] -->|/health| WEB
    BOT -->|Startup Ping| OWNER[📱 شاتك]
```

## 3. النشر على bot-hosting.net (مسودة `DEPLOY.md`)
1. Create Server → Python → الخطة المجانية.
2. Files → ZIP **بدون** `keys.json`/`.env`/`__pycache__` → Unarchive → نقل للجذر.
3. Startup → ملف البدء `main.py` → `requirements.txt`.
4. Variables: `GEMINI_API_KEYS`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`، اختيارياً `WEB_ACCESS_TOKEN`. المنفذ يُلتقط تلقائياً.
5. Start → رابط الويب في الكونسول + رسالة "🚀 شغال" على تليجرام.
6. اختياري: UptimeRobot على `/health`.

**محلياً:** `.bat` (Windows) / `start.sh` (Linux/Mac) / `.env` بعد 3.3.

## 4. مطلوب منك
- تجربة بعد كل مرحلة بمفاتيحك الحقيقية (ما عنديش مفاتيح).
- (اختياري) حد الذاكرة في الخطة المجانية لضبط `MAX_UPLOAD_MB`.

## 5. تذكير أمني
- 🔴 **ألغِ توكن GitHub `ghp_…` بعد انتهاء العمل** — اتكتب في الشات مرتين.
- بعد التنفيذ: لا ترفع `keys.json` أبداً.
