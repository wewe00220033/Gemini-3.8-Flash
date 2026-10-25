# -*- coding: utf-8 -*-
"""
clock.py — ساعة قابلة للحقن (13 §4.2، 17 المرحلة 0).

لماذا: اختبارات المهلات والنبضات يجب ألا تنام فعلياً. كل الكود الجديد يستدعي
clock.now() / clock.monotonic() / clock.sleep() بدل time.* مباشرة، والاختبار يبدّلها بـ FakeClock.

- now()       → وقت الحائط (للعرض والسجلات فقط).
- monotonic() → للنبضات والمهلات (D22: لا يتأثر بتعديل NTP لساعة السيرفر).
- sleep(s)    → قابل للمقاطعة في FakeClock.
"""
import time as _time


class _RealClock:
    def now(self):
        return _time.time()

    def monotonic(self):
        return _time.monotonic()

    def sleep(self, seconds):
        _time.sleep(max(0.0, seconds))


class FakeClock:
    """ساعة اختبار: الزمن لا يتحرك إلا بـ advance() أو sleep()."""

    def __init__(self, start_wall=1_700_000_000.0, start_mono=1000.0):
        self._wall = float(start_wall)
        self._mono = float(start_mono)
        self.sleeps = []

    def now(self):
        return self._wall

    def monotonic(self):
        return self._mono

    def sleep(self, seconds):
        seconds = max(0.0, float(seconds))
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds):
        self._wall += seconds
        self._mono += seconds


_impl = _RealClock()


def now():
    return _impl.now()


def monotonic():
    return _impl.monotonic()


def sleep(seconds):
    return _impl.sleep(seconds)


def use(impl):
    """يبدّل التنفيذ (للاختبارات). يعيد السابق كي يُستعاد."""
    global _impl
    prev = _impl
    _impl = impl
    return prev


def reset():
    use(_RealClock())
