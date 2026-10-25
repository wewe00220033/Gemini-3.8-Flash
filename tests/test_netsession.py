# -*- coding: utf-8 -*-
"""اختبارات netsession (03b الطبقتان 1 و2): Session لكل خيط، إغلاق عند الخطأ، وضع الحذر، backoff."""
import threading

import pytest
import requests

import clock
import netsession as ns


@pytest.fixture
def fake_clock():
    fc = clock.FakeClock()
    clock.use(fc)
    try:
        yield fc
    finally:
        clock.reset()


@pytest.fixture(autouse=True)
def _clean_sessions():
    ns.reset("test-setup")
    yield
    ns.reset("test-teardown")


def test_session_is_per_thread_and_reused():
    s1 = ns.get()
    assert ns.get() is s1
    other = {}

    def worker():
        other["s"] = ns.get()
        ns.reset("done")

    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert other["s"] is not s1


def test_adapter_has_no_internal_retries_and_keepalive():
    s = ns.new_session(keepalive=True)
    ad = s.get_adapter("https://api.telegram.org")
    assert isinstance(ad, ns.KeepaliveAdapter)
    assert ad.max_retries.total == 0
    opts = ns.keepalive_socket_options()
    assert opts[0][1] == __import__("socket").SO_KEEPALIVE
    s.close()


def test_network_error_closes_session_and_reraises(monkeypatch):
    s = ns.get()
    closed = {"n": 0}
    orig_close = s.close

    def close():
        closed["n"] += 1
        orig_close()

    monkeypatch.setattr(s, "close", close)

    def boom(method, url, **kw):
        raise requests.exceptions.ReadTimeout("read timed out")

    monkeypatch.setattr(s, "request", boom)
    before = ns.stats()["sessions_closed"]
    with pytest.raises(requests.exceptions.ReadTimeout):
        ns.get_url("https://api.telegram.org/x", timeout=(1, 1))
    assert closed["n"] == 1
    assert ns.stats()["sessions_closed"] == before + 1
    assert ns.stats()["last_reset_reason"] == "ReadTimeout"
    # الطلب التالي يحصل على Session جديدة (لا زومبي)
    assert ns.get() is not s


def test_request_always_has_timeout(monkeypatch):
    seen = {}
    s = ns.get()
    monkeypatch.setattr(s, "request", lambda m, u, **kw: seen.update(kw) or object())
    ns.post("https://api.telegram.org/x", json={})
    assert isinstance(seen["timeout"], tuple) and len(seen["timeout"]) == 2


def test_cautious_mode_lowers_poll_then_expires(fake_clock):
    cm = ns.CautiousMode()
    from config import Config
    assert cm.poll_timeout() == Config.TG_POLL_TIMEOUT
    assert cm.stall_after() == Config.TG_POLL_TIMEOUT + Config.TG_READ_MARGIN + Config.STALL_MARGIN  # ≈55
    cm.enter()
    assert cm.active() and cm.poll_timeout() == Config.TG_POLL_TIMEOUT_CAUTIOUS
    assert cm.stall_after() == Config.TG_POLL_TIMEOUT_CAUTIOUS + Config.TG_READ_MARGIN + Config.STALL_MARGIN  # ≈35
    assert cm.timeouts() == (Config.TG_CONNECT_TIMEOUT, Config.TG_POLL_TIMEOUT_CAUTIOUS + Config.TG_READ_MARGIN)
    fake_clock.advance(Config.CAUTIOUS_MODE_MINUTES * 60 + 1)
    assert not cm.active() and cm.poll_timeout() == Config.TG_POLL_TIMEOUT
    assert cm.entries == 1


def test_net_backoff_ladder_and_reset():
    b = ns.NetBackoff(ladder=(3, 6, 12, 30))
    assert [b.next_delay() for _ in range(6)] == [3, 6, 12, 30, 30, 30]
    assert b.degraded
    b.succeed()
    assert b.failures == 0 and not b.degraded
    assert b.next_delay() == 3
