# -*- coding: utf-8 -*-
"""
netsession — جلسات HTTP لكل خيط مع قتل الـ socket عند أي خطأ شبكي (03b الطبقتان 1 و2).

لماذا؟ حادثة 02: socket نصف مفتوح (half-open) بقي معلقاً داخل getUpdates إلى الأبد، ولا مهلة
تطبيقية تكشفه. الحل بثلاث طبقات مستقلة:
  1. Session لكل خيط (لا مشاركة pool بين الخيوط) + HTTPAdapter(max_retries=0) — لا إعادة محاولة
     صامتة داخل urllib3 تخفي الأعطال.
  2. TCP keepalive على الـ socket → الكيرنل نفسه يرمي استثناءً خلال ~50ث عند انقطاع الطرف الآخر.
  3. عند أي ReadTimeout/ConnectionError: `session.close()` يغلق الـ pool ويقتل الـ sockets فعلياً،
     ثم تُنشأ Session جديدة عند الطلب التالي. الخيط القديم لا يبقى زومبي.

وضع الحذر (CautiousMode): بعد أي ReadTimeout يهبط long-poll من 30 → 10ث لـ 5 دقائق، فالاكتشاف
التالي للتجمد ≤ 25ث بدل 45. يعود تلقائياً.

لا يستورد من المشروع سوى Config وclock (قابل للاختبار بـ FakeClock بلا شبكة).
"""
import socket
import threading

import requests
from requests.adapters import HTTPAdapter

from config import Config
import clock

_local = threading.local()
_lock = threading.Lock()
_stats = {"sessions_created": 0, "sessions_closed": 0}


# ----------------------------------------------------------------------
# keepalive adapter
# ----------------------------------------------------------------------
def keepalive_socket_options():
    """
    خيارات socket للـ keepalive: idle 20ث، interval 10ث، 3 محاولات → اكتشاف half-open خلال ~50ث.
    ترجع فقط الخيارات المتاحة على المنصة (Windows/macOS تفتقد بعض الثوابت → تُتجاهل بأمان).
    """
    opts = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    for name, val in (("TCP_KEEPIDLE", 20), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
        const = getattr(socket, name, None)
        if const is not None:
            opts.append((socket.IPPROTO_TCP, const, val))
    return opts


class KeepaliveAdapter(HTTPAdapter):
    """HTTPAdapter بلا إعادة محاولة داخلية + keepalive على كل socket جديد."""

    def __init__(self, keepalive=True, **kw):
        self._socket_options = keepalive_socket_options() if keepalive else None
        kw.setdefault("max_retries", 0)
        super().__init__(**kw)

    def init_poolmanager(self, *args, **kwargs):
        if self._socket_options:
            kwargs["socket_options"] = self._socket_options
        return super().init_poolmanager(*args, **kwargs)


def new_session(keepalive=None):
    """Session جديدة مهيأة (adapter واحد لـ http و https)."""
    if keepalive is None:
        keepalive = Config.TCP_KEEPALIVE
    s = requests.Session()
    adapter = KeepaliveAdapter(keepalive=keepalive)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    with _lock:
        _stats["sessions_created"] += 1
    return s


# ----------------------------------------------------------------------
# Session لكل خيط
# ----------------------------------------------------------------------
def get():
    """Session الخيط الحالي (تُنشأ عند أول طلب أو بعد reset())."""
    s = getattr(_local, "session", None)
    if s is None:
        s = new_session()
        _local.session = s
    return s


def reset(reason=None):
    """
    يقتل Session الخيط الحالي (يغلق pool + sockets). تُستدعى عند أي خطأ شبكي.
    الطلب التالي من هذا الخيط يحصل على Session نظيفة تلقائياً.
    """
    s = getattr(_local, "session", None)
    _local.session = None
    if s is not None:
        try:
            s.close()
        except Exception:
            pass
        with _lock:
            _stats["sessions_closed"] += 1
            _stats["last_reset_reason"] = reason
    return s is not None


def stats():
    with _lock:
        return dict(_stats)


NETWORK_ERRORS = (requests.exceptions.RequestException, OSError)


def request(method, url, **kw):
    """
    غلاف: يستخدم Session الخيط، ويعيد تعيينها عند أي خطأ شبكي ثم يرفع الخطأ نفسه للمنادي.
    يفرض وجود timeout (03b §2 بند 3: لا نداء خارجي بلا مهلة).
    """
    if "timeout" not in kw or kw["timeout"] is None:
        kw["timeout"] = (Config.TG_CONNECT_TIMEOUT, Config.TELEGRAM_TIMEOUT)
    try:
        return get().request(method, url, **kw)
    except NETWORK_ERRORS as e:
        reset(reason=f"{type(e).__name__}")
        raise


def get_url(url, **kw):
    return request("GET", url, **kw)


def post(url, **kw):
    return request("POST", url, **kw)


# ----------------------------------------------------------------------
# وضع الحذر + backoff شبكي
# ----------------------------------------------------------------------
class CautiousMode:
    """
    بعد ReadTimeout: poll أقصر لمدة CAUTIOUS_MODE_MINUTES. monotonic (D22) عبر clock.
    `poll_timeout()` و`read_timeout()` يعطيان القيم الحالية لحلقة الـ polling؛
    `stall_after()` هو عتبة الـ watchdog المتوافقة (poll + margin + STALL_MARGIN).
    """

    def __init__(self):
        self._until = 0.0
        self._lock = threading.Lock()
        self.entries = 0

    def enter(self):
        with self._lock:
            self._until = clock.monotonic() + Config.CAUTIOUS_MODE_MINUTES * 60
            self.entries += 1

    def active(self):
        with self._lock:
            return clock.monotonic() < self._until

    def remaining_s(self):
        with self._lock:
            return max(0.0, self._until - clock.monotonic())

    def poll_timeout(self):
        return Config.TG_POLL_TIMEOUT_CAUTIOUS if self.active() else Config.TG_POLL_TIMEOUT

    def read_timeout(self):
        return self.poll_timeout() + Config.TG_READ_MARGIN

    def timeouts(self):
        """tuple (connect, read) للـ long-poll الحالي."""
        return (Config.TG_CONNECT_TIMEOUT, self.read_timeout())

    def stall_after(self):
        return self.read_timeout() + Config.STALL_MARGIN


class NetBackoff:
    """backoff أُسّي 3→6→12→30 (سقف) لأخطاء الشبكة المتتالية؛ يصفر عند أول نجاح."""

    def __init__(self, ladder=None):
        self._ladder = tuple(ladder or Config.NET_BACKOFF)
        self.failures = 0

    def next_delay(self):
        idx = min(self.failures, len(self._ladder) - 1)
        self.failures += 1
        return self._ladder[idx]

    def succeed(self):
        self.failures = 0

    @property
    def degraded(self):
        """True بعد أول فشلين متتاليين (حدث net_degraded يُسجَّل مرة واحدة من المنادي)."""
        return self.failures >= 2


cautious = CautiousMode()
