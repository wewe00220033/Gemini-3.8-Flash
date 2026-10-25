# -*- coding: utf-8 -*-
"""
نقطة الانطلاق الرئيسية الموحدة 360° (Master Unified Runner)
- يشغل خادم الويب والواجهة الصوتية في خيط خلفي (Web Service)
- يشغل بوت التليجرام في خيط خلفي (Telegram Bot)
- Runtime: poller قابل لإعادة التشغيل (L1) + pool تفريغ مشترك + watchdog مستقل لا يلمس الشبكة (03b)
- SIGTERM/SIGINT → إيقاف رشيق: انتظار المهام الجارية ≤ SHUTDOWN_GRACE ثم خروج 0 (Pterodactyl Stop/Restart)
- الخيط الرئيسي خامل: يراقب المراقب (watchdog) والويب فقط
- Startup Ping: رسالة على تليجرام عند الإقلاع (تعرف لو الاستضافة عملت Restart)
- مهيأ للعمل محلياً أو على خوادم الاستضافة (24/7 Hosting)
"""

import sys
import time
import signal
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from config import Config
from key_manager import key_manager
import web_service
import telegram_bot
import preflight
import watchdog
from model_state import model_circuit


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


class Runtime:
    """
    يملك خيوط النظام: poller (قابل لإعادة التشغيل L1)، pool التفريغ المشترك، watchdog، notifier.
    - `restart_poller()`: يضبط stop_event القديم (الـ poller القديم يخرج عند أول فرصة ويقتل Session خيطه)،
      ثم يطلق خيطاً جديداً بـ stop_event جديد. الـ pool لا يُمس — المهام الجارية تكمل.
    - `shutdown()`: إيقاف رشيق (03b الطبقة 0.5): stop_event → انتظار in_flight ≤ SHUTDOWN_GRACE → خروج 0.
    """

    def __init__(self, bot_token, chat_id):
        self.bot_token, self.chat_id = bot_token, chat_id
        self.pool = ThreadPoolExecutor(max_workers=max(1, Config.BOT_WORKERS), thread_name_prefix="transcribe")
        self.poller_stop = None
        self.poller_thread = None
        self.global_stop = threading.Event()
        self.notifier = None
        self.watchdog = None
        self.watchdog_restarts = 0
        self._lock = threading.Lock()

    # ---- poller ----
    def start_poller(self):
        with self._lock:
            self.poller_stop = threading.Event()
            t = threading.Thread(target=telegram_bot.main, kwargs={"stop_event": self.poller_stop, "pool": self.pool},
                                 daemon=True, name=f"telegram-bot-{telegram_bot.STATE['poller_generation'] + 1}")
            t.start()
            self.poller_thread = t
            return t

    def restart_poller(self):
        """L1: يُنادى من الـ watchdog (لا شبكة هنا؛ إنشاء خيط فقط)."""
        if self.global_stop.is_set():
            return False
        old_stop = self.poller_stop
        if old_stop is not None:
            old_stop.set()
        self.start_poller()
        return True

    def poller_alive(self):
        return bool(self.poller_thread and self.poller_thread.is_alive())

    # ---- watchdog ----
    def start_watchdog(self):
        if self.notifier is None and self.bot_token and self.chat_id:
            self.notifier = watchdog.Notifier(
                lambda text: telegram_bot.send_telegram_message(self.bot_token, self.chat_id, text)
            )
            self.notifier.start()
        self.watchdog = watchdog.Watchdog(
            restart_poller=self.restart_poller,
            stall_after=telegram_bot.current_stall_after,
            notifier=self.notifier,
            set_protect=telegram_bot.set_protect_mode,
            set_emergency=telegram_bot.set_emergency_mode,
            watch=Config.HEALTH_WATCH_THREADS,
        )
        self.watchdog.start()
        return self.watchdog

    # ---- shutdown ----
    def shutdown(self, reason="SIGTERM"):
        """إيقاف رشيق: يرجع عدد المهام التي لم تكتمل خلال المهلة (0 = نظيف)."""
        print(f"🛑 {reason}: إيقاف رشيق — لا أستقبل جديداً، أنتظر المهام الجارية ≤ {Config.SHUTDOWN_GRACE}ث...")
        self.global_stop.set()
        if self.poller_stop is not None:
            self.poller_stop.set()
        if self.watchdog is not None:
            self.watchdog.stop(timeout=1)
        deadline = time.monotonic() + Config.SHUTDOWN_GRACE
        while telegram_bot.STATE["in_flight"] > 0 and time.monotonic() < deadline:
            time.sleep(0.2)
        left = telegram_bot.STATE["in_flight"]
        self.pool.shutdown(wait=False, cancel_futures=True)
        if self.notifier is not None:
            self.notifier.stop(timeout=1)
        if left:
            print(f"⚠️ {left} مهمة لم تكتمل خلال المهلة — ستُعاد بعد الإقلاع عند تفعيل jobs.ndjson (1b).")
        else:
            print("👋 تم إيقاف المنظومة بنجاح (0 مهام معلقة).")
        return left


def install_signal_handlers(stop_event):
    """SIGTERM/SIGINT → stop_event فقط (لا عمل داخل المعالج). يعمل فقط في الخيط الرئيسي."""
    def _handler(signum, frame):
        print(f"📨 إشارة {signal.Signals(signum).name} — بدء الإيقاف الرشيق")
        stop_event.set()
    installed = []
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
            installed.append(sig)
        except (ValueError, OSError):
            pass   # ليس الخيط الرئيسي / منصة لا تدعم
    return installed


def _startup_ping(bot_token, chat_id, web_error=None):
    """
    تقرير الإقلاع بسطر واحد (22 Z6): يُطبع دائماً في اللوج، ويُرسل على تليجرام إن NOTIFY_ON_START.
    يحل محل قراءة كونسول الاستضافة: المالك يرى فوراً keys/tg/web/DATA_DIR/circuit + النسخة.
    """
    try:
        ok, line, _details = preflight.run_preflight(
            key_manager, model_circuit=model_circuit, web_port=Config.WEB_PORT, web_error=web_error,
        )
        print(f"🚀 preflight: {line}")
        if Config.NOTIFY_ON_START and bot_token and chat_id:
            extra = ""
            if not ok and web_error:
                extra = f"\n⚠️ الويب لم يشتغل: {web_error} — البوت شغال طبيعي؛ /health غير متاح حتى يُصلح المنفذ."
            telegram_bot.send_telegram_message(
                bot_token, chat_id,
                f"🚀 المنظومة اشتغلت.\n{line}{extra}\n🕛 تصفير الكوتا: {key_manager.next_reset_display()} ({Config.DISPLAY_TZ})",
            )
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

    # 2. بوت تليجرام + watchdog مستقل (03b الطبقة 3) + معالجات SIGTERM (الطبقة 0.5)
    rt = Runtime(bot_token, chat_id)
    install_signal_handlers(rt.global_stop)
    if bot_token:
        rt.start_poller()
        rt.start_watchdog()
        time.sleep(1.5)
        _startup_ping(bot_token, chat_id, web_error=web_error)
    else:
        print("ℹ️ لم يتم العثور على توكن تليجرام، المنظومة تعمل في وضع الويب فقط.")

    # 3. الخيط الرئيسي خامل: يراقب المراقب (watchdog) والويب فقط، وينتظر إشارة الإيقاف
    web_restarts = 0
    try:
        while not rt.global_stop.wait(Config.WATCHDOG_INTERVAL * 3):
            if web_t is not None and not web_t.is_alive():
                web_restarts += 1
                print(f"🩺 main: خادم الويب توقف — إعادة تشغيل (#{web_restarts})...")
                try:
                    web_t = web_service.start_web_server_thread()
                except Exception as e:
                    print(f"⚠️ فشل إعادة تشغيل الويب: {e}")
            if rt.watchdog is not None and not rt.watchdog.is_alive():
                rt.watchdog_restarts += 1
                print(f"🩺 main: الـ watchdog توقف — إعادة إنشائه (#{rt.watchdog_restarts})")
                rt.start_watchdog()
            # 401 أوقف الـ poller طوعاً → لا نعيده (التوكن باطل)؛ أي خروج آخر غير متوقع → إعادة
            if bot_token and not rt.poller_alive() and telegram_bot.STATE.get("stopped_reason") not in ("401",):
                print("🩺 main: خيط الـ poller مات بلا سبب معروف — إعادة تشغيل")
                rt.restart_poller()
    except KeyboardInterrupt:
        rt.global_stop.set()
    code = 0
    if bot_token:
        rt.shutdown(reason="إيقاف")
    sys.exit(code)


if __name__ == "__main__":
    main()
