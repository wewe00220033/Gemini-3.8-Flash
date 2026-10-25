# -*- coding: utf-8 -*-
"""
اختبارات ربط الطابور الموحّد (1b) في telegram_bot/web_service/main:
👀 فوري، إيصال فقط لو الطابور > 1، >20MB مرفوض، فيديو مقبول، صمام الحصة inline + callback skip/wait،
/queue، مسار الويب يمر من نفس الطابور، in_flight موحّد والإيقاف الرشيق ينتظر تفريغ الويب.
"""
import importlib
import threading
import time

import pytest

import heartbeat
from tests.fake_telegram import FakeResp


def _wait(cond, timeout=5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def tb(fresh_env, monkeypatch):
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    monkeypatch.setattr(telegram_bot.key_manager, "get_telegram_config", lambda: ("TOK", "bot", "777"))
    # fresh_env يعيد تحميل config → voice_queue يحمل مرجعاً لصنف Config القديم؛ نضبط الاثنين
    import voice_queue
    for c in (telegram_bot.Config, voice_queue.Config):
        monkeypatch.setattr(c, "QUEUE_PACING_SEC", 0)
    telegram_bot._test_vq = voice_queue
    sent = []

    def fake_post(url, json=None, timeout=None, **kw):
        sent.append({"method": url.rsplit("/", 1)[-1], "payload": json})
        return FakeResp(200, {"ok": True, "result": {"message_id": 500 + len(sent)}})

    monkeypatch.setattr(telegram_bot, "_http_post", fake_post)
    heartbeat.reset()
    yield telegram_bot, sent
    telegram_bot.stop_queue()
    heartbeat.reset()


def _voice(mid=1, size=1000, chat=777):
    return {"message_id": mid, "chat": {"id": chat},
            "voice": {"file_id": f"F{mid}", "file_unique_id": f"U{mid}", "mime_type": "audio/ogg", "file_size": size}}


def _methods(sent):
    return [s["method"] for s in sent]


# ---------- الاستقبال ----------

def test_reaction_immediately_and_no_receipt_for_single_job(tb, monkeypatch):
    bot, sent = tb
    gate = threading.Event()
    monkeypatch.setattr(bot, "process_job", lambda job: gate.wait(5) or {"text": "x"})
    job = bot.enqueue_telegram_audio("TOK", "777", _voice(1), "F1", "audio/ogg", "voice")
    assert job is not None and job.source == "telegram" and job.meta["token"] == "TOK"
    assert _methods(sent)[0] == "setMessageReaction"
    assert sent[0]["payload"]["reaction"][0]["emoji"] == bot.Config.REACTION_RECEIVED
    assert "sendMessage" not in _methods(sent)          # [قرار-مالك] لا إيصال لمهمة واحدة
    gate.set()


def test_receipt_only_when_queue_has_more_than_one(tb, monkeypatch):
    bot, sent = tb
    gate = threading.Event()
    monkeypatch.setattr(bot, "process_job", lambda job: gate.wait(5) or {"text": "x"})
    bot.enqueue_telegram_audio("TOK", "777", _voice(1), "F1", "audio/ogg", "voice")
    assert _wait(lambda: bot.QUEUE.in_flight == 1)
    bot.enqueue_telegram_audio("TOK", "777", _voice(2), "F2", "audio/ogg", "voice")
    receipts = [s for s in sent if s["method"] == "sendMessage"]
    assert len(receipts) == 1 and "ترتيبك في الطابور: 2" in receipts[0]["payload"]["text"]
    assert receipts[0]["payload"]["reply_to_message_id"] == 2
    gate.set()


def test_reaction_disabled_by_config(tb, monkeypatch):
    bot, sent = tb
    monkeypatch.setattr(bot.Config, "REACTION_ENABLED", False)
    assert bot.set_reaction("TOK", "777", 1) is False and sent == []


def test_oversized_file_rejected_before_queue(tb, monkeypatch):
    bot, sent = tb
    big = _voice(3, size=21 * 1024 * 1024)
    assert bot.enqueue_telegram_audio("TOK", "777", big, "F3", "audio/ogg", "voice") is None
    assert _methods(sent) == ["sendMessage"] and "20MB" in sent[0]["payload"]["text"]
    assert bot.QUEUE is None                              # لم يُنشأ الطابور حتى


def test_video_and_video_note_accepted_when_enabled(tb, monkeypatch):
    bot, _ = tb
    vn = {"message_id": 1, "chat": {"id": 777}, "video_note": {"file_id": "V1", "file_unique_id": "UV1"}}
    vid = {"message_id": 2, "chat": {"id": 777}, "video": {"file_id": "V2", "mime_type": "video/mp4", "file_name": "clip.mp4"}}
    assert bot._is_audio_message(vn) == ("V1", "video/mp4", "video_note")
    assert bot._is_audio_message(vid) == ("V2", "video/mp4", "clip.mp4")
    monkeypatch.setattr(bot.Config, "ACCEPT_VIDEO", False)
    assert bot._is_audio_message(vn) is None and bot._is_audio_message(vid) is None


def test_queue_full_sends_backpressure_message(tb, monkeypatch):
    bot, sent = tb
    monkeypatch.setattr(bot._test_vq.Config, "QUEUE_MAX", 1)
    gate = threading.Event()
    monkeypatch.setattr(bot, "process_job", lambda job: gate.wait(5) or {"text": "x"})
    bot.enqueue_telegram_audio("TOK", "777", _voice(1), "F1", "audio/ogg", "voice")
    assert _wait(lambda: bot.QUEUE.in_flight == 1)
    # المهمة الجارية ما زالت محسوبة في الطابور (max=1) → الثانية تُرفض برسالة 🚦
    assert bot.enqueue_telegram_audio("TOK", "777", _voice(2), "F2", "audio/ogg", "voice") is None
    assert any("🚦" in s["payload"].get("text", "") for s in sent if s["method"] == "sendMessage")
    gate.set()


# ---------- المعالجة ----------

def test_process_job_journals_steps_and_dedups_second_send(tb, monkeypatch):
    import jobs
    import voice_queue
    bot, sent = tb
    monkeypatch.setattr(bot, "_download_file", lambda t, f: b"abc")
    monkeypatch.setattr(bot, "transcribe_audio_bytes_ex",
                        lambda b, m: ("النص", "gemini-3.6-flash", "A", {"elapsed": 1.0, "skipped": []}))
    j1 = voice_queue.new_telegram_job("777", _voice(1), "F1", "audio/ogg", "voice")
    j1.meta["token"] = "TOK"
    jobs.record(j1.job_id, "received", **j1.journal_fields())      # ما يكتبه submit() عادة
    bot.process_job(j1)
    st = jobs.load_state()
    assert st[j1.job_id]["state"] == "sent" and st[j1.job_id]["events"] == ["received", "downloaded", "transcribed", "sent"]
    # نفس file_unique_id مرة ثانية → لا تحميل ولا تفريغ، إرسال النص المحفوظ

    def no_download(t, f):
        raise AssertionError("no download expected")

    monkeypatch.setattr(bot, "_download_file", no_download)
    j2 = voice_queue.new_telegram_job("777", _voice(1, 1000), "F1", "audio/ogg", "voice")
    j2.meta["token"] = "TOK"
    r = bot.process_job(j2)
    assert r["dedup"] is True and "النص" in sent[-1]["payload"]["text"]
    assert jobs.load_state()[j2.job_id]["dedup_of"] == j1.job_id


def test_download_retry_then_clear_error(tb, monkeypatch):
    bot, _ = tb
    monkeypatch.setattr(bot.Config, "DOWNLOAD_BACKOFF", (0, 0, 0))
    calls = []

    def flaky(t, f):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("boom")
        return b"ok"

    monkeypatch.setattr(bot, "_download_file", flaky)
    assert bot._download_with_retry("TOK", "F") == b"ok" and len(calls) == 3
    calls.clear()

    def down(t, f):
        calls.append(1)
        raise ConnectionError("down")

    monkeypatch.setattr(bot, "_download_file", down)
    with pytest.raises(RuntimeError) as ei:
        bot._download_with_retry("TOK", "F")
    assert "تعذر تحميل" in str(ei.value) and "ConnectionError" in str(ei.value) and len(calls) == 3


def _raise_quota(job):
    from transcriber import DeferredError
    raise DeferredError("quota", 600, "quota")


def test_quota_valve_inline_once_and_callback_skip(tb, monkeypatch):
    bot, sent = tb
    monkeypatch.setattr(bot, "process_job", _raise_quota)
    job = bot.enqueue_telegram_audio("TOK", "777", _voice(1), "F1", "audio/ogg", "voice")
    assert _wait(lambda: job.deferrals >= 1 and job.job_id in bot._quota_prompts)
    prompts = [s for s in sent if s["method"] == "sendMessage" and "reply_markup" in (s["payload"] or {})]
    assert len(prompts) == 1
    kb = prompts[0]["payload"]["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in kb] == [f"q:skip:{job.job_id}", f"q:wait:{job.job_id}"]
    assert "ReplyKeyboard" not in str(prompts[0]["payload"])
    # المالك يضغط ⏭ → skip + حذف رسالة الصمام + answerCallbackQuery
    note = bot.handle_callback("TOK", {"id": "cq1", "data": f"q:skip:{job.job_id}", "message": {"chat": {"id": 777}}})
    assert "تخطّي" in note and job.job_id not in bot._quota_prompts
    assert "deleteMessage" in _methods(sent) and _methods(sent)[-1] == "answerCallbackQuery"
    assert _wait(lambda: bot.QUEUE.stats["skipped"] == 1 and bot.QUEUE.size() == 0)


def test_callback_wait_keeps_job_and_unknown_data_is_safe(tb, monkeypatch):
    bot, sent = tb
    monkeypatch.setattr(bot, "process_job", _raise_quota)
    job = bot.enqueue_telegram_audio("TOK", "777", _voice(1), "F1", "audio/ogg", "voice")
    assert _wait(lambda: job.job_id in bot._quota_prompts)
    note = bot.handle_callback("TOK", {"id": "cq2", "data": f"q:wait:{job.job_id}"})
    assert "هستنى" in note and bot.QUEUE.size() == 1 and job.state == "deferred"
    assert bot.handle_callback("TOK", {"id": "cq3", "data": "garbage"}) == ""
    assert _methods(sent)[-1] == "answerCallbackQuery"


def test_queue_command_text(tb, monkeypatch):
    bot, sent = tb
    bot.handle_text("TOK", "777", {"message_id": 9}, "/queue")
    assert "غير مشغّل" in sent[-1]["payload"]["text"]
    gate = threading.Event()
    monkeypatch.setattr(bot, "process_job", lambda job: gate.wait(5) or {"text": "x"})
    bot.enqueue_telegram_audio("TOK", "777", _voice(1), "F1", "audio/ogg", "voice")
    assert _wait(lambda: bot.QUEUE.in_flight == 1)
    bot.handle_text("TOK", "777", {"message_id": 10}, "/queue")
    txt = sent[-1]["payload"]["text"]
    assert "📋 الطابور: 1 مهمة | جارية 1" in txt and "حجب (ترتيب الوصول)" in txt and "voice [telegram] running" in txt
    assert "/queue" in bot._help_text()
    gate.set()


# ---------- الويب موحّد مع تليجرام ----------

def test_web_transcribe_goes_through_same_queue_and_in_flight(tb, monkeypatch):
    bot, _ = tb
    entered = threading.Event()
    gate = threading.Event()

    def slow(b, m):
        entered.set()
        gate.wait(5)
        return "ويب", "gemini-3.6-flash", "A", {"elapsed": 0.5}

    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", bot.transcribe_via_queue(b"xyz", "audio/webm", transcribe_fn=slow)))
    t.start()
    assert entered.wait(3)
    assert bot.QUEUE.in_flight == 1 and bot.queue_in_flight() == 1       # تفريغ الويب محسوب
    snap = bot.QUEUE.snapshot()
    assert snap["items"][0]["source"] == "web" and snap["items"][0]["state"] == "running"
    gate.set()
    t.join(3)
    assert out["r"][0] == "ويب" and bot.QUEUE.in_flight == 0 and bot.STATE["processed"] == 1


def test_web_transcribe_propagates_failure(tb):
    bot, _ = tb

    def boom(b, m):
        raise RuntimeError("كل الموديلات فشلت")

    with pytest.raises(RuntimeError, match="كل الموديلات"):
        bot.transcribe_via_queue(b"x", "audio/webm", transcribe_fn=boom)
    assert bot.QUEUE.stats["failed"] == 1


def test_web_api_uses_unified_queue(tb, monkeypatch):
    import web_service
    importlib.reload(web_service)
    bot, _ = tb
    monkeypatch.setattr(web_service, "transcribe_audio_bytes_ex", lambda b, m: ("نص", "m", "k", {"elapsed": 1}))
    assert web_service._transcribe(b"abc", "audio/webm")[0] == "نص"
    assert bot.QUEUE is not None and bot.QUEUE.stats["done"] == 1
    assert web_service.build_health()["queue"]["stats"]["done"] == 1


def test_runtime_shutdown_waits_for_web_job(tb, monkeypatch):
    bot, _ = tb
    import main
    importlib.reload(main)
    main.telegram_bot = bot
    monkeypatch.setattr(main.Config, "SHUTDOWN_GRACE", 5)
    entered, gate = threading.Event(), threading.Event()

    def slow(b, m):
        entered.set()
        gate.wait(5)
        return "ويب", "m", "k", {}

    t = threading.Thread(target=lambda: bot.transcribe_via_queue(b"x", "audio/webm", transcribe_fn=slow))
    t.start()
    assert entered.wait(3)
    rt = main.Runtime("TOK", "777")
    threading.Timer(0.4, gate.set).start()
    t0 = time.monotonic()
    left = rt.shutdown(reason="test")
    assert left == 0 and 0.3 <= time.monotonic() - t0 < 4       # انتظر تفريغ الويب ولم يقطعه
    t.join(2)
    assert bot.QUEUE is None                                    # stop_queue نُفّذ
