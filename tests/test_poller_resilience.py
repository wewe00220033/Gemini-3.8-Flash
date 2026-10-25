# -*- coding: utf-8 -*-
"""
اختبارات حلقة الـ poller (03b الطبقات 1–2) بـ FakeTelegram + FakeClock — بلا شبكة ولا نوم حقيقي.
F1 ReadTimeout → L0 وضع الحذر؛ F3 ضوضاء شبكة → backoff؛ 409 مرة واحدة؛ 401 يوقف؛ stop_event يخرج نظيفاً.
"""
import importlib
import threading

import pytest
import requests

import clock
import heartbeat
import netsession
from tests.fake_telegram import FakeTelegram, FakeResp, updates


@pytest.fixture
def tb(fresh_env, monkeypatch):
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    # توكن + chat id حتى يعمل main()
    monkeypatch.setattr(telegram_bot.key_manager, "get_telegram_config", lambda: ("TOK", "bot", "777"))
    # لا نوم حقيقي: _wait يسجل المدة ويعود فوراً
    waits = []
    monkeypatch.setattr(telegram_bot, "_wait", lambda ev, s: waits.append(s))
    telegram_bot._test_waits = waits
    heartbeat.reset()
    netsession.cautious = netsession.CautiousMode()
    fc = clock.FakeClock()
    clock.use(fc)
    telegram_bot._test_clock = fc
    yield telegram_bot
    clock.reset()
    heartbeat.reset()


def _run(tb, script, monkeypatch, stop=None):
    stop = stop or threading.Event()
    ft = FakeTelegram(script, stop_event=stop).install(tb, monkeypatch)
    tb.main(stop_event=stop)
    return ft, stop


def test_read_timeout_enters_cautious_mode_and_session_reset(tb, monkeypatch):
    """F1: ReadTimeout واحد → L0: وضع الحذر (poll 10) + backoff 3ث، ثم يعود طبيعياً بعد 5 دقائق."""
    resets = []
    monkeypatch.setattr(tb.netsession, "reset", lambda reason=None: resets.append(reason) or True)
    ft, _ = _run(tb, [requests.exceptions.ReadTimeout("dead socket"), FakeResp()], monkeypatch)
    polls = [c for c in ft.calls if "getUpdates" in c["url"]]
    assert polls[0]["params"]["timeout"] == tb.Config.TG_POLL_TIMEOUT
    assert polls[0]["timeout"] == (tb.Config.TG_CONNECT_TIMEOUT, tb.Config.TG_POLL_TIMEOUT + tb.Config.TG_READ_MARGIN)
    # بعد ReadTimeout: الدورة التالية بوضع الحذر
    assert polls[1]["params"]["timeout"] == tb.Config.TG_POLL_TIMEOUT_CAUTIOUS
    assert polls[1]["timeout"][1] == tb.Config.TG_POLL_TIMEOUT_CAUTIOUS + tb.Config.TG_READ_MARGIN
    assert tb.STATE["read_timeouts"] == 1 and tb.STATE["net_errors"] == 1
    assert tb._test_waits[0] == tb.Config.NET_BACKOFF[0]
    assert tb.netsession.cautious.entries == 1
    # الـ poller يقتل Session الخيط عند الخروج دائماً
    assert any("exit" in str(r) for r in resets)
    # انتهاء وضع الحذر
    tb._test_clock.advance(tb.Config.CAUTIOUS_MODE_MINUTES * 60 + 1)
    assert tb.netsession.cautious.poll_timeout() == tb.Config.TG_POLL_TIMEOUT


def test_connection_errors_use_exponential_backoff_then_reset_on_success(tb, monkeypatch):
    """F3: DNS/ConnectionError متتالية → 3,6,12,30,30 ثم تصفير عند أول 200."""
    errs = [requests.exceptions.ConnectionError("dns") for _ in range(5)]
    ft, _ = _run(tb, errs + [FakeResp(), requests.exceptions.ConnectionError("again")], monkeypatch)
    assert tb._test_waits[:5] == [3, 6, 12, 30, 30]
    assert tb._test_waits[5] == 3          # بعد النجاح يبدأ السلم من أوله
    assert tb.STATE["net_errors"] == 6
    assert tb.STATE["last_poll_ok"] is not None


def test_409_reported_once_and_keeps_polling(tb, monkeypatch, capsys):
    ft, _ = _run(tb, [FakeResp(409), FakeResp(409), FakeResp(409)], monkeypatch)
    out = capsys.readouterr().out
    assert out.count("409 Conflict") == 1
    assert tb._test_waits == [10, 10, 10]
    assert "409" in tb.STATE["last_error"]


def test_401_stops_poller(tb, monkeypatch):
    stop = threading.Event()
    ft, _ = _run(tb, [FakeResp(401), FakeResp(), FakeResp()], monkeypatch, stop=stop)
    polls = [c for c in ft.calls if "getUpdates" in c["url"]]
    assert len(polls) == 1                       # لم يكرر بعد 401
    assert tb.STATE["stopped_reason"] == "401"
    assert tb.STATE["alive"] is False
    assert not stop.is_set()                     # خرج من تلقاء نفسه لا بسبب stop_event


def test_stop_event_exits_cleanly_without_shutting_shared_pool(tb, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=1)
    stop = threading.Event()
    FakeTelegram([FakeResp()], stop_event=stop).install(tb, monkeypatch)
    tb.main(stop_event=stop, pool=pool)
    assert tb.STATE["stopped_reason"] == "stop_event"
    assert tb.STATE["poller_generation"] == 1
    # الـ pool المشترك لا يُغلق من الـ poller (يبقى للمهام الجارية عبر L1)
    fut = pool.submit(lambda: 1)
    assert fut.result(timeout=2) == 1
    pool.shutdown(wait=True)
    # إعادة تشغيل (L1) ترفع الجيل ولا تصفر started_at
    started = tb.STATE["started_at"]
    stop2 = threading.Event()
    FakeTelegram([], stop_event=stop2).install(tb, monkeypatch)
    tb.main(stop_event=stop2)
    assert tb.STATE["poller_generation"] == 2 and tb.STATE["started_at"] == started


def test_updates_dispatch_audio_to_pool_and_beat_poller(tb, monkeypatch):
    submitted = []

    class Pool:
        def submit(self, fn, *a):
            submitted.append((fn.__name__, a))

    msg = {"message_id": 5, "chat": {"id": 777}, "voice": {"file_id": "F1", "mime_type": "audio/ogg"}}
    stranger = {"message_id": 6, "chat": {"id": 999}, "voice": {"file_id": "F2", "mime_type": "audio/ogg"}}
    stop = threading.Event()
    FakeTelegram([updates(msg, stranger)], stop_event=stop).install(tb, monkeypatch)
    tb.main(stop_event=stop, pool=Pool())
    assert len(submitted) == 1 and submitted[0][0] == "handle_audio" and submitted[0][1][3] == "F1"
    snap = heartbeat.snapshot()
    assert "poller" in snap and snap["poller"]["count"] >= 2
