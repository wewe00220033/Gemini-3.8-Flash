# -*- coding: utf-8 -*-
"""
heartbeat.py — سجل نبضات الخيوط (03b الطبقة 3، 17 §1a، G26).

كل خيط حيوي (poller / worker / watchdog / notifier / web) ينادي beat(name) دورياً.
age(name) يعطي عمر آخر نبضة بالـ monotonic — هذا ما يقرأه /health العميق والـ watchdog،
لا is_alive() الذي يقول «حي» لخيط معلق على socket ميت (الحادث 02).

لا شبكة، لا ملفات، لا اعتماد على وحدات المشروع غير clock.
"""
import threading

import clock

_lock = threading.Lock()
_beats = {}       # name -> monotonic of last beat
_meta = {}        # name -> dict(count, last_wall, note)


def beat(name, note=None):
    """تسجيل نبضة. note نص قصير اختياري (مثل آخر مرحلة وصل لها الخيط)."""
    m = clock.monotonic()
    w = clock.now()
    with _lock:
        _beats[name] = m
        meta = _meta.setdefault(name, {"count": 0, "last_wall": None, "note": None})
        meta["count"] += 1
        meta["last_wall"] = w
        if note is not None:
            meta["note"] = note


def age(name):
    """عمر آخر نبضة بالثواني، أو None لو لم ينبض الخيط قط."""
    with _lock:
        m = _beats.get(name)
    if m is None:
        return None
    return max(0.0, clock.monotonic() - m)


def is_stale(name, max_age):
    """True لو لم ينبض خلال max_age ثانية (أو لم ينبض قط)."""
    a = age(name)
    return a is None or a > max_age


def snapshot():
    """لقطة للعرض في /health و/status: {name: {age_s, count, last_wall, note}}."""
    out = {}
    with _lock:
        names = list(_beats.keys())
    for n in names:
        with _lock:
            meta = dict(_meta.get(n, {}))
        a = age(n)
        out[n] = {
            "age_s": None if a is None else round(a, 1),
            "count": meta.get("count", 0),
            "last_wall": meta.get("last_wall"),
            "note": meta.get("note"),
        }
    return out


def forget(name):
    with _lock:
        _beats.pop(name, None)
        _meta.pop(name, None)


def reset():
    """للاختبارات."""
    with _lock:
        _beats.clear()
        _meta.clear()
