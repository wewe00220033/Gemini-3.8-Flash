# -*- coding: utf-8 -*-
"""
supervisor — المشرف النحيف (03b الطبقة 4) — اختياري: يعمل فقط مع SUPERVISED=1 [قرار-مالك ج3].

القاعدة الذهبية: **لا يستورد شيئاً من المشروع** (لا config ولا requests) — لو انكسر كود المشروع
المشرف يبقى حياً ويعيد الإطلاق. stdlib فقط، ويقرأ إعداداته من البيئة مباشرة.

الدور:
  - يطلق العامل:  python main.py --worker --generation N   (نفس المفسّر، نفس البيئة + SUPERVISED_CHILD=1)
  - يراقب ملف النبض state/worker.beat (العامل يلمسه كل 5ث)؛ قدُم > SUPERVISOR_BEAT_MAX (60ث) = عامل متجمد
    بالكامل (كل خيوطه زومبي — ما لا يستطيع watchdog الخيطي علاجه) → terminate() → kill() → إعادة إطلاق.
  - رموز خروج العامل:  0 = إيقاف مقصود (المشرف يخرج 0 أيضاً) | 3 = L4 "أعد تشغيلي" (فوراً، بلا backoff)
                      | غيرها = crash → backoff (5,10,20,60) + حد SUPERVISOR_MAX_PER_HOUR (20) ثم توقف بكود 1.
  - يكتب state/restart_reason.json قبل كل إعادة إطلاق؛ **العامل الجديد** هو من يقرأه ويُخطر المالك
    (المشرف لا يلمس الشبكة أبداً).
  - SIGTERM/SIGINT على المشرف → يمرّرها للعامل وينتظر SHUTDOWN_GRACE+5 ثم kill، ويخرج 0.
"""
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque

EXIT_OK = 0
EXIT_RESTART = 3           # يطابق Config.EXIT_CODE_RESTART (مكرر عمداً: لا import من المشروع)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_tuple(name, default):
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return tuple(int(x) for x in raw.replace("(", "").replace(")", "").split(",") if x.strip())
    except ValueError:
        return default


class Supervisor:
    def __init__(self, data_dir=None, script=None, backoff=None, max_per_hour=None, beat_max=None,
                 shutdown_grace=None, python=None, sleep=time.sleep, clock=time.monotonic, log=print,
                 beat_interval=None):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = data_dir or (os.environ.get("DATA_DIR", "").strip() or script_dir)
        self.state_dir = os.path.join(self.data_dir, "state")
        self.beat_file = os.path.join(self.state_dir, "worker.beat")
        self.reason_file = os.path.join(self.state_dir, "restart_reason.json")
        self.script = script or os.path.join(script_dir, "main.py")
        self.python = python or sys.executable
        self.backoff = backoff or _env_tuple("SUPERVISOR_BACKOFF", (5, 10, 20, 60))
        self.max_per_hour = max_per_hour or _env_int("SUPERVISOR_MAX_PER_HOUR", 20)
        # لا يقل عن 3 × فاصل النبض (5ث) — قيمة أقل تعني "تجمد" كاذب كل دورة (كشفه اختبار دخان حي)
        self.beat_interval = beat_interval or _env_int("WORKER_BEAT_INTERVAL", 5)
        self.beat_max = max(3 * self.beat_interval, beat_max or _env_int("SUPERVISOR_BEAT_MAX", 60))
        self.shutdown_grace = shutdown_grace or _env_int("SHUTDOWN_GRACE", 20)
        self._sleep, self._clock, self._log = sleep, clock, log
        self.generation = 0
        self.consecutive_crashes = 0
        self.restart_times = deque()           # monotonic لكل إعادة إطلاق خلال الساعة
        self.proc = None
        self.stopping = False
        self.history = deque(maxlen=50)        # (wall_ts, generation, reason, code)

    # ------------------------------------------------------------------ أدوات
    def _ensure_dirs(self):
        os.makedirs(self.state_dir, exist_ok=True)

    def beat_age(self):
        """عمر ملف النبض بالثواني (None لو غير موجود)."""
        try:
            return max(0.0, time.time() - os.path.getmtime(self.beat_file))
        except OSError:
            return None

    def write_reason(self, reason, code=None, detail=""):
        self._ensure_dirs()
        payload = {"ts": time.time(), "generation": self.generation, "reason": reason, "exit_code": code,
                   "detail": detail, "consecutive_crashes": self.consecutive_crashes}
        tmp = self.reason_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.reason_file)
        except OSError as e:
            self._log(f"⚠️ supervisor: تعذر كتابة restart_reason.json: {e}")
        self.history.append((payload["ts"], self.generation, reason, code))

    def restarts_last_hour(self):
        now = self._clock()
        while self.restart_times and now - self.restart_times[0] > 3600:
            self.restart_times.popleft()
        return len(self.restart_times)

    def next_backoff(self):
        idx = min(max(0, self.consecutive_crashes - 1), len(self.backoff) - 1)
        return self.backoff[idx]

    # ------------------------------------------------------------------ العامل
    def spawn(self):
        self._ensure_dirs()
        self.generation += 1
        # نبض أولي: العامل الجديد يحتاج وقتاً للإقلاع قبل أول لمسة — لا نحكم عليه متجمداً فوراً
        try:
            with open(self.beat_file, "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass
        env = dict(os.environ)
        env["SUPERVISED_CHILD"] = "1"
        env["SUPERVISED"] = "1"
        env["WORKER_GENERATION"] = str(self.generation)
        env.setdefault("PYTHONUNBUFFERED", "1")
        cmd = [self.python, self.script, "--worker", "--generation", str(self.generation)]
        self.proc = subprocess.Popen(cmd, env=env, cwd=os.path.dirname(self.script) or None)
        self._log(f"👷 supervisor: أطلقت العامل #{self.generation} (pid {self.proc.pid})")
        return self.proc

    def _terminate(self, grace):
        """terminate → انتظار grace → kill. يرجع كود الخروج."""
        p = self.proc
        if p is None:
            return None
        if p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
            try:
                return p.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except OSError:
                    pass
                try:
                    return p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    return None
        return p.returncode

    def _on_signal(self, signum, frame):
        self._log(f"📨 supervisor: إشارة {signal.Signals(signum).name} — تمريرها للعامل وإيقاف رشيق")
        self.stopping = True

    def install_signals(self):
        for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
            if sig is None:
                continue
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass

    # ------------------------------------------------------------------ الحلقة
    def run_once(self):
        """
        دورة واحدة: إطلاق العامل ومراقبته حتى يخرج/يتجمد/يُطلب الإيقاف.
        ترجع (reason, code): reason ∈ exit_ok | restart_requested | crash | frozen | stopping
        """
        self.spawn()
        while True:
            if self.stopping:
                code = self._terminate(self.shutdown_grace + 5)
                return "stopping", code
            code = self.proc.poll()
            if code is not None:
                if code == EXIT_OK:
                    return "exit_ok", code
                if code == EXIT_RESTART:
                    return "restart_requested", code
                return "crash", code
            age = self.beat_age()
            if age is not None and age > self.beat_max:
                self._log(f"🧊 supervisor: العامل #{self.generation} متجمد (نبض منذ {int(age)}ث > {self.beat_max}) → terminate/kill")
                code = self._terminate(10)
                return "frozen", code
            self._sleep(1)

    def run(self):
        """الحلقة الكاملة. ترجع كود خروج المشرف."""
        self.install_signals()
        self._log(f"🛡️ supervisor: الطبقة 4 مفعّلة (SUPERVISED=1) — backoff {self.backoff}، حد {self.max_per_hour}/ساعة، نبض ≤ {self.beat_max}ث")
        while True:
            reason, code = self.run_once()
            if reason == "stopping":
                self._log(f"👋 supervisor: العامل خرج ({code}) — إيقاف المشرف (0)")
                return EXIT_OK
            if reason == "exit_ok":
                self._log("👋 supervisor: العامل خرج بكود 0 (إيقاف مقصود) — إيقاف المشرف")
                return EXIT_OK
            # إعادة إطلاق مطلوبة
            if reason == "restart_requested":
                self.consecutive_crashes = 0
                delay = 1
                detail = "L4: العامل طلب إعادة تشغيل نفسه"
            else:
                self.consecutive_crashes += 1
                delay = self.next_backoff()
                detail = f"{'تجمد كامل' if reason == 'frozen' else 'انهيار'} (كود {code}) — إعادة #{self.consecutive_crashes} متتالية"
            self.restart_times.append(self._clock())
            n = self.restarts_last_hour()
            if n > self.max_per_hour:
                self.write_reason("gave_up", code, f"{n} إعادة خلال ساعة > {self.max_per_hour} — توقف المشرف")
                self._log(f"🛑 supervisor: {n} إعادة إطلاق خلال ساعة > {self.max_per_hour} — crash-loop، أتوقف (كود 1) ليراها المالك")
                return 1
            self.write_reason(reason, code, detail)
            self._log(f"🔁 supervisor: {detail} → إعادة الإطلاق بعد {delay}ث ({n}/{self.max_per_hour} في الساعة)")
            self._sleep(delay)
            if self.stopping:
                return EXIT_OK


def read_and_clear_restart_reason(path):
    """يقرأه العامل الجديد عند الإقلاع (ليُخطر المالك) ثم يحذفه حتى لا يُبلَّغ مرتين. None لو لا يوجد."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return data


def touch_beat(path):
    """يلمسه العامل كل WORKER_BEAT_INTERVAL ثانية من خيط مستقل (لا شبكة)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(str(time.time()))
        return True
    except OSError:
        return False


if __name__ == "__main__":
    sys.exit(Supervisor().run())
