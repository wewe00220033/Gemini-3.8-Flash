# -*- coding: utf-8 -*-
"""
إعدادات المنظومة الشاملة 360° (Central System Configuration)
جميع المتغيرات والثوابت والموديلات مجمعة هنا لسهولة التعديل والتخصيص في أي وقت.
"""

import os


def _load_dotenv(path):
    """
    يقرأ ملف .env بسيط (KEY=VALUE، يدعم التعليقات وعلامات الاقتباس) بدون مكتبات خارجية.
    لا يكتب فوق متغيرات البيئة الموجودة (لوحة الاستضافة لها الأولوية).
    """
    if not os.path.exists(path):
        return 0
    loaded = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].lstrip()
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
                    loaded += 1
    except Exception as e:
        print(f"⚠️ تعذر قراءة .env: {e}")
    return loaded


_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_load_dotenv(os.path.join(_SCRIPT_DIR, ".env"))


class Config:
    # ==============================================================
    # 1. قائمة الموديلات المعتمدة رسمياً (الثلاثي الفائز فقط - بدون Lite نهائياً)
    # ==============================================================
    # الترتيب = أولوية التجربة. القائمة الكاملة المسموح بها (بدون Lite نهائياً):
    _ALL_MODELS = [
        "gemini-3.6-flash",       # الأساسي — الأثبت في الاختبار الميداني (6–8 ث، 200 OK) (20 طلب/مشروع)
        "gemini-3.5-transcribe",  # الاحتياطي — محرك جوجل التخصصي للصوت، الأسرع (3–4 ث) (25 طلب/مشروع)
        "gemini-3.8-flash",       # معطّل (انظر DISABLED_MODELS) — 503/Timeout متكرر مع الصوت بتاريخ 2026-10-23
    ]
    # موديلات معطّلة مؤقتاً: لن تُجرَّب أبداً. لإعادة 3.8 احذفه من هنا فقط (سطر واحد).
    DISABLED_MODELS = {"gemini-3.8-flash"}

    # (حلقة عادية بدل comprehension لأن Python لا يرى متغيرات الكلاس داخل comprehension)
    MODELS = []
    for _m in _ALL_MODELS:
        if _m not in DISABLED_MODELS:
            MODELS.append(_m)
    del _m

    # الكوتا اليومية المجانية لكل موديل لكل مشروع Google (من الاختبار الميداني — عدّلها لو غيّرت Google)
    # ملاحظة: Google تحسب الكوتا لكل *مشروع* وليس لكل مفتاح — مفتاحان من نفس المشروع لا يضاعفان الرصيد.
    MODEL_DAILY_QUOTA = {
        "gemini-3.6-flash": 20,
        "gemini-3.5-transcribe": 25,
        "gemini-3.8-flash": 20,
    }

    # متى تتصفّر كوتا Google اليومية (منتصف الليل بتوقيت المحيط الهادئ) وبأي توقيت تُعرض الأوقات لك
    DAILY_RESET_TZ = os.environ.get("DAILY_RESET_TZ", "America/Los_Angeles")
    DISPLAY_TZ = os.environ.get("DISPLAY_TZ", "Africa/Cairo")

    # ==============================================================
    # 2. قواعد الذكاء الاصطناعي وهندسة الأوامر (System Rules)
    # ==============================================================
    SYSTEM_PROMPT = (
        "أنت محرك تفريغ صوتي محترف فائق الدقة والذكاء (Multimodal Speech-to-Text Engine).\n"
        "المهمة: تفريغ التسجيل الصوتي بدقة متناهية كلمة بكلمة إلى نص عربي سليم، مع الالتزام التام بالقواعد التالية:\n"
        "1. المصطلحات التقنية، والبرمجية، وأسماء الأدوات، والبروتوكولات، والشركات، والاختصارات تُكتب باللغة الإنجليزية بأحرفها اللاتينية الأصلية القياسية (مثل: GitHub, AI, Python, Gist, IP, API, DevOps, Docker, Linux, Windows, Groq, Telegram).\n"
        "   وكذلك أسماء الملفات والامتدادات والمتغيرات والمناطق الزمنية كما تُكتب في الكود (مثل: .env, keys.json, main.py, config.py, Africa/Cairo, America/Los_Angeles, PORT, SERVER_PORT).\n"
        "2. تفهم العامية المصرية بدقة تامة دون تحريف ودون اختلاق كلمات أو تخمين من عندك؛ انقل الكلام خام كما نُطق تماماً.\n"
        "3. اضبط الإملاء وعلامات الترقيم الأساسية دون إسقاط أي حرف أو كلمة.\n"
        "4. أخرج النص المفرغ فقط، بدون أي مقدمات، أو اعتذارات، أو تعليقات جانبية."
    )

    # (اختياري — مُعطّل افتراضياً) إرسال القواعد كـ system_instruction منفصل + temperature.
    # جرّبه بمفاتيحك: USE_SYSTEM_INSTRUCTION=1 و TEMPERATURE=0 ثم قارن الجودة. لو أسوأ ارجع للافتراضي.
    USE_SYSTEM_INSTRUCTION = os.environ.get("USE_SYSTEM_INSTRUCTION", "0") in ("1", "true", "True")
    _t = os.environ.get("TEMPERATURE", "").strip()
    TEMPERATURE = float(_t) if _t else None

    # ==============================================================
    # 3. إعدادات الشبكة والاستضافة (DevOps & Hosting)
    # ==============================================================
    SCRIPT_DIR = _SCRIPT_DIR
    # مجلد البيانات (keys.json). الافتراضي بجانب الكود. على الاستضافة يمكن توجيهه لمجلد
    # لا يُستبدل عند إعادة رفع ZIP (مثل DATA_DIR=/home/container/data).
    DATA_DIR = os.environ.get("DATA_DIR", "").strip() or SCRIPT_DIR
    KEYS_JSON_FILE = os.path.join(DATA_DIR, "keys.json")
    KEYS_TXT_FILE = os.path.join(DATA_DIR, "keys.txt")
    ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

    # خادم الويب (Web UI)
    # المنفذ: PORT (مضبوط يدوياً) ثم SERVER_PORT (تحقنه استضافات Pterodactyl مثل bot-hosting.net) ثم 8080
    WEB_HOST = os.environ.get("HOST", "0.0.0.0")
    WEB_PORT = int(os.environ.get("PORT") or os.environ.get("SERVER_PORT") or "8080")

    # توكن وصول اختياري لواجهة الويب (المنظومة شخصية — فاضي = مفتوح بالكامل).
    # لو ضبطته، الواجهة تطلبه مرة واحدة وتحفظه في المتصفح. localhost مفتوح دائماً.
    WEB_ACCESS_TOKEN = os.environ.get("WEB_ACCESS_TOKEN", "").strip()

    # الحد الأقصى لحجم الملف الصوتي المرفوع (Gemini inline_data حدها ~20MB)
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))

    # ------------------------------------------------------------------
    # مهلات Gemini (حادثة 01): المهلة الديناميكية تتناسب مع حجم الملف
    #   read_timeout = min(MAX, BASE + PER_MB × حجم_الملف_بالميجا)
    #   مثال: 770KB → ~21 ث ؛ 5MB → ~55 ث ؛ 20MB → 90 ث (السقف)
    # ------------------------------------------------------------------
    GEMINI_CONNECT_TIMEOUT = 10          # فتح الاتصال — لو فشل = مشكلة شبكة من السيرفر
    GEMINI_READ_TIMEOUT_BASE = 15
    GEMINI_READ_TIMEOUT_PER_MB = 8
    GEMINI_READ_TIMEOUT_MAX = 90         # (توافق) = سقف الملفات الصغيرة
    # سقفان حسب الحجم (04 §2.5b): صغير ≤ 8MB → 90ث (اكتشاف تعليق سريع)؛ كبير > 8MB → 180ث (15MB يحتاج 135ث فعلاً)
    GEMINI_READ_TIMEOUT_MAX_SMALL = int(os.environ.get("GEMINI_READ_TIMEOUT_MAX_SMALL", "90"))
    GEMINI_READ_TIMEOUT_MAX_LARGE = int(os.environ.get("GEMINI_READ_TIMEOUT_MAX_LARGE", "180"))
    GEMINI_LARGE_FILE_MB = 8

    # قاطع الدائرة على مستوى الموديل (بالثواني): لو موديل رجّع 5xx أو Timeout يُتخطّى للمدة دي
    # على كل المفاتيح (المشكلة في Google مش في المفتاح). 0 = معطّل. /reset يفتح القواطع يدوياً.
    MODEL_PAUSE_ON_TIMEOUT = 90
    MODEL_PAUSE_ON_5XX = 180
    MODEL_PAUSE_ON_404 = 3600

    # لو كل الموديلات وقعت في نفس اللحظة: جولات إضافية محدودة مع انتظار قصير بينها
    TRANSIENT_RETRY_ROUNDS = 2           # إجمالي 3 جولات كحد أقصى
    TRANSIENT_RETRY_BACKOFF = (2, 4)     # ثوانٍ قبل الجولة 2 ثم 3

    # (اختياري — فاضي افتراضياً) مستوى التفكير لكل موديل: MINIMAL | LOW | MEDIUM | HIGH
    # يُرسل فقط للموديلات المذكورة هنا. قرار المالك: نسيبه على الافتراضي (MEDIUM) لأعلى دقة في العامية.
    MODEL_THINKING_LEVEL = {}

    # مهلات تليجرام: طلبات قصيرة / تنزيل الملف الصوتي
    TELEGRAM_TIMEOUT = 30
    TELEGRAM_DOWNLOAD_TIMEOUT = 60

    # شفافية تليجرام أثناء التفريغ: تجديد typing (تليجرام تعرضه 5 ث فقط) + رسالة "⏳ لسه شغّال"
    # بعد PROGRESS_AFTER_SECONDS تُحذف تلقائياً عند وصول النتيجة. 0 = فوراً، رقم كبير = لا تُرسل.
    TYPING_REFRESH_SECONDS = 4
    PROGRESS_AFTER_SECONDS = 12
    # (للتوافق مع الكود القديم) أقصى مهلة طلب
    REQUEST_TIMEOUT = GEMINI_READ_TIMEOUT_MAX

    # فترة التهدئة عند 429 بالدقيقة (RPM) لو Google ما رجّعتش retryDelay. 429 اليومي يُقفل حتى تصفير Pacific.
    KEY_COOLDOWN_SECONDS = 60
    # مدة قفل المفتاح المرفوض من Google (400 API key not valid / 401 / 403)
    INVALID_KEY_COOLDOWN_SECONDS = 24 * 3600

    # تليجرام: وضع التنسيق وحد طول الرسالة (الحد الرسمي 4096 — نترك هامشاً للوسوم)
    TELEGRAM_MAX_MSG = 4000
    # عدد عمليات التفريغ المتوازية في البوت
    BOT_WORKERS = int(os.environ.get("BOT_WORKERS", "2"))
    # إشعار على تليجرام عند بدء التشغيل (يفيد لمعرفة إعادة تشغيل الاستضافة)
    NOTIFY_ON_START = os.environ.get("NOTIFY_ON_START", "1") not in ("0", "false", "False", "")
    # Watchdog (03b الطبقة 3): خيط مستقل لا يلمس الشبكة، يفحص كل 10ث (لا 30).
    WATCHDOG_INTERVAL = int(os.environ.get("WATCHDOG_INTERVAL", "10"))

    # ------------------------------------------------------------------
    # شبكة تليجرام والتعافي الذاتي (03b الطبقات 0.5–3) — المرحلة 1a
    # ------------------------------------------------------------------
    # مهلات tuples: (فتح الاتصال، القراءة). القراءة للـ long-poll = TG_POLL_TIMEOUT + TG_READ_MARGIN.
    TG_CONNECT_TIMEOUT = int(os.environ.get("TG_CONNECT_TIMEOUT", "10"))
    TG_POLL_TIMEOUT = int(os.environ.get("TG_POLL_TIMEOUT", "30"))
    TG_READ_MARGIN = int(os.environ.get("TG_READ_MARGIN", "10"))
    # وضع الحذر: بعد أي ReadTimeout يهبط long-poll إلى 10ث لمدة 5 دقائق ثم يعود (اكتشاف أسرع للتجمد).
    TG_POLL_TIMEOUT_CAUTIOUS = int(os.environ.get("TG_POLL_TIMEOUT_CAUTIOUS", "10"))
    CAUTIOUS_MODE_MINUTES = int(os.environ.get("CAUTIOUS_MODE_MINUTES", "5"))
    # TCP keepalive على socket الـ poller (يكشف half-open خلال ~50ث؛ Linux فقط، يُتجاهل بأمان على غيره)
    TCP_KEEPALIVE = os.environ.get("TCP_KEEPALIVE", "1") not in ("0", "false", "False", "")
    # backoff أُسّي لأخطاء الشبكة المتتالية (DNS/ConnectionError) بدل 3ث ثابتة
    NET_BACKOFF = (3, 6, 12, 30)
    # stall للـ poller = poll + margin + STALL_MARGIN (≈55ث عادي، ≈35ث في وضع الحذر)
    STALL_MARGIN = int(os.environ.get("STALL_MARGIN", "15"))
    # سلم التصعيد: L3 وضع الحماية بعد N إعادة poller خلال نافذة؛ L3b short-poll طوارئ دقيقتين
    PROTECT_AFTER_RESTARTS = int(os.environ.get("PROTECT_AFTER_RESTARTS", "3"))
    PROTECT_WINDOW_MINUTES = int(os.environ.get("PROTECT_WINDOW_MINUTES", "10"))
    EMERGENCY_SHORT_POLL_SECONDS = int(os.environ.get("EMERGENCY_SHORT_POLL_SECONDS", "120"))
    EMERGENCY_SHORT_POLL_INTERVAL = 2
    # إيقاف رشيق عند SIGTERM (Pterodactyl): انتظار المهام الجارية ≤ هذه الثواني ثم خروج 0
    SHUTDOWN_GRACE = int(os.environ.get("SHUTDOWN_GRACE", "20"))

    # --- الطبقة 4 (1c): Supervisor/Worker اختياري [قرار-مالك ج3] — SUPERVISED=0 افتراضياً ---
    SUPERVISED = os.environ.get("SUPERVISED", "0").strip() in ("1", "true", "True")
    SUPERVISOR_BACKOFF = (5, 10, 20, 60)            # ثوانٍ بين إعادات الإطلاق بعد crash متتالٍ
    SUPERVISOR_MAX_PER_HOUR = int(os.environ.get("SUPERVISOR_MAX_PER_HOUR", "20"))
    SUPERVISOR_BEAT_MAX = int(os.environ.get("SUPERVISOR_BEAT_MAX", "60"))      # ملف النبض أقدم من هذا = عامل متجمد
    WORKER_BEAT_INTERVAL = int(os.environ.get("WORKER_BEAT_INTERVAL", "5"))   # العامل يلمس state/worker.beat كل 5ث (المشرف يقرأ نفس المتغير)
    WORKER_BEAT_FILE = os.path.join(DATA_DIR, "state", "worker.beat")
    RESTART_REASON_FILE = os.path.join(DATA_DIR, "state", "restart_reason.json")
    EXIT_CODE_RESTART = 3                           # L4: "أعد تشغيلي" (المشرف يعيد فوراً بلا backoff)
    EXIT_AFTER_RESTARTS = int(os.environ.get("EXIT_AFTER_RESTARTS", "6"))      # L4: 6 × L1 خلال النافذة
    EXIT_WINDOW_MINUTES = int(os.environ.get("EXIT_WINDOW_MINUTES", "15"))
    L4_STALL_SECONDS = int(os.environ.get("L4_STALL_SECONDS", "300"))          # أو stall > 5 د رغم L1

    # ------------------------------------------------------------------
    # طابور الفويسات + سجل المهام (03 §2.3/§3، 19 §2) — المرحلة 1b
    # ------------------------------------------------------------------
    # عامل تفريغ واحد = FIFO حقيقي (رفعه لـ 2 يكسر الترتيب ويضاعف ذروة الذاكرة)
    TRANSCRIBE_WORKERS = int(os.environ.get("TRANSCRIBE_WORKERS", "1"))
    QUEUE_MAX = int(os.environ.get("QUEUE_MAX", "50"))
    # [قرار-مالك] المؤجَّل يحجب ما بعده (سياق الفويسات المترابطة)؛ 1 = يتخطى المؤجل ويكمل الجاهز
    QUEUE_SKIP_DEFERRED = os.environ.get("QUEUE_SKIP_DEFERRED", "0") not in ("0", "false", "False", "")
    # فاصل RPM بين مهمتين متتاليتين (لا نضرب Gemini بدفعة 5 فويسات في ثانية واحدة)
    QUEUE_PACING_SEC = float(os.environ.get("QUEUE_PACING_SEC", "2"))
    # إعادة تحميل الملف من تليجرام (فشل التحميل ≠ فشل التفريغ)
    DOWNLOAD_RETRIES = 3
    DOWNLOAD_BACKOFF = (2, 5, 10)
    # قبول الفيديو/video_note للتفريغ (N3) — حد getFile في Bot API = 20MB
    ACCEPT_VIDEO = os.environ.get("ACCEPT_VIDEO", "1") not in ("0", "false", "False", "")
    MAX_FILE_MB = 20
    # سجل المهام (سطر لكل حدث، fsync) — استئناف pending بعد restart (P0) + idempotency بـ file_unique_id
    JOBS_FILE = os.path.join(DATA_DIR, "state", "jobs.ndjson")
    JOBS_RESUME_MAX_AGE_H = int(os.environ.get("JOBS_RESUME_MAX_AGE_H", "20"))   # تليجرام يحتفظ بـ file_id ساعات
    JOBS_DEDUP_HOURS = 24
    JOBS_COMPACT_KEEP = 2000   # عند تجاوز ضعفه يُضغط الملف لآخر هذا العدد من المهام
    # ريأكشن فوري على الفويس (Bot API 7.0+، من القائمة المسموحة فقط؛ ⏳/✅ غير مسموحين)
    REACTION_RECEIVED = "\U0001F440"   # 👀
    REACTION_ENABLED = os.environ.get("REACTION_ENABLED", "1") not in ("0", "false", "False", "")
    # /health العميق (G26، 03b §3): يرجع 503 لو أي خيط حيوي لم ينبض خلال هذه المدة.
    # يُحسب افتراضياً من مهلة polling + هامش القراءة + 15 (≈55ث) — لا 90 ولا 120.
    HEALTH_STALE_AFTER = int(os.environ.get("HEALTH_STALE_AFTER", "55"))
    # الخيوط التي يراقبها /health (تُسجَّل عند بدء كل خيط عبر heartbeat.beat). فارغة = لا فحص stale.
    HEALTH_WATCH_THREADS = tuple(t for t in os.environ.get("HEALTH_WATCH_THREADS", "poller").split(",") if t.strip())
    # مستوى السجلات: DEBUG | INFO | WARNING
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # المستخدم المصرح له في تليجرام (حماية البوت من التطفل).
    # فاضي = البوت يرفض الجميع حتى تضبط TELEGRAM_CHAT_ID في البيئة أو .env
    DEFAULT_CHAT_ID = ""

    # مسار تسجيلات الصوت الافتراضي للكمبيوتر
    DEFAULT_RECORDINGS_DIR = r"C:\Users\USER\Documents\تسجيلات الصوت"
