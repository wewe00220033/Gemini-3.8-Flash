# -*- coding: utf-8 -*-
"""
المرحلة 5 — المراقبة الكاملة (09 §7 + 12 §5): logkit (حجب/تدوير/احتفاظ)، /logs، /trace، /doctor، تقارير الحوادث.
كل الاختبارات بلا شبكة. DoD المغطاة:
  09 §7 بند 1 (مفتاح محجوب في الملف)، بند 2 (تدوير يومي + ضغط + حذف > 90 يوماً)، بند 4 (/trace لفشل تحميل: 3 محاولات تحميل و0 Gemini)،
  بند 5 (سقف 500MB يحذف الأقدم أولاً)؛ 12 §5 بند 2 (/doctor يكمل رغم فشل فحص)، بند 4 (تقرير حادث عند stall مُحاكى بخط زمني).
"""
import gzip
import importlib
import json
import logging
import os
import time

import pytest

VALID_A = "AIzaSy" + "A" * 33
TG_TOKEN = "1234567890:" + "B" * 35
M1 = "gemini-3.6-flash"


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or json.dumps(payload or {})
        self.headers = {}

    def json(self):
        return self._payload


def ok_text(text):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


@pytest.fixture
def LK(fresh_env, monkeypatch):
    import logkit
    monkeypatch.setattr(logkit.Config, "LOG_TO_FILE", True)
    return logkit


@pytest.fixture
def bot(fresh_env, monkeypatch):
    import sessions
    sessions.reset_cache()
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    sent, docs = [], []

    def fake_post(url, json=None, timeout=None, **kw):
        sent.append({"method": url.rsplit("/", 1)[-1], "payload": json, "kw": kw})
        return FakeResp(200, {"ok": True, "result": {"message_id": 500 + len(sent)}})

    monkeypatch.setattr(telegram_bot, "_http_post", fake_post)
    monkeypatch.setattr(telegram_bot, "_send_document",
                        lambda token, chat_id, filename, data, mime, caption, reply_to_id: docs.append((filename, data, caption)) or 900)
    return telegram_bot, sent, docs


def _texts(sent):
    return [x["payload"].get("text", "") for x in sent if x["method"] == "sendMessage"]


def _cmd(tb, text, reply_to=None):
    msg = {"message_id": 1}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to}
    tb.handle_text("T", "1", msg, text)


def _voice_job(uid, mid=5):
    import voice_queue
    import jobs
    msg = {"message_id": mid, "chat": {"id": 1}, "date": 1, "voice": {"file_id": "f" + uid, "file_unique_id": uid}}
    job = voice_queue.new_telegram_job("1", msg, "f" + uid, "audio/ogg", "voice")
    job.meta["token"] = "T"
    jobs.record(job.job_id, "received", **job.journal_fields())
    return job


# =====================================================================
# logkit — الحجب (09 §3، DoD 1)
# =====================================================================

def test_redact_masks_gemini_telegram_web_tokens(fresh_env, monkeypatch):
    import logkit
    monkeypatch.setattr(logkit.Config, "WEB_ACCESS_TOKEN", "webtok-SECRET-12345")
    txt = f"خطأ 403 للمفتاح {VALID_A} والتوكن {TG_TOKEN} و token=webtok-SECRET-12345 x-goog-api-key: {VALID_A}"
    out = logkit.redact(txt)
    assert VALID_A not in out and TG_TOKEN not in out and "webtok-SECRET-12345" not in out
    assert "AIza…****" in out and "****:****" in out
    assert logkit.redact("") == "" and logkit.redact(None) is None
    assert logkit.redact("نص عادي بلا أسرار 123") == "نص عادي بلا أسرار 123"


def test_redact_filter_protects_file_and_stream(LK, tmp_path):
    root = logging.getLogger("t5-redact")
    root.handlers[:] = []
    root.propagate = False
    root.setLevel(logging.INFO)
    stream_path = tmp_path / "stream.txt"
    sh = logging.StreamHandler(open(stream_path, "w", encoding="utf-8"))
    sh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(sh)
    fh = LK.install(root)
    assert fh is not None and any(isinstance(f, LK.RedactFilter) for f in root.filters)
    root.info("مفتاح %s فشل", VALID_A)
    root.error("توكن " + TG_TOKEN)
    sh.flush()
    sh.stream.close()
    fh.close()
    day_file = LK.day_path()
    body = open(day_file, encoding="utf-8").read()
    assert VALID_A not in body and TG_TOKEN not in body and "AIza…****" in body and "| ERROR" in body
    assert VALID_A not in open(stream_path, encoding="utf-8").read()
    assert LK.install(root) is fh or isinstance(LK.install(root), LK.DailyFileHandler)   # آمن للتكرار: لا handler ثانٍ
    assert sum(isinstance(h, LK.DailyFileHandler) for h in root.handlers) == 1


# =====================================================================
# logkit — التدوير والاحتفاظ (09 §2، DoD 2 و5)
# =====================================================================

def _make_record(msg, created):
    r = logging.LogRecord("x", logging.INFO, "f", 1, msg, None, None)
    r.created = created
    return r


def test_daily_rotation_gzips_previous_day(LK):
    h = LK.DailyFileHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    t0 = time.mktime((2026, 1, 10, 12, 0, 0, 0, 0, 0))
    h.emit(_make_record("يوم أول", t0))
    h.emit(_make_record("يوم ثانٍ", t0 + 86400))
    h.close()
    files = {os.path.basename(p): d for p, d, _s in LK.list_log_files()}
    gz = [f for f in files if f.endswith(".log.gz")]
    plain = [f for f in files if f.endswith(".log")]
    assert len(gz) == 1 and len(plain) == 1
    with gzip.open(os.path.join(LK.logs_dir(), gz[0]), "rt", encoding="utf-8") as f:
        assert "يوم أول" in f.read()
    assert "يوم ثانٍ" in open(os.path.join(LK.logs_dir(), plain[0]), encoding="utf-8").read()


def test_size_rotation_inside_day(LK, monkeypatch):
    monkeypatch.setattr(LK.Config, "LOG_FILE_MAX_MB", 0)       # أي سطر يتجاوز السقف → تدوير فوري
    h = LK.DailyFileHandler()
    h.setFormatter(logging.Formatter("%(message)s"))
    now = time.time()
    for i in range(3):
        h.emit(_make_record(f"سطر {i}", now))
    h.close()
    names = sorted(os.path.basename(p) for p, _d, _s in LK.list_log_files())
    assert any(".1.log.gz" in n for n in names) and any(".2.log.gz" in n for n in names)


def test_retention_days_and_total_cap_oldest_first(LK, monkeypatch):
    os.makedirs(LK.logs_dir(), exist_ok=True)
    now = time.time()
    days = [now - d * 86400 for d in (120, 100, 10, 5, 0)]
    for ts in days:
        with open(LK.day_path(LK._utc_day(ts)), "w", encoding="utf-8") as f:
            f.write("x" * 1000)
    removed = LK.enforce_retention(now=now)
    assert removed == 2                                                       # 120 و100 يوماً > 90
    left = [d for _p, d, _s in LK.list_log_files()]
    assert len(left) == 3 and LK._utc_day(now - 10 * 86400) in left
    monkeypatch.setattr(LK.Config, "LOG_TOTAL_MAX_MB", 0)                     # سقف 0 → يحذف الأقدم أولاً، اليوم الحالي يبقى
    removed = LK.enforce_retention(now=now)
    assert removed == 2
    left = [d for _p, d, _s in LK.list_log_files()]
    assert left == [LK._utc_day(now)]


def test_tail_and_errors_and_read_day_are_redacted(LK):
    os.makedirs(LK.logs_dir(), exist_ok=True)
    day = LK._utc_day()
    with open(LK.day_path(day), "w", encoding="utf-8") as f:
        f.write(f"12:00:00 | INFO    | عادي {VALID_A}\n12:00:01 | ERROR   | ❌ فشل\n12:00:02 | INFO    | آخر\n")
    t = LK.tail(2)
    assert len(t) == 2 and "آخر" in t[-1]
    e = LK.tail(errors_only=True)
    assert len(e) == 1 and "فشل" in e[0]
    d = LK.read_day(day)
    assert len(d) == 3 and VALID_A not in "\n".join(d) and "AIza…****" in d[0]
    assert LK.read_day("1999-01-01") == []


# =====================================================================
# /trace (09 §4، DoD 4)
# =====================================================================

def test_trace_download_failure_three_attempts_zero_gemini(bot, monkeypatch):
    tb, sent, _docs = bot
    import jobs
    import observability
    monkeypatch.setattr(tb.Config, "DOWNLOAD_RETRIES", 3)
    monkeypatch.setattr(tb.Config, "DOWNLOAD_BACKOFF", (0, 0, 0))
    monkeypatch.setattr(tb.time, "sleep", lambda s: None)
    monkeypatch.setattr(tb, "_download_file", lambda t, f: (_ for _ in ()).throw(ConnectionError("net down")))
    gem = []
    monkeypatch.setattr(tb, "transcribe_audio_bytes_ex", lambda b, m: gem.append(1) or ("x", M1, "A", {}))
    job = _voice_job("dl1", mid=77)
    with pytest.raises(RuntimeError):
        tb.process_job(job)
    rows = jobs.events_of(job.job_id)
    assert [r["event"] for r in rows] == ["received", "download_fail", "download_fail", "download_fail"]
    assert gem == []
    txt = observability.trace_text(job.job_id)
    assert "محاولات التحميل الفاشلة 3" in txt and "محاولات Gemini 0" in txt and "net down" in txt
    assert observability.trace_text(job.job_id[:6]) == txt                   # بادئة المعرّف تكفي
    assert observability.trace_text("nope-xyz") is None
    assert jobs.find_by_message("1", 77) == job.job_id and jobs.find_by_message("1", 999) is None


def test_trace_success_includes_gemini_attempts_and_session(bot, monkeypatch):
    tb, sent, _docs = bot
    import jobs
    import observability
    import sessions
    import transcriber
    sessions.start("تتبع")
    monkeypatch.setattr(tb, "_download_file", lambda t, f: b"abc")

    def fake_transcribe(b, m):
        # يحاكي ما يفعله transcriber عند كل محاولة (الخيط نفسه → thread-local)
        att = type("A", (), {"outcome": "SERVER_ERROR", "elapsed": 0.3, "detail": "503", "status": 503})()
        transcriber._log_attempt(M1, "A", att)
        att2 = type("A", (), {"outcome": transcriber.SUCCESS, "elapsed": 1.2, "detail": "", "status": 200})()
        transcriber._log_attempt("gemini-3.5-transcribe", "A", att2)
        return ("نص الفويس هنا بما يكفي", "gemini-3.5-transcribe", "A", {"elapsed": 1.5, "skipped": []})

    monkeypatch.setattr(tb, "transcribe_audio_bytes_ex", fake_transcribe)
    job = _voice_job("ok1")
    tb.process_job(job)
    assert getattr(transcriber._current_job, "job_id", None) is None          # يُمسح بعد المهمة
    ev = [r["event"] for r in jobs.events_of(job.job_id)]
    assert ev == ["received", "downloaded", "attempt", "attempt", "transcribed", "sent"]
    txt = observability.trace_text(job.job_id)
    assert "SERVER_ERROR 300ms" in txt and "3.5-transcribe [A] SUCCESS 1200ms" in txt
    assert "محاولات Gemini 2" in txt and "📂 «تتبع»" in txt and "الحالة النهائية: sent" in txt


def test_attempt_journal_only_when_job_bound(fresh_env):
    import transcriber
    import jobs
    transcriber.set_current_job(None)
    att = type("A", (), {"outcome": "SERVER_ERROR", "elapsed": 0.1, "detail": "x", "status": 503})()
    transcriber._log_attempt(M1, "A", att)                                   # بلا مهمة → لا صف
    assert jobs.load_state() == {}


def test_trace_command_paths(bot, monkeypatch):
    tb, sent, _docs = bot
    import jobs
    last = lambda: _texts(sent)[-1]  # noqa: E731
    _cmd(tb, "/trace")
    assert "الاستخدام" in last()
    _cmd(tb, "/trace nothing")
    assert "لا أثر" in last()
    job = _voice_job("c1", mid=42)
    jobs.record(job.job_id, "failed", error="boom")
    _cmd(tb, f"/trace {job.job_id}")
    assert "<pre>" in last() and "boom" in last()
    _cmd(tb, "/trace", reply_to=42)                                           # رد على الفويس
    assert job.job_id in last()


# =====================================================================
# /logs
# =====================================================================

def test_logs_command_tail_errors_and_day_file(bot, LK, monkeypatch):
    tb, sent, docs = bot
    last = lambda: _texts(sent)[-1]  # noqa: E731
    _cmd(tb, "/logs")
    assert "لا سجلات" in last()
    os.makedirs(LK.logs_dir(), exist_ok=True)
    day = LK._utc_day()
    with open(LK.day_path(day), "w", encoding="utf-8") as f:
        for i in range(30):
            f.write(f"12:00:{i:02d} | INFO    | سطر {i} {VALID_A}\n")
        f.write("12:01:00 | ERROR   | ❌ خطأ كبير\n")
    _cmd(tb, "/logs")
    assert "آخر 20 سطر" in last() and VALID_A not in last() and "AIza…****" in last()
    _cmd(tb, "/logs 5")
    assert "آخر 5 سطر" in last() and "سطر 26" in last()
    _cmd(tb, "/logs errors")
    assert "خطأ/تحذير" in last() and "خطأ كبير" in last() and "سطر 3 " not in last()
    _cmd(tb, f"/logs {day}")
    assert docs and docs[-1][0] == f"app_{day}.log" and VALID_A not in docs[-1][1].decode("utf-8")
    _cmd(tb, "/logs 1999-01-01")
    assert "لا سجل ليوم" in last()


# =====================================================================
# /doctor (12 §3.1، DoD 2)
# =====================================================================

def test_doctor_report_completes_even_if_a_check_raises(fresh_env, monkeypatch):
    import observability
    import key_manager
    fresh_env.key_manager.add_key(VALID_A, "A")
    monkeypatch.setattr(key_manager.key_manager, "quota_ledger", lambda: (_ for _ in ()).throw(RuntimeError("kaput")))
    import text_assist
    monkeypatch.setattr(text_assist.requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    lines, worst = observability.doctor_report(bot_state={"alive": False}, queue=None, watchdog=None)
    joined = "\n".join(lines)
    assert lines[0].startswith("🩺") and len(lines) >= 10
    assert "تعذر الفحص (RuntimeError: kaput)" in joined                      # الفحص المكسور لا يوقف التقرير
    assert "القرص" in joined and "الأحجام" in joined and "الجلسات" in joined and "العملية" in joined
    assert worst in ("ok", "warn", "fail")


def test_doctor_statuses_and_persist_probe(fresh_env, monkeypatch):
    import observability
    import sessions
    sessions.reset_cache()
    st, txt = observability.persist_probe_check()
    assert st == observability.OK and "كُتب الآن" in txt and os.path.exists(observability.Config.PERSIST_PROBE_FILE)
    st, txt = observability.persist_probe_check()
    assert st == observability.OK and "موجود" in txt
    os.remove(observability.Config.PERSIST_PROBE_FILE)
    sessions.start("أ")
    st, txt = observability.persist_probe_check()
    assert st == observability.WARN and "غير مستمر" in txt
    # لا مفاتيح → ❌ ؛ البوت غير نشط → ⚠️ ؛ الحالة العامة fail
    lines, worst = observability.doctor_report(bot_state={"alive": False}, queue=None)
    assert worst == observability.FAIL and any("❌ المفاتيح والحصة: لا مفاتيح" in ln for ln in lines)
    fresh_env.key_manager.add_key(VALID_A, "A")
    now = time.time()
    lines, worst = observability.doctor_report(bot_state={"alive": True, "last_poll_ok": now - 5, "started_at": now - 120}, queue=None, now=now)
    assert any(ln.startswith("✅ الاستقبال") for ln in lines) and any("✅ المفاتيح" in ln for ln in lines)
    lines, worst = observability.doctor_report(bot_state={"alive": True, "last_poll_ok": now - 400}, queue=None, now=now)
    assert any(ln.startswith("❌ الاستقبال") for ln in lines)


def test_doctor_never_calls_gemini(fresh_env, monkeypatch):
    import observability
    import transcriber
    import text_assist
    fresh_env.key_manager.add_key(VALID_A, "A")
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("Gemini call forbidden"))  # noqa: E731
    monkeypatch.setattr(transcriber.requests, "post", boom)
    monkeypatch.setattr(text_assist.requests, "post", boom)
    lines, _w = observability.doctor_report(bot_state={}, queue=None)
    assert not any("Gemini call forbidden" in ln for ln in lines)


def test_doctor_command(bot):
    tb, sent, _docs = bot
    _cmd(tb, "/doctor")
    assert "🩺 الطبيب الذاتي" in _texts(sent)[-1] and "القرص" in _texts(sent)[-1]
    _cmd(tb, "/help")
    assert "/doctor" in _texts(sent)[-1] and "/trace" in _texts(sent)[-1] and "/logs" in _texts(sent)[-1] and "/incidents" in _texts(sent)[-1]


# =====================================================================
# تقارير الحوادث (12 §3.3، DoD 4)
# =====================================================================

def test_incident_on_simulated_stall_has_timeline_and_notifies(fresh_env, monkeypatch):
    import incidents
    import jobs
    import watchdog
    import heartbeat
    import clock
    notes = []
    incidents.set_notifier(notes.append)
    jobs.record("j-aaa", "received", source="telegram", label="voice", session_name="س1")
    jobs.record("j-aaa", "failed", error="timeout")
    fc = clock.FakeClock()
    prev = clock.use(fc)
    try:
        heartbeat.reset()
        heartbeat.beat("poller")
        fc.advance(200)
        wd = watchdog.Watchdog(restart_poller=lambda: True, stall_after=lambda: 55, interval=10, watch=("poller",))
        level = wd.check()
    finally:
        clock.use(prev)
        heartbeat.reset()
    assert level == watchdog.L1_POLLER_RESTART
    files = incidents.list_recent()
    assert len(files) == 1 and os.path.basename(files[0]).startswith("INC-")
    body = open(files[0], encoding="utf-8").read()
    assert "تعليق الاستقبال" in body and "الخط الزمني" in body and "j-aaa" in body and "failed" in body and "س1" in body
    assert "## القواطع" in body and "## الـ watchdog" in body and "L1" in body
    assert notes and "🚨 حادث" in notes[0] and "/trace" in notes[0]


def test_incident_cooldown_fail_streak_and_prune(fresh_env, monkeypatch):
    import incidents
    assert incidents.write_report("stall", "a") and incidents.write_report("stall", "b") is None   # تهدئة لنفس النوع
    assert incidents.write_report("stall", "c", force=True)
    incidents.reset_state()
    assert incidents.on_job_result(False, "e1") is None and incidents.on_job_result(False, "e2") is None
    p = incidents.on_job_result(False, "e3")
    assert p and "فشل متتالٍ" in open(p, encoding="utf-8").read() and "e3" in open(p, encoding="utf-8").read()
    assert incidents.on_job_result(False, "e4") is None                      # مرة واحدة لكل سلسلة
    assert incidents.on_job_result(True) is None and incidents.fail_streak() == 0
    monkeypatch.setattr(incidents.Config, "INCIDENTS_KEEP", 2)
    for i in range(3):
        incidents.write_report("manual", f"m{i}", force=True, now=time.time() + i * 60)
    assert len(incidents.list_recent()) == 2
    assert incidents.latest_text() and incidents.latest_text(10).endswith("…")


def test_incident_on_boot_restart_and_errors_redacted(fresh_env, LK, monkeypatch):
    import incidents
    os.makedirs(LK.logs_dir(), exist_ok=True)
    with open(LK.day_path(), "w", encoding="utf-8") as f:
        f.write(f"12:00:01 | ERROR   | ❌ مفتاح {VALID_A} مرفوض\n")
    assert incidents.on_boot_restart(None) is None
    p = incidents.on_boot_restart({"reason": "frozen", "detail": "no beat 90s"})
    body = open(p, encoding="utf-8").read()
    assert "إعادة تشغيل العملية" in body and "frozen" in body and "no beat 90s" in body
    assert "AIza…****" in body and VALID_A not in body


def test_queue_failures_trigger_incident_via_bot(bot, monkeypatch):
    tb, sent, _docs = bot
    import incidents
    import voice_queue
    j = voice_queue.Job(source="telegram", file_id="f", mime_type="audio/ogg", chat_id="1", message_id=3)
    j.meta["token"] = "T"
    for i in range(3):
        tb._on_job_failed(j, RuntimeError(f"فشل {i}"))
    assert incidents.fail_streak() == 3 and len(incidents.list_recent()) == 1
    tb._on_job_done(j, {})
    assert incidents.fail_streak() == 0


def test_incidents_command(bot):
    tb, sent, docs = bot
    last = lambda: _texts(sent)[-1]  # noqa: E731
    _cmd(tb, "/incidents")
    assert "لا حوادث" in last()
    _cmd(tb, "/incidents test")
    assert "كُتب: INC-" in last()
    _cmd(tb, "/incidents")
    assert "آخر الحوادث" in last() and "<pre>" in last() and docs and docs[-1][0].startswith("INC-")


# =====================================================================
# main: الطبيب الدوري + التنظيف
# =====================================================================

def test_periodic_doctor_notifies_only_on_new_fail_and_recovery(fresh_env, monkeypatch):
    import main
    import observability
    notes = []
    rt = type("RT", (), {"notifier": type("N", (), {"notify": staticmethod(notes.append)})(), "watchdog": None})()
    seq = iter([(["h", "❌ المفاتيح: لا مفاتيح"], observability.FAIL), (["h", "❌ المفاتيح: لا مفاتيح"], observability.FAIL),
                (["h", "✅ ok"], observability.OK), (["h", "✅ ok"], observability.OK)])
    monkeypatch.setattr(observability, "doctor_report", lambda **kw: next(seq))
    main._DOCTOR_LAST["worst"] = None
    for _ in range(4):
        main._periodic_doctor(rt)
    assert len(notes) == 2 and "وجد مشكلة" in notes[0] and "❌ المفاتيح" in notes[0] and "زالت" in notes[1]


def test_config_defaults_phase5(fresh_env):
    import config
    assert config.Config.LOG_RETENTION_DAYS == 90 and config.Config.LOG_TOTAL_MAX_MB == 500
    assert config.Config.INCIDENT_FAIL_STREAK == 3 and config.Config.DOCTOR_INTERVAL_MIN == 60
    assert config.Config.LOGS_DIR.endswith("logs") and config.Config.INCIDENTS_DIR.endswith("incidents")
