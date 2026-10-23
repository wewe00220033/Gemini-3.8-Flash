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
    MODELS = [
        "gemini-3.8-flash",       # ملك الجودة وفهم السياق والنبرة (20 طلب/مشروع)
        "gemini-3.6-flash",       # التوأم فائق السرعة بنفس الدقة (20 طلب/مشروع)
        "gemini-3.5-transcribe",  # محرك جوجل التخصصي للتفريغ الصوتي (25 طلب/مشروع)
    ]

    # الكوتا اليومية المجانية لكل موديل لكل مفتاح (من الاختبار الميداني — عدّلها لو غيّرت Google)
    MODEL_DAILY_QUOTA = {
        "gemini-3.8-flash": 20,
        "gemini-3.6-flash": 20,
        "gemini-3.5-transcribe": 25,
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
        "2. تفهم العامية المصرية بدقة تامة دون تحريف ودون اختلاق كلمات أو تخمين من عندك؛ انقل الكلام خام كما نُطق تماماً.\n"
        "3. اضبط الإملاء وعلامات الترقيم الأساسية دون إسقاط أي حرف أو كلمة.\n"
        "4. أخرج النص المفرغ فقط، بدون أي مقدمات، أو اعتذارات، أو تعليقات جانبية."
    )

    # ==============================================================
    # 3. إعدادات الشبكة والاستضافة (DevOps & Hosting)
    # ==============================================================
    SCRIPT_DIR = _SCRIPT_DIR
    KEYS_JSON_FILE = os.path.join(SCRIPT_DIR, "keys.json")
    KEYS_TXT_FILE = os.path.join(SCRIPT_DIR, "keys.txt")
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

    # المهلة الزمنية لطلبات الشبكة (Gemini) ولطلبات تليجرام القصيرة
    REQUEST_TIMEOUT = 60
    TELEGRAM_TIMEOUT = 30

    # فترة التهدئة الافتراضية عند 429 (بالثواني). لو Google رجّعت retryDelay في الرد يُستخدم بدلاً منها.
    KEY_COOLDOWN_SECONDS = 3600

    # تليجرام: وضع التنسيق وحد طول الرسالة (الحد الرسمي 4096 — نترك هامشاً للوسوم)
    TELEGRAM_MAX_MSG = 4000
    # عدد عمليات التفريغ المتوازية في البوت
    BOT_WORKERS = int(os.environ.get("BOT_WORKERS", "2"))
    # إشعار على تليجرام عند بدء التشغيل (يفيد لمعرفة إعادة تشغيل الاستضافة)
    NOTIFY_ON_START = os.environ.get("NOTIFY_ON_START", "1") not in ("0", "false", "False", "")
    # Watchdog: كل كم ثانية يفحص أن البوت والويب أحياء ويعيد تشغيلهما
    WATCHDOG_INTERVAL = 30

    # المستخدم المصرح له في تليجرام (حماية البوت من التطفل).
    # فاضي = البوت يرفض الجميع حتى تضبط TELEGRAM_CHAT_ID في البيئة أو .env
    DEFAULT_CHAT_ID = ""

    # مسار تسجيلات الصوت الافتراضي للكمبيوتر
    DEFAULT_RECORDINGS_DIR = r"C:\Users\USER\Documents\تسجيلات الصوت"
