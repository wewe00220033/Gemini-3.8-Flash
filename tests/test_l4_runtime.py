# -*- coding: utf-8 -*-
"""
اختبارات الطبقة 4 (1c) في watchdog/main: L4 مسلّح فقط مع request_exit (SUPERVISED=1)؛ غير مسلّح → إشعار واحد
ويبقى في L3؛ Runtime.request_exit يكتب restart_reason.json ويضبط exit_code=3؛ العامل الجديد يقرأ السبب ويحذفه؛
نبض العامل يُكتب فقط تحت المشرف؛ entry() يختار main() أو المشرف حسب SUPERVISED/--worker.
"""
import importlib
import json
import os
import threading
import time

import pytest

import clock
import heartbeat
import watchdog as wd
from config import Config


@pytest.fixture
def fc():
    fc = clock.FakeClock()
    clock.use(fc)
    heartbeat.reset()
    try:
        yield fc
    finally:
        clock.reset()
        heartbeat.reset()


class FakeNotifier:
    def __init__(self):
        self.msgs = []

    def notify(self, text):
        self.msgs.append(text)


def _harness(request_exit=None, notifier=None):
    return wd.Watchdog(restart_poller=lambda: True, stall_after=lambda: 55, notifier=notifier,
                       set_protect=lambda on: None, set_emergency=lambda on: None, interval=10,
                       request_exit=request_exit)


def _drive_l1s(w, fc, n, step=60):
    """n دورات stall متتالية (كل 60ث) → n × L1 (L3/L3b في الطريق)."""
    heartbeat.beat("poller")
    out = []
    for _ in range(n):
        fc.advance(step)
        out.append(w.check())
    return out


def test_l4_armed_fires_once_after_six_l1_within_window(fc):
    exits = []
    w = _harness(request_exit=exits.append)
    levels = _drive_l1s(w, fc, Config.EXIT_AFTER_RESTARTS)
    assert levels[-1] == wd.L4_PROCESS_EXIT and len(exits) == 1 and "× L1" in exits[0]
    assert w.l4_requested is True and w.snapshot()["l4_armed"] is True
    # لا يتكرر الطلب في الدورة التالية
    fc.advance(60)
    assert w.check() != wd.L4_PROCESS_EXIT and len(exits) == 1


def test_l4_unarmed_stays_in_protection_and_notifies_owner_once(fc):
    n = FakeNotifier()
    w = _harness(request_exit=None, notifier=n)
    _drive_l1s(w, fc, Config.EXIT_AFTER_RESTARTS + 2)
    assert w.l4_requested is False and w.level in ("L3", "L3b")
    l4_msgs = [m for m in n.msgs if "SUPERVISED=1" in m]
    assert len(l4_msgs) == 1                                     # إشعار واحد فقط (الصمت مطلوب)
    assert w.snapshot()["l4_armed"] is False


def test_l4_by_continuous_stall_over_five_minutes(fc, monkeypatch):
    """الشرط الثاني: stall مستمر > L4_STALL_SECONDS رغم L1 (حتى لو العدد < 6)."""
    monkeypatch.setattr(wd.Config, "EXIT_AFTER_RESTARTS", 99)
    exits = []
    w = _harness(request_exit=exits.append)
    heartbeat.beat("poller")
    fired = None
    for _ in range(8):                                           # 8 × 60ث = 480ث > 300
        fc.advance(60)
        if w.check() == wd.L4_PROCESS_EXIT:
            fired = True
            break
    assert fired and "stall مستمر" in exits[0]


def test_l4_not_triggered_by_old_scattered_l1s(fc):
    """6 × L1 لكن موزعة على ساعات → ليست سلسلة ولا داخل نافذة 15 د → لا L4."""
    exits = []
    w = _harness(request_exit=exits.append)
    for _ in range(6):
        heartbeat.beat("poller")
        fc.advance(60 * 40)                                      # 40 دقيقة بين كل stall
        w.check()
    assert exits == [] and w.l4_requested is False


@pytest.fixture
def env(fresh_env, monkeypatch, tmp_path):
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    monkeypatch.setattr(telegram_bot.key_manager, "get_telegram_config", lambda: ("TOK", "bot", "777"))
    import main
    importlib.reload(main)
    main.telegram_bot = telegram_bot
    state = tmp_path / "state"
    monkeypatch.setattr(main.Config, "RESTART_REASON_FILE", str(state / "restart_reason.json"))
    monkeypatch.setattr(main.Config, "WORKER_BEAT_FILE", str(state / "worker.beat"))
    monkeypatch.setattr(main.Config, "WORKER_BEAT_INTERVAL", 0.05)
    monkeypatch.delenv("SUPERVISED_CHILD", raising=False)
    monkeypatch.delenv("WORKER_GENERATION", raising=False)
    heartbeat.reset()
    yield main, telegram_bot, state
    heartbeat.reset()


def test_runtime_request_exit_writes_reason_and_sets_exit_code_3(env):
    main, tb, state = env
    rt = main.Runtime("TOK", "777")
    rt.request_exit("6 × L1 خلال 15 د")
    assert rt.exit_code == 3 and rt.global_stop.is_set()
    data = json.load(open(state / "restart_reason.json", encoding="utf-8"))
    assert data["reason"] == "L4" and "6 × L1" in data["detail"]
    # العامل الجديد يقرأ السبب للمالك ويحذف الملف
    notice = main._restart_notice()
    assert "أعدت تشغيل نفسي" in notice and "6 × L1" in notice
    assert not (state / "restart_reason.json").exists() and main._restart_notice() == ""


def test_watchdog_armed_only_when_supervised(env, monkeypatch):
    main, tb, state = env
    monkeypatch.setattr(main.Config, "SUPERVISED", False)
    rt = main.Runtime("TOK", "777")
    w = rt.start_watchdog()
    try:
        assert w.snapshot()["l4_armed"] is False
    finally:
        rt.shutdown(reason="test")
    monkeypatch.setattr(main.Config, "SUPERVISED", True)
    rt2 = main.Runtime("TOK", "777")
    w2 = rt2.start_watchdog()
    try:
        assert w2.snapshot()["l4_armed"] is True
    finally:
        rt2.shutdown(reason="test")


def test_worker_beat_only_under_supervisor(env, monkeypatch):
    main, tb, state = env
    rt = main.Runtime("TOK", "777")
    assert rt.start_worker_beat() is None and not (state / "worker.beat").exists()
    monkeypatch.setenv("SUPERVISED_CHILD", "1")
    rt2 = main.Runtime("TOK", "777")
    t = rt2.start_worker_beat()
    assert t is not None and (state / "worker.beat").exists()
    m1 = os.path.getmtime(state / "worker.beat")
    time.sleep(0.2)
    assert os.path.getmtime(state / "worker.beat") >= m1
    rt2._beat_stop.set()
    t.join(1)


def test_entry_dispatch(env, monkeypatch):
    main, tb, state = env
    calls = []
    monkeypatch.setattr(main, "main", lambda: calls.append("main"))

    class FakeSup:
        def run(self):
            calls.append("supervisor")
            return 0

    monkeypatch.setattr(main.supervisor, "Supervisor", FakeSup)
    monkeypatch.setattr(main.Config, "SUPERVISED", False)
    main.entry([])
    assert calls == ["main"]                                     # الافتراضي: لا مشرف إطلاقاً
    monkeypatch.setattr(main.Config, "SUPERVISED", True)
    main.entry(["--worker", "--generation", "2"])
    assert calls == ["main", "main"]                             # العامل تحت المشرف
    with pytest.raises(SystemExit) as ei:
        main.entry([])
    assert ei.value.code == 0 and calls[-1] == "supervisor"


def test_l4_end_to_end_runtime_exits_with_3_after_graceful_shutdown(env, monkeypatch):
    """سلسلة كاملة: watchdog مسلّح → request_exit → الخيط الرئيسي يخرج من _idle_loop → shutdown → كود 3."""
    main, tb, state = env
    monkeypatch.setattr(main.Config, "SUPERVISED", True)
    monkeypatch.setattr(main.Config, "SHUTDOWN_GRACE", 2)
    rt = main.Runtime("TOK", "777")
    threading.Timer(0.3, lambda: rt.request_exit("اختبار L4")).start()
    main._idle_loop(rt, None, "TOK", tick=0.05)
    assert rt.global_stop.is_set()
    left = rt.shutdown(reason=f"L4 ({rt.exit_reason})")
    assert left == 0 and rt.exit_code == 3
    assert json.load(open(state / "restart_reason.json", encoding="utf-8"))["detail"] == "اختبار L4"
