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

import os
import sys
import time
import json
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
import supervisor
import incidents
import jobs
import logkit
import observability
import backup
import audit
import panel
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
    # المرحلة 5 (09 §2–3): حجب الأسرار على كل القنوات + ملف يومي logs/app_YYYY-MM-DD.log مع تدوير واحتفاظ
    try:
        import logkit
        logkit.install(root, level)
    except Exception as e:  # pragma: no cover
        print(f"⚠️ logkit: {e}")
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
        self.exit_code = 0                      # 3 = L4 "أعد تشغيلي" (المشرف يعيد فوراً)
        self.exit_reason = None
        self._beat_stop = threading.Event()
        self._beat_thread = None
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
            request_exit=self.request_exit if Config.SUPERVISED else None,   # L4 مسلّح فقط مع SUPERVISED=1
        )
        self.watchdog.start()
        web_service.RUNTIME["watchdog"] = self.watchdog
        # المرحلة 5: تقارير الحوادث تُخطر المالك عبر نفس طابور الإشعارات (لا شبكة داخل الـ watchdog)
        if self.notifier is not None:
            incidents.set_notifier(self.notifier.notify)
        return self.watchdog

    # ---- L4 (1c) ----
    def request_exit(self, reason):
        """
        L4: يكتب state/restart_reason.json ثم يطلب خروج العملية بكود 3. المشرف (SUPERVISED=1) يعيد الإطلاق
        فوراً، والعامل الجديد يقرأ الملف ويُخطر المالك. لا نستدعي os._exit هنا مباشرة: نضبط global_stop
        فيمر الخيط الرئيسي بـ shutdown() الرشيق (≤ SHUTDOWN_GRACE) ثم sys.exit(3)؛ ولو تجمد كل شيء فعلاً
        يتكفل المشرف بالقتل عبر ملف النبض (worker.beat).
        """
        self.exit_code = Config.EXIT_CODE_RESTART
        self.exit_reason = reason
        _write_restart_reason("L4", reason)
        print(f"🔁 L4: {reason} → طلب إعادة تشغيل العملية (exit {Config.EXIT_CODE_RESTART})")
        self.global_stop.set()

    # ---- نبض العامل للمشرف (ملف، لا شبكة) ----
    def start_worker_beat(self):
        if not os.environ.get("SUPERVISED_CHILD"):
            return None

        def _run():
            while not self._beat_stop.wait(Config.WORKER_BEAT_INTERVAL):
                supervisor.touch_beat(Config.WORKER_BEAT_FILE)
        supervisor.touch_beat(Config.WORKER_BEAT_FILE)
        self._beat_thread = threading.Thread(target=_run, daemon=True, name="worker-beat")
        self._beat_thread.start()
        return self._beat_thread

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
        # 1b: عداد موحّد (تليجرام + ويب) من الطابور — لا نقطع تفريغاً جارياً من أي مصدر
        while self._in_flight() > 0 and time.monotonic() < deadline:
            time.sleep(0.2)
        left = self._in_flight()
        pending = telegram_bot.QUEUE.size() if telegram_bot.QUEUE is not None else 0
        telegram_bot.stop_queue(timeout=1)
        self.pool.shutdown(wait=False, cancel_futures=True)
        if self.notifier is not None:
            self.notifier.stop(timeout=1)
        self._beat_stop.set()
        if left or pending:
            print(f"⚠️ {left} مهمة جارية لم تكتمل + {max(0, pending - left)} في الانتظار — مهام تليجرام ستُستأنف بعد الإقلاع من jobs.ndjson (P0).")
        else:
            print("👋 تم إيقاف المنظومة بنجاح (0 مهام معلقة).")
        return left

    @staticmethod
    def _in_flight():
        return max(telegram_bot.queue_in_flight(), telegram_bot.STATE.get("in_flight", 0))


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


def _write_restart_reason(kind, detail):
    """state/restart_reason.json — يقرأه العامل الجديد عند الإقلاع ليُخطر المالك (03b الطبقة 4)."""
    try:
        os.makedirs(os.path.dirname(Config.RESTART_REASON_FILE), exist_ok=True)
        tmp = Config.RESTART_REASON_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "reason": kind, "detail": detail,
                       "generation": os.environ.get("WORKER_GENERATION")}, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, Config.RESTART_REASON_FILE)
    except OSError as e:
        print(f"⚠️ تعذر كتابة restart_reason.json: {e}")


def _restart_notice():
    """لو أُعيد تشغيلنا (L4 أو crash أو تجمد) — سطر للمالك يُلحق برسالة الإقلاع. يحذف الملف بعد القراءة."""
    data = supervisor.read_and_clear_restart_reason(Config.RESTART_REASON_FILE)
    if not data:
        return ""
    try:
        incidents.on_boot_restart(data)                      # المرحلة 5 (12 §3.3): تقرير حادث لإعادة التشغيل
    except Exception as e:
        print(f"⚠️ incidents boot: {e}")
    reason = data.get("reason")
    labels = {"L4": "L4 — تعثر الاستقبال رغم كل الطبقات", "restart_requested": "L4 — طلب إعادة تشغيل",
              "crash": "انهيار العملية", "frozen": "تجمد كامل (قتله المشرف)", "gave_up": "المشرف استسلم"}
    age = int(time.time() - float(data.get("ts") or time.time()))
    gen = os.environ.get("WORKER_GENERATION")
    gen_txt = f" (العامل #{gen})" if gen else ""
    return f"\n🔁 أعدت تشغيل نفسي قبل {age}ث{gen_txt} — السبب: {labels.get(reason, reason)}: {data.get('detail') or ''}".rstrip(": ")


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
        st, txt = observability.persist_probe_check()        # 09 §6
        if st != observability.OK:
            print(f"⚠️ {txt}")
            line += f"\n⚠️ {txt}"
        if Config.NOTIFY_ON_START and bot_token and chat_id:
            extra = _restart_notice()
            if not ok and web_error:
                extra += f"\n⚠️ الويب لم يشتغل: {web_error} — البوت شغال طبيعي؛ /health غير متاح حتى يُصلح المنفذ."
            telegram_bot.send_telegram_message(
                bot_token, chat_id,
                f"🚀 المنظومة اشتغلت.\n{line}{extra}\n🕛 تصفير الكوتا: {key_manager.next_reset_display()} ({Config.DISPLAY_TZ})",
            )
    except Exception as e:
        print(f"⚠️ تعذر إرسال إشعار الإقلاع: {e}")


_DOCTOR_LAST = {"worst": None}


def _periodic_doctor(rt):
    """12 §3.2: فحص دوري صامت كل DOCTOR_INTERVAL_MIN؛ يُبلَّغ المالك فقط عند ❌ جديد (انتقال إلى fail)، ومرة عند التعافي.
    يضغط jobs.ndjson ويطبّق احتفاظ السجلات في نفس الدورة (09 §1 / 03). بلا getMe (الـ poller نفسه يثبت الاتصال)."""
    try:
        lines, worst = observability.doctor_report(bot_state=telegram_bot.STATE, queue=telegram_bot.QUEUE, watchdog=rt.watchdog, check_telegram=False)
        prev = _DOCTOR_LAST["worst"]
        _DOCTOR_LAST["worst"] = worst
        print(f"🩺 doctor دوري: {worst}")
        if rt.notifier is not None:
            if worst == observability.FAIL and prev != observability.FAIL:
                bad = [ln for ln in lines[1:] if ln.startswith("❌")]
                rt.notifier.notify("🩺 الفحص الدوري وجد مشكلة:\n" + "\n".join(bad) + "\n/doctor للتقرير الكامل")
            elif prev == observability.FAIL and worst != observability.FAIL:
                rt.notifier.notify("🩺 الفحص الدوري: المشكلة السابقة زالت ✅")
    except Exception as e:
        print(f"⚠️ doctor دوري: {e}")
    try:
        if jobs.compact_if_needed():
            print("🧹 jobs.ndjson ضُغط")
        n = logkit.enforce_retention()
        if n:
            print(f"🧹 سجلات: حُذف {n} ملف قديم")
    except Exception as e:
        print(f"⚠️ تنظيف دوري: {e}")


def _weekly_backup_tick(rt, bot_token, now=None):
    """09 §5: لو حان الموعد (اليوم/الساعة ولم تُنفَّذ هذا الأسبوع) → نسخة تلقائية إلى الشات + backups/ (آخر 4).
    بلا استثناءات؛ الفشل يُسجَّل في backup_state + audit ويُبلَّغ المالك سطراً واحداً."""
    try:
        if not backup.weekly_due(now):
            return None
        audit.set_actor("scheduler")
        def _send(name, data, caption):
            telegram_bot._send_document(bot_token, rt.chat_id, name, data, "application/zip", caption, None)
        res = backup.run_weekly(send_fn=_send if (bot_token and rt.chat_id) else None, now=now)
        if res.get("ok"):
            print(f"🗂️ نسخة أسبوعية: {res.get('files')} ملف، {len(res.get('paths') or [])} جزء، حُذف {res.get('removed')} قديم"
                  + (f" ({res.get('error')})" if res.get('error') else ""))
        else:
            print(f"⚠️ النسخة الأسبوعية فشلت: {res.get('error')}")
            if rt.notifier is not None:
                rt.notifier.notify(f"⚠️ النسخة الاحتياطية الأسبوعية فشلت: {res.get('error')}\n/backup يدوياً · /doctor")
        return res
    except Exception as e:  # pragma: no cover
        print(f"⚠️ جدولة النسخ الأسبوعي: {e}")
        return None


def _idle_loop(rt, web_t, bot_token, tick=1.0):
    """
    حلقة الخيط الرئيسي: تنام بنبضات قصيرة (tick) لا بـ WATCHDOG_INTERVAL*3 دفعة واحدة.
    السبب: SIGTERM قد تُسلَّم لخيط آخر، ومعالج بايثون لا يعمل إلا عندما يستيقظ الخيط الرئيسي —
    نوم 30 ث يعني تأخر الإيقاف الرشيق حتى 30 ث (كشفه اختبار الدخان في 1b). الفحوصات الدورية تبقى كل 30 ث.
    """
    period = Config.WATCHDOG_INTERVAL * 3
    next_check = time.monotonic() + period
    next_doctor = time.monotonic() + max(60, Config.DOCTOR_INTERVAL_MIN * 60) if Config.DOCTOR_INTERVAL_MIN > 0 else None
    web_restarts = 0
    while not rt.global_stop.wait(tick):
        if next_doctor is not None and time.monotonic() >= next_doctor:
            next_doctor = time.monotonic() + Config.DOCTOR_INTERVAL_MIN * 60
            _periodic_doctor(rt)
        if time.monotonic() < next_check:
            continue
        next_check = time.monotonic() + period
        _weekly_backup_tick(rt, bot_token)
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
    return web_t


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

    # 0. مرحلة 2 (04 §2.5): قواطع الموديلات تستمر عبر إعادة التشغيل (state/circuit.json — المنتهي يُهمل)
    try:
        model_circuit.attach_store(Config.CIRCUIT_FILE)
        active = [x for x in model_circuit.snapshot(Config.MODELS) if x["paused"]]
        if active:
            print("⚡ قواطع محمّلة من الجلسة السابقة: " + ", ".join(
                f"{x['model']} ({x['reason']}، يفتح بعد {x['remaining_s']}ث)" for x in active))
    except Exception as e:
        print(f"⚠️ تعذر تحميل circuit.json: {e} — نكمل بقواطع في الذاكرة فقط.")

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
    rt.start_worker_beat()                 # فقط تحت المشرف (SUPERVISED_CHILD=1) — ملف نبض، لا شبكة
    if Config.SUPERVISED and not os.environ.get("SUPERVISED_CHILD"):
        print("ℹ️ SUPERVISED=1 لكن العملية ليست تحت المشرف (شُغّلت بـ --worker يدوياً؟) — L4 سيخرج بكود 3 بلا من يعيد الإطلاق.")
    # 1b: الطابور الموحّد يبدأ قبل الـ poller ويستأنف المهام المعلقة من الجلسة السابقة (P0)
    try:
        telegram_bot.start_queue(resume=bool(bot_token))
    except Exception as e:
        print(f"⚠️ تعذر بدء الطابور الموحّد: {e}")
    # 11 §4: تجاوزات settings.json (/set) تُطبَّق على Config قبل أي عمل
    try:
        n_over = panel.apply_persisted_overrides()
        if n_over:
            print(f"⚙️ طُبّق {n_over} تجاوز من settings.json")
    except Exception as e:
        print(f"⚠️ تجاوزات الإعدادات: {e}")
    if bot_token:
        telegram_bot.register_commands(bot_token)     # 11 §5
        rt.start_poller()
        rt.start_watchdog()
        time.sleep(1.5)
        _startup_ping(bot_token, chat_id, web_error=web_error)
    else:
        print("ℹ️ لم يتم العثور على توكن تليجرام، المنظومة تعمل في وضع الويب فقط.")

    # 3. الخيط الرئيسي خامل: يراقب المراقب (watchdog) والويب فقط، وينتظر إشارة الإيقاف
    try:
        web_t = _idle_loop(rt, web_t, bot_token)
    except KeyboardInterrupt:
        rt.global_stop.set()
    rt.shutdown(reason="إيقاف" if rt.exit_code == 0 else f"L4 ({rt.exit_reason})")   # أيضاً في وضع الويب-فقط
    sys.exit(rt.exit_code)


def entry(argv=None):
    """
    نقطة الدخول (1c): SUPERVISED=0 (افتراضي) → main() مباشرة كما كان تماماً (لا عملية إضافية).
    SUPERVISED=1 وبدون --worker → نعمل كمشرف نحيف يطلق "main.py --worker" ويراقبه (الطبقة 4).
    --worker → العامل الحقيقي (تحت المشرف أو يدوياً).
    """
    argv = sys.argv[1:] if argv is None else argv
    if "--worker" in argv or not Config.SUPERVISED:
        return main()
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass
    return sys.exit(supervisor.Supervisor().run())


if __name__ == "__main__":
    entry()
