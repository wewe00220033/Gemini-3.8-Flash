# -*- coding: utf-8 -*-
"""
إعدادات المالك القابلة للتغيير من البوت (settings.json) — المرحلة 3.

- `delivery_mode`: dual | raw_only | polished_only (07 §1 — عام، يبقى بعد restart).
- `role`: معرّف الدور الحالي (06 §2 — عام؛ تجاوز لكل جلسة يأتي في المرحلة 4).
- `header_target`: both | raw_only | polished_only | off (06 §4).
Thread-safe، كتابة ذرية، ملف تالف → الافتراضي من Config (لا انهيار).
"""

import threading

from config import Config
import store

DELIVERY_MODES = ("dual", "raw_only", "polished_only")
HEADER_TARGETS = ("both", "raw_only", "polished_only", "off")
_MODE_ALIASES = {"dual": "dual", "both": "dual", "raw": "raw_only", "raw_only": "raw_only",
                 "polished": "polished_only", "polished_only": "polished_only", "خام": "raw_only",
                 "منظم": "polished_only", "الاتنين": "dual", "الاثنين": "dual"}

_lock = threading.RLock()


def _audit(action, **fields):
    """09 §2 قناة audit: لا تكسر العملية أبداً لو فشل التسجيل."""
    try:
        import audit
        audit.record(action, **fields)
    except Exception:
        pass
_cache = None
_cache_path = None


def _defaults():
    return {
        "schema_version": 1,
        "delivery_mode": Config.DELIVERY_MODE_DEFAULT if Config.DELIVERY_MODE_DEFAULT in DELIVERY_MODES else "dual",
        "role": Config.DEFAULT_ROLE,
        "header_target": Config.TRANSCRIPT_HEADER_TARGET if Config.TRANSCRIPT_HEADER_ENABLED else "off",
    }


def _load():
    global _cache, _cache_path
    if _cache is not None and _cache_path == Config.SETTINGS_FILE:
        return _cache
    data = store.load_json_tolerant(Config.SETTINGS_FILE, default={})
    merged = _defaults()
    if isinstance(data, dict):
        for k in ("delivery_mode", "role", "header_target"):
            if isinstance(data.get(k), str) and data[k]:
                merged[k] = data[k]
    if merged["delivery_mode"] not in DELIVERY_MODES:
        merged["delivery_mode"] = "dual"
    if merged["header_target"] not in HEADER_TARGETS:
        merged["header_target"] = "both"
    _cache, _cache_path = merged, Config.SETTINGS_FILE
    return _cache


def _save():
    try:
        store.atomic_write_json(Config.SETTINGS_FILE, _cache)
    except Exception as e:  # pragma: no cover
        print(f"⚠️ settings.json: تعذّر الحفظ ({e})")


def get(key, default=None):
    with _lock:
        return _load().get(key, default)


def set_value(key, value):
    with _lock:
        old = _load().get(key)
        _load()[key] = value
        _save()
    if old != value:
        _audit(f"settings.{key}", old=old, new=value)
    return value


def reset_cache():
    """للاختبارات: يجبر إعادة القراءة من القرص."""
    global _cache, _cache_path
    with _lock:
        _cache, _cache_path = None, None


def delivery_mode():
    return get("delivery_mode", "dual")


def set_delivery_mode(mode):
    """يقبل dual/raw/polished ومرادفات عربية. يرجع الوضع المعتمد أو None لو غير صالح."""
    m = _MODE_ALIASES.get((mode or "").strip().lower())
    if not m:
        return None
    return set_value("delivery_mode", m)


def role():
    return get("role", Config.DEFAULT_ROLE)


def effective_role():
    """الدور الفعّال: تجاوز الجلسة الحالية (06 §2) إن وُجد، وإلا الدور العام."""
    try:
        import sessions
        ov = sessions.role_override()
        if ov:
            return ov
    except Exception:
        pass
    return role()


def set_role(role_id):
    return set_value("role", role_id)


def header_target():
    return get("header_target", "both")


def set_header_target(target):
    t = (target or "").strip().lower()
    t = {"raw": "raw_only", "polished": "polished_only"}.get(t, t)
    if t not in HEADER_TARGETS:
        return None
    return set_value("header_target", t)


def header_for(kind):
    """هل يُضاف البانر لرسالة من نوع kind ('raw' | 'polished')؟ (06 §8 — 3 حالات)"""
    t = header_target()
    if t == "off":
        return False
    return t == "both" or t == f"{kind}_only"
