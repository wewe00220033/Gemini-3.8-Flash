# -*- coding: utf-8 -*-
"""
audit.py — قناة سجل التدقيق `logs/audit.log` (09 §2 القناة الثالثة، 11 §4 "كل تغيير → audit.log").

"من فعل ماذا ومتى": كل تغيير إداري (مفاتيح، إعدادات، أدوار، قاموس، جلسات) يُلحق سطراً واحداً:
    2026-10-26 03:14:05 | key.add | actor=telegram | name=Project 3 | key=AIza…****
- لا يُدوَّر (صغير) — لكن له سقف أمان AUDIT_MAX_BYTES: لو تجاوزه نُبقي النصف الأخير فقط.
- يمر على logkit.redact قبل الكتابة: لا مفتاح كامل ولا توكن يصل إلى القرص أبداً.
- فشل الكتابة لا يكسر العملية الأصلية (الإضافة/الحذف تمت فعلاً) — يُطبع تحذير فقط.
"""
import os
import threading
import time

from config import Config

_lock = threading.Lock()
AUDIT_MAX_BYTES = 2 * 1024 * 1024
_actor = threading.local()


def set_actor(name):
    """يحدد الفاعل للخيط الحالي (telegram | web | system | scheduler). None = system."""
    _actor.name = name


def _current_actor():
    return getattr(_actor, "name", None) or "system"


def _path():
    return Config.AUDIT_FILE


def _redact(text):
    try:
        import logkit
        return logkit.redact(text)
    except Exception:
        return text


def record(action, actor=None, **fields):
    """
    يلحق سطر تدقيق. action مثل: key.add / key.remove / settings.delivery_mode / glossary.add / role.set.
    fields: قيم None تُسقط؛ أسطر جديدة تُسطّح. يرجع السطر المكتوب (أو None عند الفشل).
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    parts = [ts, str(action), f"actor={actor or _current_actor()}"]
    for k, v in fields.items():
        if v is None:
            continue
        sv = str(v).replace("\n", " ").replace("\r", " ").strip()
        if len(sv) > 120:
            sv = sv[:117] + "..."
        parts.append(f"{k}={sv}")
    line = _redact(" | ".join(parts))
    try:
        with _lock:
            p = _path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            _trim_if_needed(p)
    except Exception as e:  # pragma: no cover
        print(f"⚠️ audit.log: تعذّر التسجيل ({e})")
        return None
    return line


def _trim_if_needed(p):
    """سقف أمان فقط (القناة لا تُدوَّر): فوق AUDIT_MAX_BYTES نُبقي آخر نصف الأسطر."""
    try:
        if os.path.getsize(p) <= AUDIT_MAX_BYTES:
            return
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        keep = lines[len(lines) // 2:]
        with open(p, "w", encoding="utf-8") as f:
            f.writelines(keep)
    except OSError:
        pass


def tail(n=20):
    """آخر n سطر (محجوبة أصلاً عند الكتابة؛ نحجب ثانية دفاعياً)."""
    p = _path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except OSError:
        return []
    return [_redact(ln) for ln in lines[-max(1, int(n)):]]


def size_bytes():
    p = _path()
    try:
        return os.path.getsize(p) if os.path.exists(p) else 0
    except OSError:
        return 0
