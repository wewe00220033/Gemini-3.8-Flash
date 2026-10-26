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
    MODEL_PAUSE_ON_5XX = 180             # يبقى للتوافق = أعلى درجة في السلم أدناه
    MODEL_PAUSE_ON_404 = 3600

    # --- Failover v2 (المرحلة 2 — 04 §2) ---
    # قفل متدرج لـ 5xx: الضربة 1 → 20ث، 2 → 45ث، 3 → 90ث، 4+ → 180ث؛ العداد يُصفَّر عند نجاح الموديل
    MODEL_PAUSE_LADDER_5XX = (20, 45, 90, 180)
    # 503 على مفتاح: جرّب حتى N مفتاح آخر من مجموعة مشروع مختلفة قبل قفل الموديل (المالك: 3 أو 5؛ 2 افتراضياً — كل قفزة تحرق طلباً)
    MODEL_503_KEY_HOPS = int(os.environ.get("MODEL_503_KEY_HOPS", "2"))
    # ميزانية زمنية كلية للمحاولة الواحدة؛ بعدها → تأجيل في الطابور (DeferredError) بدل الدوران
    RETRY_BUDGET_SECONDS = int(os.environ.get("RETRY_BUDGET_SECONDS", "45"))
    # حفظ القاطع على القرص حتى لا نضرب موديلاً نعلم أنه مقفول بعد إعادة التشغيل (04 §2.5)
    CIRCUIT_FILE = os.path.join(DATA_DIR, "state", "circuit.json")
    # مجموعات المشاريع: project_group صريح → وسم project → كل مفتاح مجموعة مستقلة؛ دمج تلقائي عند 429 يومي متزامن
    KEY_GROUP_AUTODETECT = os.environ.get("KEY_GROUP_AUTODETECT", "1") not in ("0", "false", "False", "")
    KEY_GROUP_AUTODETECT_WINDOW = 60     # ثانية: مفتاحان نفد يومياً على نفس الموديل خلالها = مشروع واحد
    KEY_GROUPS_FILE = os.path.join(DATA_DIR, "state", "key_groups.json")
    # أوضاع التدهور حسب المتبقي اليومي (04 §3.2): normal > 30% ≥ saver > 0 = freeze
    QUOTA_SAVER_RATIO = 0.30

    # ==============================================================
    # المرحلة 3 — خط الإنتاج: الأرشفة + القاموس + الأدوار + الرسالتان (مواصفات 05/06/07/08)
    # ==============================================================
    TRANSCRIPTS_DIR = os.path.join(DATA_DIR, "transcripts")          # ALL.md (append-only)
    GLOSSARY_FILE = os.path.join(DATA_DIR, "glossary.json")           # القاموس المخصص (بلا أسرار → يدخل /backup)
    ROLES_FILE = os.path.join(DATA_DIR, "roles.json")                 # الأدوار المخصصة (الافتراضية في الكود محمية)
    SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")           # delivery_mode / role / header_target
    # البانر (06 §4): يُنسخ مع النص ليفهم الوكيل أنه تفريغ آلي
    TRANSCRIPT_HEADER_ENABLED = os.environ.get("TRANSCRIPT_HEADER_ENABLED", "1") not in ("0", "false", "False", "")
    TRANSCRIPT_HEADER_TARGET = os.environ.get("TRANSCRIPT_HEADER_TARGET", "both")   # both | raw_only | polished_only
    TRANSCRIBE_LAYER_A_MAX_CHARS = 600     # 06 §8: طبقة A (التفريغ) قصيرة وثابتة دائماً — اختبار يحرسها
    # الرسالتان (07 §1): dual (افتراضي) | raw_only | polished_only — محفوظ في settings.json
    DELIVERY_MODE_DEFAULT = os.environ.get("DELIVERY_MODE", "dual")
    DEFAULT_ROLE = os.environ.get("DEFAULT_ROLE", "general")
    # المساعد النصي (02 §1 عمود 3): Lite مسموح **نصياً فقط** (تنظيم/تسمية/أدوار) — ممنوع للتفريغ (اختبار يمنع التقاطع)
    TEXT_ASSIST_MODEL = os.environ.get("TEXT_ASSIST_MODEL", "gemini-3.5-flash-lite")
    TEXT_ASSIST_THINKING_BUDGET = int(os.environ.get("TEXT_ASSIST_THINKING_BUDGET", "2048"))   # أقصى تفكير (قرار المالك)
    TEXT_ASSIST_TIMEOUT = int(os.environ.get("TEXT_ASSIST_TIMEOUT", "40"))
    TEXT_ASSIST_DAILY_QUOTA = 500
    POLISH_FILE_THRESHOLD = 12000          # 07 §4: نص > ~3 رسائل → يُرسل كملف .md + معاينة
    PANEL_PAGE_SIZE = 5

    # ==============================================================
    # المرحلة 4 — الجلسات والتسمية والسياق (مواصفتا 08 و10)
    # ==============================================================
    SESSIONS_DIR = os.path.join(DATA_DIR, "sessions")                 # sessions_index.json + session_<uuid8>.md + current_session.json
    SESSIONS_INDEX_FILE = os.path.join(SESSIONS_DIR, "sessions_index.json")
    CURRENT_SESSION_FILE = os.path.join(SESSIONS_DIR, "current_session.json")
    SESSION_NAME_MAX = 60                  # 08 §3: تنقية الاسم + حد 60 حرفاً (الملف يبقى session_<uuid8>.md)
    SESSION_EXPORT_ZIP_MB = 50             # 08 §2: /export يرسل .zip لو الملف > 50MB
    SMART_TITLE_ENABLED = os.environ.get("SMART_TITLE_ENABLED", "1") not in ("0", "false", "False", "")
    SMART_TITLE_WORDS = 100                # 08 §2: الاقتراح من أول 100 كلمة عبر flash-lite (نصياً)
    # السياق (10 §2): L1 = آخر N فويسات خام من الجلسة النشطة، مقتطعة إلى CONTEXT_MAX_CHARS — داخل طبقة A فقط
    CONTEXT_ENABLED = os.environ.get("CONTEXT_ENABLED", "1") not in ("0", "false", "False", "")
    CONTEXT_LAST_N = 2
    CONTEXT_MAX_CHARS = 800
    CONTEXT_TERMS_MAX = 20                 # L0: الأسماء التقنية التي ظهرت في الجلسة (محلياً بلا AI)
    CONTEXT_ECHO_RATIO = 0.60              # 10 §3: ≥60% من الناتج موجود حرفياً في L1 → "صدى السياق" = strike
    CONTEXT_STRIKES_TO_SIMPLE = 2          # بعد ضربتين → المحاولة التالية بسيطة (بلا L1)
    SIMPLE_MODE_COOLDOWN = 3               # 10 §3: بعد الوصول لضربتين تبدأ الفويسات التالية بسيطة لـ 3 فويسات
    # تكميلي المرحلة 4 (08 §2 / 16 D27): بطاقة الاستئناف تعرض آخر 3 نصوص بلا LLM — RESUME_RECAP=0 لإخفائها
    RESUME_RECAP = os.environ.get("RESUME_RECAP", "1") not in ("0", "false", "False", "")
    TITLE_SUGGESTIONS_N = 3                # 08 §2: «🎲 3 اقتراحات» عند الطلب فقط (3 نداءات flash-lite نصية)
    # 10 §2 L2: ملخص الجلسة التراكمي يُحدَّث كل N فويسات عبر flash-lite ويدخل طبقة B (التنظيم) فقط — أبداً في طبقة A
    SESSION_SUMMARY_EVERY = 5
    SESSION_SUMMARY_MAX_CHARS = 600

    # لو كل الموديلات وقعت في نفس اللحظة: جولات إضافية محدودة مع انتظار ذكي (min(أقرب فتح, الميزانية))
    TRANSIENT_RETRY_ROUNDS = 2           # إجمالي 3 جولات كحد أقصى
    TRANSIENT_RETRY_BACKOFF = (2, 4)     # حد أدنى للانتظار بين الجولات

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

    # --- المرحلة 5 (09 §1–3): سجل CCTV بملفات يومية + حجب الأسرار + احتفاظ دوّار ---
    LOGS_DIR = os.path.join(DATA_DIR, "logs")
    LOG_TO_FILE = os.environ.get("LOG_TO_FILE", "1") not in ("0", "false", "False", "")
    LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "90"))     # 09 §1: أحدث قرار (90) يغلب
    LOG_TOTAL_MAX_MB = int(os.environ.get("LOG_TOTAL_MAX_MB", "500"))         # السقف الكلي — الأقدم يُحذف أولاً
    LOG_FILE_MAX_MB = 20                                                      # 09 §2: تدوير داخل اليوم عند 20MB
    LOG_TAIL_DEFAULT = 20                                                     # /logs N (افتراضي)
    LOG_ERRORS_TAIL = 50                                                      # /logs errors
    # --- المرحلة 5 (12 §3): الطبيب الذاتي + تقارير الحوادث ---
    INCIDENTS_DIR = os.path.join(DATA_DIR, "incidents")
    INCIDENTS_KEEP = 50                                                       # أقدم التقارير تُحذف بعد هذا العدد
    INCIDENT_FAIL_STREAK = 3                                                  # ≥3 فشل متتالٍ → تقرير
    INCIDENT_COOLDOWN_S = 600                                                 # لا تقريرين لنفس النوع خلال 10 د
    DOCTOR_INTERVAL_MIN = int(os.environ.get("DOCTOR_INTERVAL_MIN", "60"))    # فحص دوري صامت (0 = معطّل)
    DISK_LOW_MB = 200                                                         # ⚠️ لو المساحة الحرة أقل
    PERSIST_PROBE_FILE = os.path.join(DATA_DIR, ".persist_probe")             # 09 §6
    # --- المرحلة 5.1 (09 §2 قناة audit + 09 §5 النسخ التلقائي + 12 §3.1 getMe) ---
    AUDIT_FILE = os.path.join(LOGS_DIR, "audit.log")                          # من فعل ماذا متى — لا يُدوَّر (صغير)
    BACKUPS_DIR = os.path.join(DATA_DIR, "backups")                           # النسخ المحلية (آخر BACKUPS_KEEP فقط)
    BACKUPS_KEEP = 4
    BACKUP_WEEKLY_ENABLED = os.environ.get("BACKUP_WEEKLY_ENABLED", "1") not in ("0", "false", "False", "")
    BACKUP_WEEKLY_DAY = os.environ.get("BACKUP_WEEKLY_DAY", "fri")            # mon..sun أو 0..6 أو اسم عربي (الجمعة)
    BACKUP_HOUR = int(os.environ.get("BACKUP_HOUR", "3"))                     # ساعة التنفيذ (توقيت الخادم المحلي)
    BACKUP_SPLIT_MB = 50                                                      # > 50MB → backup_part1.zip… (حد sendDocument)
    BACKUP_STATE_FILE = os.path.join(DATA_DIR, "state", "backup_state.json")  # آخر نسخة تلقائية
    DOCTOR_TG_TIMEOUT = 3                                                     # مهلة getMe في /doctor (ثوانٍ)
    DOCTOR_TG_SLOW_MS = 1500                                                  # ⚠️ لو الاستجابة أبطأ

    # المستخدم المصرح له في تليجرام (حماية البوت من التطفل).
    # فاضي = البوت يرفض الجميع حتى تضبط TELEGRAM_CHAT_ID في البيئة أو .env
    DEFAULT_CHAT_ID = ""

    # مسار تسجيلات الصوت الافتراضي للكمبيوتر
    DEFAULT_RECORDINGS_DIR = r"C:\Users\USER\Documents\تسجيلات الصوت"
