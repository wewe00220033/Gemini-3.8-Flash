# -*- coding: utf-8 -*-
"""
اختبارات Runtime في main.py (03b الطبقة 0.5 + L1 بخيوط حقيقية):
- restart_poller: الخيط القديم يخرج، جيل جديد يبدأ، الـ pool المشترك يبقى، لا زومبي.
- shutdown: ينتظر in_flight حتى SHUTDOWN_GRACE ثم يرجع 0 عند الاكتمال / العدد المتبقي عند التجاوز.
- install_signal_handlers: SIGTERM يضبط stop_event فقط.
"""
import importlib
import signal
import threading
import time

import pytest

import heartbeat
from tests.fake_telegram import FakeTelegram, FakeResp


@pytest.fixture
def env(fresh_env, monkeypatch):
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    monkeypatch.setattr(telegram_bot.key_manager, "get_telegram_config", lambda: ("TOK", "bot", "777"))
    import main
    importlib.reload(main)
    main.telegram_bot = telegram_bot
    heartbeat.reset()
    yield main, telegram_bot
    heartbeat.reset()


class BlockingTelegram(FakeTelegram):
    """getUpdates يحجب حتى stop_event (يحاكي long-poll) — يرجع 200 فارغاً عند الإيقاف."""

    def __init__(self, release):
        super().__init__([])
        self.release = release
        self.polls = 0

    def get(self, url, params=None, timeout=None, **kw):
        self.calls.append({"method": "GET", "url": url, "params": params, "timeout": timeout})
        if "getUpdates" in url:
            self.polls += 1
            self.release.wait(2)
            return FakeResp()
        return super().get(url, params=params, timeout=timeout, **kw)


def test_restart_poller_spawns_new_generation_and_old_thread_exits(env, monkeypatch):
    main, tb = env
    release = threading.Event()
    ft = BlockingTelegram(release).install(tb, monkeypatch)
    rt = main.Runtime("TOK", "777")
    t1 = rt.start_poller()
    time.sleep(0.2)
    assert tb.STATE["poller_generation"] == 1 and t1.is_alive()
    old_stop = rt.poller_stop
    assert rt.restart_poller() is True
    release.set()                      # الـ long-poll القديم يعود → يرى stop_event → يخرج
    t1.join(timeout=3)
    assert not t1.is_alive() and old_stop.is_set()
    time.sleep(0.2)
    assert tb.STATE["poller_generation"] == 2 and rt.poller_alive()
    assert ft.polls >= 2
    rt.shutdown("test")
    rt.poller_thread.join(timeout=3)
    assert not rt.poller_thread.is_alive()
    # لا زومبي: لا خيوط telegram-bot حية
    assert not [t for t in threading.enumerate() if t.name.startswith("telegram-bot")]


def test_restart_poller_refused_after_global_stop(env):
    main, tb = env
    rt = main.Runtime("TOK", "777")
    rt.global_stop.set()
    assert rt.restart_poller() is False


def test_shutdown_waits_for_in_flight_then_returns_zero(env, monkeypatch):
    main, tb = env
    monkeypatch.setattr(main.Config, "SHUTDOWN_GRACE", 5)
    rt = main.Runtime("TOK", "777")
    tb.STATE["in_flight"] = 1

    def finish_later():
        time.sleep(0.5)
        tb.STATE["in_flight"] = 0

    threading.Thread(target=finish_later, daemon=True).start()
    t0 = time.monotonic()
    assert rt.shutdown("test") == 0
    assert 0.4 <= time.monotonic() - t0 < 4
    assert rt.global_stop.is_set()


def test_shutdown_gives_up_after_grace_and_reports_left(env, monkeypatch, capsys):
    main, tb = env
    monkeypatch.setattr(main.Config, "SHUTDOWN_GRACE", 1)
    rt = main.Runtime("TOK", "777")
    tb.STATE["in_flight"] = 2
    t0 = time.monotonic()
    left = rt.shutdown("test")
    assert left == 2 and 0.9 <= time.monotonic() - t0 < 3
    assert "لم تكتمل" in capsys.readouterr().out
    tb.STATE["in_flight"] = 0


def test_signal_handler_sets_stop_event_only(env):
    main, _ = env
    ev = threading.Event()
    prev = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        installed = main.install_signal_handlers(ev)
        assert signal.SIGTERM in installed
        signal.raise_signal(signal.SIGTERM)   # عابر للمنصات: os.kill على Windows = TerminateProcess
        assert ev.wait(2)
    finally:
        for s, h in prev.items():
            signal.signal(s, h)


def test_start_watchdog_wires_poller_modes_and_notifier(env, monkeypatch):
    main, tb = env
    sent = []
    monkeypatch.setattr(tb, "send_telegram_message", lambda tok, cid, text, **kw: sent.append((cid, text)) or True)
    rt = main.Runtime("TOK", "777")
    w = rt.start_watchdog()
    try:
        assert w.is_alive() and rt.notifier is not None
        assert w._stall_after is tb.current_stall_after
        w._set_protect(True)
        assert tb.STATE["protect_mode"] is True
        w._set_protect(False)
        rt.notifier.notify("ping")
        for _ in range(30):
            if sent:
                break
            time.sleep(0.05)
        assert sent == [("777", "ping")]
    finally:
        rt.shutdown("test")


def test_idle_loop_reacts_to_stop_within_a_tick_not_watchdog_period(env, monkeypatch):
    """1b (كشفه اختبار الدخان): الخيط الرئيسي ينام بنبضات قصيرة كي لا يتأخر SIGTERM حتى 30 ث."""
    main, tb = env
    monkeypatch.setattr(main.Config, "WATCHDOG_INTERVAL", 10)      # الفترة الدورية 30 ث
    rt = main.Runtime("TOK", "777")
    threading.Timer(0.3, rt.global_stop.set).start()
    t0 = time.monotonic()
    assert main._idle_loop(rt, None, "TOK", tick=0.1) is None
    assert time.monotonic() - t0 < 2.0


def test_idle_loop_restarts_dead_poller_only_on_period(env, monkeypatch):
    main, tb = env
    monkeypatch.setattr(main.Config, "WATCHDOG_INTERVAL", 0.1)     # الفترة 0.3 ث
    rt = main.Runtime("TOK", "777")
    calls = []
    monkeypatch.setattr(rt, "poller_alive", lambda: False)
    monkeypatch.setattr(rt, "restart_poller", lambda: calls.append(1))
    threading.Timer(0.8, rt.global_stop.set).start()
    main._idle_loop(rt, None, "TOK", tick=0.05)
    assert 1 <= len(calls) <= 3                                      # لا كل 50 مللي ثانية


def test_main_applies_persisted_overrides_before_start_queue(env, monkeypatch):
    """T-09 (B-09): apply_persisted_overrides() تُستدعى قبل start_queue(resume=True) وبدء الويب (حين تفعيله)."""
    main, tb = env

    # 1) المسار الافتراضي: WEB_ENABLED = False -> الويب معطل
    order = []
    monkeypatch.setattr(main.Config, "WEB_ENABLED", False)
    monkeypatch.setattr(main.panel, "apply_persisted_overrides", lambda: order.append("overrides") or 0)
    monkeypatch.setattr(main.web_service, "start_web_server_thread", lambda: order.append("web") or None)
    monkeypatch.setattr(tb, "start_queue", lambda resume=False: order.append(("queue", resume)))
    monkeypatch.setattr(tb, "register_commands", lambda tok: None)
    monkeypatch.setattr(main.Runtime, "start_poller", lambda self: None)
    monkeypatch.setattr(main.Runtime, "start_watchdog", lambda self: None)
    monkeypatch.setattr(main, "_startup_ping", lambda *a, **k: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    monkeypatch.setattr(main, "_idle_loop", lambda rt, wt, tok: None)

    with pytest.raises(SystemExit) as ei:
        main.main()
    assert ei.value.code == 0
    assert order[0] == "overrides" and ("queue", True) in order
    assert "web" not in order
    assert order.index("overrides") < order.index(("queue", True))

    # 2) مسار WEB_ENABLED = True -> الويب يبدأ بعد overrides وقبل start_queue
    order.clear()
    monkeypatch.setattr(main.Config, "WEB_ENABLED", True)
    with pytest.raises(SystemExit) as ei:
        main.main()
    assert ei.value.code == 0
    assert order[0] == "overrides" and ("queue", True) in order
    assert order.index("overrides") < order.index("web") < order.index(("queue", True))


def test_shutdown_respects_bg_grace_and_reports_unfinished_bg(env, monkeypatch):
    """T-10 (B-09): shutdown() يمنح خيوط السبك حتى SHUTDOWN_BG_GRACE ويعيد left + bg_left."""
    import delivery
    main, tb = env
    monkeypatch.setattr(main.Config, "SHUTDOWN_GRACE", 5)
    monkeypatch.setattr(main.Config, "SHUTDOWN_BG_GRACE", 4.0)
    rt = main.Runtime("TOK", "777")

    ev_done = threading.Event()
    t = threading.Thread(target=lambda: ev_done.wait(2.0), daemon=True)
    t.start()
    with delivery._bg_threads_lock:
        delivery._bg_threads.add(t)
    threading.Timer(0.25, ev_done.set).start()
    try:
        assert rt.shutdown("test-ok") == 0
    finally:
        ev_done.set()
        t.join(1)
        with delivery._bg_threads_lock:
            delivery._bg_threads.discard(t)

    # خيط سبك عالق بعد انقضاء المهلة → يرجع العدد المتبقي > 0
    monkeypatch.setattr(main.Config, "SHUTDOWN_GRACE", 0.2)
    monkeypatch.setattr(main.Config, "SHUTDOWN_BG_GRACE", 0.2)
    rt2 = main.Runtime("TOK", "777")
    ev_stuck = threading.Event()
    t2 = threading.Thread(target=lambda: ev_stuck.wait(5.0), daemon=True)
    t2.start()
    with delivery._bg_threads_lock:
        delivery._bg_threads.add(t2)
    try:
        assert rt2.shutdown("test-stuck") >= 1
    finally:
        ev_stuck.set()
        t2.join(1)
        with delivery._bg_threads_lock:
            delivery._bg_threads.discard(t2)
