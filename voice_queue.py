# -*- coding: utf-8 -*-
"""
voice_queue — طابور أولوية موحّد للتفريغ (03 §2.3، 19 §2، 22 U1) لتليجرام **والويب معاً**.

- `PriorityQueue` بمفتاح `(ready_at, seq)`: FIFO صارم بين الجاهزين؛ المؤجَّل له ready_at في المستقبل.
- **[قرار-مالك] `QUEUE_SKIP_DEFERRED=0`:** المؤجَّل **يحجب** ما بعده (سياق فويسات مترابطة) — العامل ينتظر
  حتى ready_at. `=1`: يتخطى المؤجَّل ويكمل الجاهز.
- **صمام الحصة:** لو التأجيل بسبب حصة (ساعات) يُستدعى `on_quota_deferral(job, wait_s)` مرة واحدة للمهمة
  (الـ bot يسأل المالك `[⏭ تخطَّ] [⏳ انتظر]`)؛ الافتراضي انتظار. `skip(job_id)` يعلّمها skipped.
- عامل تفريغ واحد (`TRANSCRIBE_WORKERS=1`) + فاصل RPM `QUEUE_PACING_SEC` بين مهمتين.
- `in_flight` موحّد: يشمل مهام تليجرام والويب → الإيقاف الرشيق ينتظر الاثنين (فجوة المرحلة 1a).
- سجل `jobs.ndjson` عبر `jobs.record` + **استئناف pending بعد restart (P0)** عبر `resume()`.
- backpressure: `QUEUE_MAX` → `QueueFull`.

المعالجة نفسها (تحميل/تفريغ/إرسال) تُحقن كدالة `processor(job)` ترجع dict نتيجة أو ترفع:
  - `transcriber.DeferredError` → تأجيل (ready_at = now + wait_s)
  - أي استثناء آخر → failed (يُسجَّل، ويُستدعى on_failed)
لا يستورد requests. monotonic عبر clock (قابل للاختبار بـ FakeClock).
"""
import itertools
import queue
import threading
import time
import uuid

from config import Config
import clock
import heartbeat
import jobs


class QueueFull(Exception):
    pass


class Job:
    __slots__ = ("job_id", "source", "chat_id", "message_id", "file_id", "file_unique_id", "mime_type",
                 "label", "size", "meta", "ready_at", "seq", "attempts", "deferrals", "state", "asked_quota",
                 "enqueued_mono", "result", "error", "skip_requested")

    def __init__(self, source, file_id, mime_type, label="voice", chat_id=None, message_id=None,
                 file_unique_id=None, size=None, meta=None, job_id=None):
        self.job_id = job_id or uuid.uuid4().hex[:12]
        self.source = source                  # "telegram" | "web" | "resume"
        self.chat_id = chat_id
        self.message_id = message_id
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.mime_type = mime_type
        self.label = label
        self.size = size
        self.meta = meta or {}
        self.ready_at = 0.0
        self.seq = 0
        self.attempts = 0
        self.deferrals = 0
        self.state = "queued"
        self.asked_quota = False
        self.enqueued_mono = 0.0
        self.result = None
        self.error = None
        self.skip_requested = False

    def journal_fields(self):
        return {"source": self.source, "chat_id": self.chat_id, "message_id": self.message_id,
                "file_id": self.file_id, "file_unique_id": self.file_unique_id, "mime": self.mime_type,
                "label": self.label, "size": self.size}


class VoiceQueue:
    def __init__(self, processor, on_deferred=None, on_quota_deferral=None, on_failed=None, on_done=None,
                 workers=None, pacing_s=None, skip_deferred=None, max_size=None, journal=True):
        self._processor = processor
        self._on_deferred = on_deferred or (lambda job, wait_s, kind: None)
        self._on_quota = on_quota_deferral or (lambda job, wait_s: None)
        self._on_failed = on_failed or (lambda job, err: None)
        self._on_done = on_done or (lambda job, result: None)
        self.workers = max(1, workers or Config.TRANSCRIBE_WORKERS)
        self.pacing_s = Config.QUEUE_PACING_SEC if pacing_s is None else pacing_s
        self.skip_deferred = Config.QUEUE_SKIP_DEFERRED if skip_deferred is None else skip_deferred
        self.max_size = max_size or Config.QUEUE_MAX
        self._journal = journal
        self._pq = queue.PriorityQueue()
        self._seq = itertools.count(1)
        self._jobs = {}                      # job_id -> Job (queued/deferred/running)
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._threads = []
        self._in_flight = 0
        self._last_job_end_mono = None
        self.stats = {"enqueued": 0, "done": 0, "failed": 0, "deferred": 0, "skipped": 0, "resumed": 0, "deduped": 0}

    # ------------------------------------------------------------------ تشغيل
    def start(self):
        for i in range(self.workers):
            t = threading.Thread(target=self._run, daemon=True, name=f"transcribe-{i + 1}")
            t.start()
            self._threads.append(t)
        return self

    def stop(self, timeout=2):
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        for t in self._threads:
            t.join(timeout=timeout)

    def is_alive(self):
        return any(t.is_alive() for t in self._threads)

    def _key(self, job):
        """
        مفتاح الترتيب: وضع الحجب (الافتراضي) → ترتيب الوصول فقط (المؤجَّل يبقى في مكانه ويحجب ما بعده)؛
        وضع التخطي → (ready_at, seq) فالجاهز يسبق المؤجَّل (19 §2).
        """
        return (job.ready_at if self.skip_deferred else 0.0, job.seq, job.job_id)

    # ------------------------------------------------------------------ إدخال
    def submit(self, job, journal_event="received"):
        with self._lock:
            if len(self._jobs) >= self.max_size:
                raise QueueFull(f"الطابور ممتلئ ({self.max_size}) — انتظر قليلاً ثم أعد الإرسال.")
            job.seq = next(self._seq)
            job.enqueued_mono = clock.monotonic()
            job.state = "queued"
            self._jobs[job.job_id] = job
            self._pq.put(self._key(job))
            self.stats["enqueued"] += 1
            position = self._position_locked(job)
            self._cv.notify()
        if self._journal and journal_event:
            jobs.record(job.job_id, journal_event, **job.journal_fields())
        return position

    def resume(self):
        """P0: يعيد المهام غير المكتملة من jobs.ndjson (file_id كافٍ؛ تليجرام يحتفظ بالملف ساعات)."""
        n = 0
        for j in jobs.pending_jobs():
            job = Job(source="resume", file_id=j["file_id"], mime_type=j.get("mime") or "audio/ogg",
                      label=j.get("label") or "voice", chat_id=j.get("chat_id"), message_id=j.get("message_id"),
                      file_unique_id=j.get("file_unique_id"), size=j.get("size"), job_id=j["job_id"])
            job.meta["resumed_from"] = j.get("state")
            try:
                self.submit(job, journal_event="resumed")
                n += 1
            except QueueFull:
                break
        self.stats["resumed"] += n
        return n

    def skip(self, job_id):
        """صمام الحصة: المالك اختار ⏭ — تُعلَّم skipped وتُزال (لو لم تبدأ)."""
        with self._cv:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job.skip_requested = True
            self._cv.notify_all()
        return True

    # ------------------------------------------------------------------ استعلام
    def _position_locked(self, job):
        return 1 + sum(1 for o in self._jobs.values() if o is not job and self._key(o) < self._key(job))

    def position(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return self._position_locked(job) if job else None

    def size(self):
        with self._lock:
            return len(self._jobs)

    @property
    def in_flight(self):
        with self._lock:
            return self._in_flight

    def snapshot(self):
        with self._lock:
            items = sorted(self._jobs.values(), key=self._key)
            now = clock.monotonic()
            return {
                "size": len(items), "in_flight": self._in_flight, "skip_deferred": self.skip_deferred,
                "stats": dict(self.stats),
                "items": [{"job_id": j.job_id, "source": j.source, "label": j.label, "state": j.state,
                           "deferrals": j.deferrals, "ready_in_s": max(0, int(j.ready_at - now)),
                           "waiting_s": int(now - j.enqueued_mono)} for j in items],
            }

    # ------------------------------------------------------------------ العامل
    def _next_job(self):
        """يحجز المهمة التالية حسب السياسة. يعود None عند الإيقاف."""
        skipped_back = []
        while not self._stop.is_set():
            try:
                key = self._pq.get(timeout=0.5)
                job_id = key[2]
            except queue.Empty:
                self._requeue(skipped_back)
                skipped_back = []
                continue
            with self._cv:
                job = self._jobs.get(job_id)
                if job is None:
                    continue
                if job.skip_requested:
                    self._finish_locked(job, "skipped")
                    self.stats["skipped"] += 1
                    jobs.record(job.job_id, "skipped", reason="owner") if self._journal else None
                    continue
                now = clock.monotonic()
                if job.ready_at > now:
                    if self.skip_deferred:
                        skipped_back.append(key)                          # نكمل الجاهز
                        continue
                    # [قرار-مالك] الحجب: ننتظر حتى موعدها (أو إيقاف/تخطٍ)
                    self._cv.wait(timeout=min(job.ready_at - now, 1.0))
                    self._pq.put(key)
                    self._requeue(skipped_back)
                    skipped_back = []
                    continue
                job.state = "running"
                self._in_flight += 1
            self._requeue(skipped_back)
            return job
        self._requeue(skipped_back)
        return None

    def _requeue(self, items):
        for it in items:
            self._pq.put(it)

    def _finish_locked(self, job, state):
        job.state = state
        self._jobs.pop(job.job_id, None)
        ev = job.meta.get("done") if isinstance(job.meta, dict) else None
        if ev is not None:
            try:
                ev.set()                      # يوقظ منتظر الويب فوراً (transcribe_via_queue)
            except Exception:
                pass

    def _pace(self):
        if self.pacing_s and self._last_job_end_mono is not None:
            gap = self.pacing_s - (clock.monotonic() - self._last_job_end_mono)
            if gap > 0:
                self._stop.wait(gap)

    def _run(self):
        heartbeat.beat("worker", note="start")
        while not self._stop.is_set():
            job = self._next_job()
            if job is None:
                break
            self._pace()
            heartbeat.beat("worker", note=f"job {job.job_id}")
            job.attempts += 1
            try:
                result = self._processor(job)
                job.result = result                    # قبل تغيير الحالة: من يراقب state==done يجد النتيجة جاهزة
                with self._lock:
                    self._finish_locked(job, "done")
                    self.stats["done"] += 1
                self._safe(self._on_done, job, result)
            except Exception as e:
                wait_s = getattr(e, "wait_s", None)
                kind = getattr(e, "kind", None)
                if wait_s:
                    self._defer(job, wait_s, kind, e)
                else:
                    job.error = str(e)
                    with self._lock:
                        self._finish_locked(job, "failed")
                        self.stats["failed"] += 1
                    if self._journal:
                        jobs.record(job.job_id, "failed", error=str(e)[:300], attempts=job.attempts)
                    self._safe(self._on_failed, job, e)
            finally:
                with self._lock:
                    self._in_flight -= 1
                self._last_job_end_mono = clock.monotonic()
                heartbeat.beat("worker", note="job end")
        heartbeat.beat("worker", note="exit")

    def _defer(self, job, wait_s, kind, err):
        with self._lock:
            job.deferrals += 1
            job.ready_at = clock.monotonic() + wait_s
            job.state = "deferred"
            self._pq.put(self._key(job))
            self.stats["deferred"] += 1
        if self._journal:
            jobs.record(job.job_id, "deferred", wait_s=wait_s, kind=kind, reason=str(err)[:200])
        self._safe(self._on_deferred, job, wait_s, kind)
        if kind == "quota" and not job.asked_quota:
            job.asked_quota = True
            self._safe(self._on_quota, job, wait_s)

    @staticmethod
    def _safe(fn, *a):
        try:
            fn(*a)
        except Exception as e:
            print(f"⚠️ voice_queue callback {getattr(fn, '__name__', fn)}: {type(e).__name__}: {e}")


def new_telegram_job(chat_id, msg, file_id, mime_type, label):
    media = msg.get("voice") or msg.get("audio") or msg.get("video_note") or msg.get("video") or msg.get("document") or {}
    return Job(source="telegram", file_id=file_id, mime_type=mime_type, label=label, chat_id=str(chat_id),
               message_id=msg.get("message_id"), file_unique_id=media.get("file_unique_id"),
               size=media.get("file_size"), meta={"update_id": msg.get("_update_id")})


def wall_from_mono(mono_ts):
    """تحويل تقريبي لعرض 'جاهز الساعة' للمالك."""
    return time.time() + (mono_ts - clock.monotonic())
