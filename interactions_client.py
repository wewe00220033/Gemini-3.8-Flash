# -*- coding: utf-8 -*-
"""
عميل Interactions API (Google Antigravity) — 12 §2، معزول بالكامل.
- POST https://generativelanguage.googleapis.com/v1beta/interactions
  بجسم {"agent": AGENT_NAME, "environment": "remote", "input": "<prompt>"} — **نص فقط** (الصوت → 400 كما اختبر المالك).
- مهلة AGENT_TIMEOUT (180 ث) — النداء في خيط خلفي من telegram_bot؛ هنا نداء متزامن واحد.
- تحليل مرن للرد (v1beta/preview قد يتغير): نبحث عن أول نص في output/outputs/candidates/text/content…
- أي فشل → (None, reason) واضح؛ لا استثناء غير معالج يخرج من هنا أبداً. لا failover (100 RPD وفيرة).
- يستخدم مفاتيح key_manager نفسها (أول مفتاح متاح)؛ 429 يومي يُعلَّم على «موديل» افتراضي مستقل حتى لا يمس حصة التفريغ.
- ممنوع للتفريغ: assert_not_transcription() — اختبار يضمن أن اسم الوكيل ليس في Config.MODELS.
"""

import threading
import time

import requests

from config import Config
from key_manager import key_manager

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
AGENT_QUOTA_MODEL = "antigravity"      # اسم داخلي لعدّاد الحصة في key_manager (ليس موديل تفريغ)

_lock = threading.Lock()
_state = {"calls": 0, "ok": 0, "fail": 0, "last_error": None, "last_ms": None}


def assert_not_transcription():
    if Config.AGENT_NAME in Config.MODELS or AGENT_QUOTA_MODEL in Config.MODELS:
        raise RuntimeError("Antigravity ممنوع للتفريغ — AGENT_NAME موجود في Config.MODELS")


def build_payload(prompt):
    return {"agent": Config.AGENT_NAME, "environment": Config.AGENT_ENVIRONMENT, "input": str(prompt)}


# ======================================================================
# تحليل مرن للرد
# ======================================================================
_TEXT_KEYS = ("text", "output_text", "content", "response", "result", "message", "answer")
_LIST_KEYS = ("output", "outputs", "candidates", "parts", "items", "messages", "events", "steps")


def extract_text(obj, _depth=0):
    """يستخرج أول نص «نهائي» معقول من بنية JSON غير معروفة الشكل. يرجع '' لو لا شيء."""
    if _depth > 8 or obj is None:
        return ""
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, list):
        texts = [extract_text(x, _depth + 1) for x in obj]
        texts = [t for t in texts if t]
        return "\n".join(texts) if texts else ""
    if isinstance(obj, dict):
        # رسالة خطأ صريحة
        if "error" in obj and isinstance(obj["error"], dict):
            return ""
        for k in _TEXT_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for k in _TEXT_KEYS + _LIST_KEYS:
            v = obj.get(k)
            if isinstance(v, (dict, list)):
                t = extract_text(v, _depth + 1)
                if t:
                    return t
        # أي قيمة أخرى متداخلة
        for v in obj.values():
            if isinstance(v, (dict, list)):
                t = extract_text(v, _depth + 1)
                if t:
                    return t
    return ""


def _error_detail(resp):
    try:
        j = resp.json()
        e = j.get("error") if isinstance(j, dict) else None
        if isinstance(e, dict):
            return str(e.get("message") or e.get("status") or "")[:200]
        return str(j)[:200]
    except Exception:
        return (resp.text or "")[:200]


# ======================================================================
# النداء
# ======================================================================
def run(prompt, timeout=None, post_fn=None):
    """
    يرجع (text, None) أو (None, reason). reason عربي قصير جاهز للعرض.
    post_fn(url, json=..., headers=..., timeout=...) → resp — يُحقن في الاختبارات.
    """
    if not Config.FEATURE_PLAN:
        return None, "ميزة /plan معطّلة (FEATURE_PLAN=0)"
    prompt = (prompt or "").strip()
    if not prompt:
        return None, "لا نص للتخطيط"
    try:
        assert_not_transcription()
    except RuntimeError as e:
        return None, str(e)
    keys = key_manager.get_active_keys(model=AGENT_QUOTA_MODEL)
    if not keys:
        return None, "لا مفتاح متاح لـ Antigravity الآن"
    key_entry = keys[0]
    key_str, key_name = key_entry.get("key"), key_entry.get("name", "Project")
    post = post_fn or requests.post
    timeout = timeout or (Config.GEMINI_CONNECT_TIMEOUT, int(Config.AGENT_TIMEOUT))
    with _lock:
        _state["calls"] += 1
    t0 = time.time()
    try:
        resp = post(INTERACTIONS_URL, json=build_payload(prompt),
                    headers={"x-goog-api-key": key_str, "Content-Type": "application/json"}, timeout=timeout)
    except requests.exceptions.Timeout:
        return _fail(f"Antigravity لم يرد خلال {int(Config.AGENT_TIMEOUT)} ث — جرّب لاحقاً")
    except requests.exceptions.RequestException as e:
        return _fail(f"شبكة: {type(e).__name__}")
    except Exception as e:  # أي شيء آخر (post_fn غريب…) — لا يخرج استثناء
        return _fail(f"خطأ غير متوقع: {type(e).__name__}")
    ms = int((time.time() - t0) * 1000)
    with _lock:
        _state["last_ms"] = ms
    status = getattr(resp, "status_code", 0)
    if status == 200:
        try:
            data = resp.json()
        except Exception:
            data = None
        text = extract_text(data) if data is not None else (getattr(resp, "text", "") or "").strip()
        if not text:
            return _fail("رد Antigravity بشكل غير متوقع (بلا نص) — قد تغيّر شكل الـ API")
        with _lock:
            _state["ok"] += 1
        try:
            key_manager.record_success(key_str, model=AGENT_QUOTA_MODEL)
        except Exception:
            pass
        print(f"🧭 [antigravity] [{key_name}] خطة في {ms / 1000:.1f} ث")
        return text, None
    detail = _error_detail(resp)
    if status == 429:
        daily = "day" in detail.lower() or "daily" in detail.lower() or "quota" in detail.lower()
        try:
            key_manager.mark_key_exhausted(key_str, model=AGENT_QUOTA_MODEL, daily=daily)
        except Exception:
            pass
        return _fail("حصة Antigravity اليومية نفدت على هذا المفتاح" if daily else "تهدئة 429 من Antigravity — جرّب بعد دقيقة")
    if status in (401, 403):
        return _fail(f"المفتاح مرفوض لـ Antigravity ({status}) — قد يحتاج تفعيل الـ API في مشروع Google")
    if status == 404:
        return _fail("Interactions API غير متاح (404) — v1beta قد تغيّر أو الوكيل غير مفعّل")
    if status == 400:
        return _fail(f"طلب مرفوض (400): {detail or 'نص فقط مدعوم'}")
    if status >= 500:
        return _fail(f"Google مزدحمة ({status}) — جرّب لاحقاً")
    return _fail(f"رد غير متوقع ({status}) {detail}".strip())


def _fail(reason):
    with _lock:
        _state["fail"] += 1
        _state["last_error"] = reason
    return None, reason


def stats():
    with _lock:
        return dict(_state)
