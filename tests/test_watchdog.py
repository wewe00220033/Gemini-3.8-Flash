# -*- coding: utf-8 -*-
"""اختبارات الـ watchdog (03b الطبقة 3): سلم L1→L3→L3b→تعافٍ بـ FakeClock بلا خيوط ولا شبكة."""
import threading

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


class Harness:
    def __init__(self, notifier=None, stall=55):
        self.restarts = 0
        self.protect = []
        self.emergency = []
        self.stall = stall
        self.w = wd.Watchdog(
            restart_poller=self._restart,
            stall_after=lambda: self.stall,
            notifier=notifier,
            set_protect=self.protect.append,
            set_emergency=self.emergency.append,
            interval=10,
        )

    def _restart(self):
        self.restarts += 1
        return True


def test_no_action_while_poller_beats(fc):
    h = Harness()
    heartbeat.beat("poller")
    fc.advance(50)
    assert h.w.check() is None and h.restarts == 0
    assert heartbeat.age("watchdog") == 0      # الـ watchdog ينبض بنفسه


def test_l1_restart_when_stalled_then_grace(fc):
    """MTTD ≤ 60ث: stall 56ث → L1 فوراً؛ لا L1 ثانٍ قبل مرور العتبة."""
    h = Harness()
    heartbeat.beat("poller")
    fc.advance(56)
    assert h.w.check() == wd.L1_POLLER_RESTART and h.restarts == 1
    fc.advance(20)
    assert h.w.check() is None and h.restarts == 1       # مهلة سماح للخيط الجديد
    assert h.w.snapshot()["restarts"] == 1 and h.w.snapshot()["level"] is None


def test_three_l1_in_window_enters_protect_with_single_notice(fc):
    sent = []
    n = wd.Notifier(lambda t: sent.append(t) or True)
    h = Harness(notifier=n)
    heartbeat.beat("poller")
    results = []
    for _ in range(3):
        fc.advance(60)
        results.append(h.w.check())
    assert results == ["L1", "L1", "L3"]
    assert h.protect == [True]
    assert n.pending() == 1
    assert n.drain_once() and len(sent) == 1 and "وضع آمن" in sent[0]
    # L1 رابع لا يرسل إشعاراً ثانياً بل يصعّد لـ L3b
    fc.advance(60)
    assert h.w.check() == wd.L3B_EMERGENCY
    assert h.emergency == [True] and n.pending() == 0
    snap = h.w.snapshot()
    assert snap["level"] == "L3b" and snap["emergency_remaining_s"] == Config.EMERGENCY_SHORT_POLL_SECONDS


def test_l3b_expires_back_to_l3_then_full_recovery(fc):
    h = Harness()
    heartbeat.beat("poller")
    for _ in range(4):
        fc.advance(60)
        h.w.check()
    assert h.w.level == "L3b"
    # انتهاء الطوارئ مع عودة النبض → L3
    fc.advance(Config.EMERGENCY_SHORT_POLL_SECONDS + 1)
    heartbeat.beat("poller")
    assert h.w.check() is None
    assert h.w.level == "L3" and h.emergency[-1] is False
    # النافذة تمر بلا L1 جديد → تعافٍ كامل وإطفاء الحماية
    fc.advance(Config.PROTECT_WINDOW_MINUTES * 60 + 1)
    heartbeat.beat("poller")
    assert h.w.check() is None
    assert h.w.level is None and h.protect[-1] is False and h.w.protect_notified is False


def test_restart_failure_is_recorded_not_raised(fc):
    def boom():
        raise RuntimeError("thread spawn failed")
    w = wd.Watchdog(restart_poller=boom, stall_after=lambda: 55, interval=10)
    heartbeat.beat("poller")
    fc.advance(60)
    assert w.check() == "L1"
    assert "فشل إعادة التشغيل" in w.events[-1][2] and "❌" in w.events[-1][2]


def test_notifier_thread_sends_and_beats():
    heartbeat.reset()
    got = []
    done = threading.Event()

    def send(t):
        got.append(t)
        done.set()
        return True

    n = wd.Notifier(send)
    n.start()
    n.notify("hello")
    assert done.wait(3)
    n.stop()
    assert got == ["hello"] and n.sent == 1
    assert "notifier" in heartbeat.snapshot()
    heartbeat.reset()


def test_watchdog_thread_runs_and_stops_without_zombies():
    heartbeat.reset()
    before = threading.active_count()
    w = wd.Watchdog(restart_poller=lambda: True, stall_after=lambda: 55, interval=0.05)
    heartbeat.beat("poller")
    w.start()
    import time
    time.sleep(0.2)
    assert w.is_alive() and heartbeat.snapshot()["watchdog"]["count"] >= 2
    w.stop()
    assert not w.is_alive() and threading.active_count() == before
    heartbeat.reset()
