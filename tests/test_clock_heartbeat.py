# -*- coding: utf-8 -*-
import pytest

import clock
import heartbeat


@pytest.fixture
def fake_clock():
    fc = clock.FakeClock()
    prev = clock.use(fc)
    heartbeat.reset()
    yield fc
    clock.use(prev)
    heartbeat.reset()


def test_fake_clock_sleep_advances_both_clocks_without_blocking(fake_clock):
    w0, m0 = clock.now(), clock.monotonic()
    clock.sleep(42.5)
    assert clock.now() - w0 == pytest.approx(42.5)
    assert clock.monotonic() - m0 == pytest.approx(42.5)
    assert fake_clock.sleeps == [42.5]


def test_heartbeat_age_and_stale(fake_clock):
    assert heartbeat.age("poller") is None
    assert heartbeat.is_stale("poller", 10)
    heartbeat.beat("poller", note="getUpdates ok")
    assert heartbeat.age("poller") == pytest.approx(0.0)
    fake_clock.advance(9)
    assert not heartbeat.is_stale("poller", 10)
    fake_clock.advance(2)
    assert heartbeat.is_stale("poller", 10)


def test_heartbeat_uses_monotonic_not_wall(fake_clock):
    heartbeat.beat("worker")
    # NTP يرجّع ساعة الحائط 1 ساعة للخلف — النبض لا يتأثر
    fake_clock._wall -= 3600
    assert heartbeat.age("worker") == pytest.approx(0.0)


def test_snapshot_shape(fake_clock):
    heartbeat.beat("a")
    heartbeat.beat("a", note="x")
    fake_clock.advance(3)
    snap = heartbeat.snapshot()
    assert set(snap) == {"a"}
    assert snap["a"]["count"] == 2 and snap["a"]["note"] == "x"
    assert snap["a"]["age_s"] == pytest.approx(3.0)
    heartbeat.forget("a")
    assert heartbeat.snapshot() == {}


def test_real_clock_restored():
    clock.reset()
    import time
    assert abs(clock.now() - time.time()) < 1
