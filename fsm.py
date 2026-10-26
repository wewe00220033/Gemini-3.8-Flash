# -*- coding: utf-8 -*-
"""
fsm.py — آلة حالات خفيفة لإدخال النصوص متعدد الخطوات (11 §3).

- حالة واحدة فقط (مستخدم واحد) في الذاكرة — لا قرص.
- `FSM_TTL_SECONDS` (300): انتهاء تلقائي؛ `expired_and_clear()` يُعلم المتصل ليرسل «انتهت المهلة» مرة واحدة.
- الفويس أثناء الحالة يُعالج طبيعياً ولا يمسّها: الحالة تنتظر **نصاً** فقط (telegram_bot يستشير fsm في مسار النص وحده).
- `/cancel` في أي وقت → `clear()`.
الحالات: await_key, await_word, await_word_dst, await_session_name, await_role, await_set_value (+ data سياقية).
"""
import threading

import clock
from config import Config

_lock = threading.Lock()
_state = {"name": None, "data": None, "since": 0.0, "chat_id": None, "prompt_msg_id": None}

STATES = ("await_key", "await_word", "await_word_dst", "await_session_name", "await_role", "await_set_value", "await_key_rename")


def enter(name, data=None, chat_id=None, prompt_msg_id=None):
    """يدخل حالة جديدة (يستبدل أي حالة سابقة — حالة واحدة فقط). يرجع dict الحالة."""
    if name not in STATES:
        raise ValueError(f"حالة غير معروفة: {name}")
    with _lock:
        _state.update({"name": name, "data": dict(data or {}), "since": clock.now(), "chat_id": chat_id, "prompt_msg_id": prompt_msg_id})
        return dict(_state)


def clear():
    """يمسح الحالة. يرجع الحالة السابقة (أو None)."""
    with _lock:
        prev = dict(_state) if _state["name"] else None
        _state.update({"name": None, "data": None, "since": 0.0, "chat_id": None, "prompt_msg_id": None})
        return prev


def current():
    """الحالة الحالية إن كانت سارية؛ None لو لا حالة أو انتهت مهلتها (دون مسح — استخدم expired_and_clear)."""
    with _lock:
        if not _state["name"]:
            return None
        if clock.now() - _state["since"] > Config.FSM_TTL_SECONDS:
            return None
        return dict(_state)


def expired_and_clear():
    """لو هناك حالة انتهت مهلتها: يمسحها ويرجعها (ليُرسل «انتهت المهلة» مرة واحدة). وإلا None."""
    with _lock:
        if _state["name"] and clock.now() - _state["since"] > Config.FSM_TTL_SECONDS:
            prev = dict(_state)
            _state.update({"name": None, "data": None, "since": 0.0, "chat_id": None, "prompt_msg_id": None})
            return prev
        return None


def active():
    return current() is not None


def remaining_s():
    st = current()
    if not st:
        return 0
    return max(0, int(Config.FSM_TTL_SECONDS - (clock.now() - st["since"])))


LABELS = {
    "await_key": "إضافة مفتاح Gemini",
    "await_key_rename": "إعادة تسمية مفتاح",
    "await_word": "إضافة كلمة للقاموس",
    "await_word_dst": "المقصود من الكلمة",
    "await_session_name": "اسم الجلسة",
    "await_role": "دور جديد",
    "await_set_value": "قيمة الإعداد",
}
