# -*- coding: utf-8 -*-
"""
watchdog — خيط مراقبة مستقل لا يلمس الشبكة أبداً (03b الطبقة 3) + سلم التصعيد L1→L3→L3b.

المبدأ: الـ watchdog يفحص النبضات (heartbeat، monotonic) كل WATCHDOG_INTERVAL=10ث ويقرر؛
أي رسالة للمالك تُدفع إلى طابور إشعارات يخدمه خيط `notifier` منفصل — لو الشبكة معلقة فالـ watchdog
نفسه لا يعلق أبداً (F7). والخيط الرئيسي الخامل يراقب نبض الـ watchdog ("من يراقب المراقب").

سلم التصعيد (03b §3):
  L0  ReadTimeout واحد            → داخل الـ poller نفسه (netsession.cautious) — ليس هنا.
  L1  stall للـ poller            → restart_poller(): stop_event القديم + خيط poller جديد (صامت، يُسجَّل).
  L3  3 × L1 خلال 10 دقائق        → وضع الحماية: poll 10ث مستمر + إشعار واحد للمالك.
  L3b بعد L3 إن استمر stall       → short-poll طوارئ (timeout=0 كل 2ث) لدقيقتين ثم عودة لـ L3 (19 §1).
  L4  (os._exit) فقط مع SUPERVISED=1 — المرحلة 1c؛ هنا نبقى في L3 ونُخطر.

كل شيء قابل للحقن: clock (FakeClock)، heartbeat، دوال التشغيل. لا استيراد لـ requests.
"""
import queue
import threading
from collections import deque

from config import Config
import clock
import heartbeat

L1_POLLER_RESTART = "L1"
L3_PROTECT = "L3"
L3B_EMERGENCY = "L3b"


class Notifier:
    """
    خيط إرسال الإشعارات: الوحيد الذي يلمس الشبكة نيابة عن الـ watchdog.
    `send_fn(text)` تُحقن (عادةً lambda تنادي telegram_bot.send_telegram_message).
    """

    def __init__(self, send_fn, name="notifier"):
        self._send = send_fn
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._thread = None
        self.name = name
        self.sent = 0
        self.failed = 0

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name=self.name)
        self._thread.start()
        return self._thread

    def stop(self, timeout=2):
        self._stop.set()
        self._q.put(None)
        if self._thread:
            self._thread.join(timeout=timeout)

    def notify(self, text):
        self._q.put(text)

    def pending(self):
        return self._q.qsize()

    def drain_once(self, timeout=0):
        """يخدم عنصراً واحداً (للاختبارات بلا خيط). يرجع True لو خدم شيئاً."""
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return False
        if item is None:
            return False
        heartbeat.beat(self.name, note="sending")
        try:
            ok = self._send(item)
            if ok is False:
                self.failed += 1
            else:
                self.sent += 1
        except Exception as e:
            self.failed += 1
            print(f"⚠️ notifier: فشل إرسال إشعار: {type(e).__name__}: {e}")
        heartbeat.beat(self.name, note="sent")
        return True

    def _run(self):
        heartbeat.beat(self.name, note="start")
        while not self._stop.is_set():
            if not self.drain_once(timeout=5):
                heartbeat.beat(self.name, note="idle")


class Watchdog:
    """
    يفحص كل interval ثانية ويصعّد. لا شبكة. يكتب نبضه في heartbeat("watchdog").

    المعاملات المحقونة:
      restart_poller()  → يُنادى عند L1، يرجع True لو أعاد التشغيل.
      stall_after()     → العتبة بالثواني (netsession.cautious.stall_after: ≈55 عادي / ≈35 حذر).
      set_protect(on)   → يفعّل/يطفئ وضع الحماية في الـ poller (poll قصير دائماً).
      set_emergency(on) → يفعّل/يطفئ short-poll الطوارئ (L3b).
      notifier          → Notifier أو None (صامت).
    """

    def __init__(self, restart_poller, stall_after, notifier=None, set_protect=None, set_emergency=None,
                 interval=None, watch=("poller",)):
        self._restart_poller = restart_poller
        self._stall_after = stall_after
        self._notifier = notifier
        self._set_protect = set_protect or (lambda on: None)
        self._set_emergency = set_emergency or (lambda on: None)
        self.interval = interval or Config.WATCHDOG_INTERVAL
        self.watch = tuple(watch)
        self._stop = threading.Event()
        self._thread = None
        self.events = deque(maxlen=200)          # (mono, level, detail)
        self._l1_times = deque()                 # monotonic لكل L1 ضمن النافذة
        self.level = None                        # أعلى درجة نشطة: None | L3 | L3b
        self.protect_notified = False
        self.emergency_until = 0.0
        self._last_restart_mono = None
        self.restarts = 0
        self._started_mono = clock.monotonic()

    # ------------------------------------------------------------------
    def _is_stalled(self, name, threshold, now):
        """
        خيط لم ينبض قط لا يُعد عالقاً إلا بعد مرور العتبة من بدء الـ watchdog (سباق الإقلاع:
        الـ watchdog قد يفحص قبل أول نبضة للـ poller — اكتُشف في اختبار دخان حقيقي).
        """
        a = heartbeat.age(name)
        if a is None:
            return (now - self._started_mono) > threshold
        return a > threshold

    def start(self):
        self._started_mono = clock.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True, name="watchdog")
        self._thread.start()
        return self._thread

    def stop(self, timeout=2):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def is_alive(self):
        return bool(self._thread and self._thread.is_alive())

    def _run(self):
        heartbeat.beat("watchdog", note="start")
        while not self._stop.is_set():
            try:
                self.check()
            except Exception as e:
                print(f"⚠️ watchdog: خطأ داخلي: {type(e).__name__}: {e}")
            self._stop.wait(self.interval)

    # ------------------------------------------------------------------
    def _record(self, level, detail):
        self.events.append((clock.monotonic(), level, detail))
        print(f"🩺 watchdog {level}: {detail}")

    def _prune_l1(self, now):
        window = Config.PROTECT_WINDOW_MINUTES * 60
        while self._l1_times and now - self._l1_times[0] > window:
            self._l1_times.popleft()

    def l1_in_window(self):
        self._prune_l1(clock.monotonic())
        return len(self._l1_times)

    def check(self):
        """دورة فحص واحدة (قابلة للنداء المباشر في الاختبارات). ترجع الدرجة المتخذة أو None."""
        now = clock.monotonic()
        heartbeat.beat("watchdog", note="check")
        threshold = self._stall_after()

        # L3b انتهى؟ → عودة إلى L3 (الحماية تبقى)
        if self.level == L3B_EMERGENCY and now >= self.emergency_until:
            self._set_emergency(False)
            self.level = L3_PROTECT
            self._record(L3_PROTECT, "انتهى short-poll الطوارئ — عودة لوضع الحماية")

        stalled = [n for n in self.watch if self._is_stalled(n, threshold, now)]
        if not stalled:
            # تعافٍ: لو مرّت النافذة كاملة بلا L1 جديد → إطفاء الحماية
            if self.level in (L3_PROTECT, L3B_EMERGENCY) and self.l1_in_window() == 0:
                self._set_protect(False)
                self._set_emergency(False)
                self.level = None
                self.protect_notified = False
                self._record("recovered", "الشبكة استقرت — إطفاء وضع الحماية")
            return None

        # لا نكرر L1 قبل مرور العتبة من آخر إعادة (نعطي الخيط الجديد فرصة)
        if self._last_restart_mono is not None and now - self._last_restart_mono < threshold:
            return None

        # ---- L1 ----
        detail = ", ".join(f"{n} عالق {int(heartbeat.age(n) or 0)}ث > {int(threshold)}" for n in stalled)
        restarted = False
        try:
            restarted = bool(self._restart_poller())
        except Exception as e:
            detail += f" | فشل إعادة التشغيل: {type(e).__name__}: {e}"
        self._last_restart_mono = now
        self.restarts += 1
        self._l1_times.append(now)
        self._record(L1_POLLER_RESTART, f"{detail} → poller جديد {'✅' if restarted else '❌'}")
        heartbeat.beat("poller", note="L1 restart grace")   # نبض وكيل حتى يبدأ الخيط الجديد

        # ---- L3 / L3b ----
        n = self.l1_in_window()
        if n >= Config.PROTECT_AFTER_RESTARTS:
            if self.level is None:
                self.level = L3_PROTECT
                self._set_protect(True)
                self._record(L3_PROTECT, f"{n} × L1 خلال {Config.PROTECT_WINDOW_MINUTES} د → وضع الحماية (poll {Config.TG_POLL_TIMEOUT_CAUTIOUS}ث)")
                if self._notifier and not self.protect_notified:
                    self.protect_notified = True
                    self._notifier.notify(
                        f"🛡️ الشبكة متقلبة — أعمل بوضع آمن (أعدت تشغيل الاستقبال {n} مرات خلال {Config.PROTECT_WINDOW_MINUTES} د). "
                        f"لا يلزمك فعل شيء؛ سأعود للوضع الطبيعي تلقائياً عند الاستقرار."
                    )
                return L3_PROTECT
            if self.level == L3_PROTECT:
                # L3b: الحماية لم تكفِ → short-poll طوارئ لدقيقتين
                self.level = L3B_EMERGENCY
                self.emergency_until = now + Config.EMERGENCY_SHORT_POLL_SECONDS
                self._set_emergency(True)
                self._record(L3B_EMERGENCY, f"stall رغم الحماية → short-poll كل {Config.EMERGENCY_SHORT_POLL_INTERVAL}ث لـ {Config.EMERGENCY_SHORT_POLL_SECONDS}ث")
                return L3B_EMERGENCY
        return L1_POLLER_RESTART

    def snapshot(self):
        return {
            "level": self.level,
            "restarts": self.restarts,
            "l1_in_window": self.l1_in_window(),
            "emergency_remaining_s": max(0, int(self.emergency_until - clock.monotonic())) if self.level == L3B_EMERGENCY else 0,
            "recent": [{"level": lv, "detail": d, "age_s": int(clock.monotonic() - t)} for t, lv, d in list(self.events)[-5:]],
        }
