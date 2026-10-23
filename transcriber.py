# -*- coding: utf-8 -*-
"""
محرك التفريغ الصوتي المتعدد الوسائط (Core Multimodal Transcription Engine)

الترتيب (من Config.MODELS): 3.6 Flash (أساسي) ➔ 3.5 Transcribe (احتياطي). 3.8 معطّل. Lite ممنوع نهائياً.

بعد حادثة 01 (تأخير 127 ث): كل محاولة تُصنَّف أولاً — هل المشكلة في المفتاح، أم في الموديل،
أم في الطلب نفسه، أم في الشبكة؟ — وبناءً على التصنيف نقرر: مفتاح تاني، أم موديل تاني فوراً،
أم نوقف. موديل واقع (5xx/Timeout) يُقفل مؤقتاً على كل المفاتيح (ModelCircuit) فلا يضيّع وقت
الطلبات التالية.
"""

import os
import re
import time
import base64
import requests
from config import Config
from key_manager import key_manager
from model_state import model_circuit


# ======================================================================
# نتائج المحاولة (Outcome)
# ======================================================================
SUCCESS = "SUCCESS"
KEY_EXHAUSTED_DAILY = "KEY_EXHAUSTED_DAILY"   # 429 كوتا يومية → مفتاح تاني
KEY_RATE_LIMITED = "KEY_RATE_LIMITED"         # 429 بالدقيقة → مفتاح تاني
KEY_INVALID = "KEY_INVALID"                   # 400 API key / 401 / 403 → مفتاح تاني
MODEL_DOWN = "MODEL_DOWN"                     # 5xx → موديل تاني فوراً + قفل الموديل
MODEL_SLOW = "MODEL_SLOW"                     # ReadTimeout → موديل تاني فوراً + قفل الموديل
MODEL_NOT_FOUND = "MODEL_NOT_FOUND"           # 404 → موديل تاني + قفل طويل
BAD_REQUEST = "BAD_REQUEST"                   # 400 آخر → موديل تاني (مفاتيح تانية بلا فايدة)
BLOCKED = "BLOCKED"                           # 200 بدون نص (حجب/رفض) → موديل تاني
NETWORK = "NETWORK"                           # شبكة السيرفر → لا تلمس أي حالة

KEY_OUTCOMES = {KEY_EXHAUSTED_DAILY, KEY_RATE_LIMITED, KEY_INVALID}
MODEL_OUTCOMES = {MODEL_DOWN, MODEL_SLOW, MODEL_NOT_FOUND}
TRANSIENT_OUTCOMES = {MODEL_DOWN, MODEL_SLOW}   # تستحق جولة تانية بعد انتظار قصير

# 429 يومي: GenerateRequestsPerDay / generate_requests_per_day / "per day" / daily / quota_exceeded
_DAILY_RE = re.compile(r"per[\s_]*day|daily|quota_exceeded", re.IGNORECASE)
_KEY_HINTS = ("api key", "api_key", "apikey", "key not valid", "leaked", "permission_denied", "unauthenticated")

_NETWORK_MSG = "🌐 السيرفر مش قادر يوصل لـ Google (مشكلة شبكة من جهة الاستضافة). جرّب بعد دقيقة."


class Attempt:
    """نتيجة محاولة واحدة (موديل × مفتاح)."""
    __slots__ = ("outcome", "text", "detail", "retry_after", "elapsed", "status")

    def __init__(self, outcome, text="", detail="", retry_after=None, elapsed=0.0, status=None):
        self.outcome = outcome
        self.text = text
        self.detail = detail
        self.retry_after = retry_after
        self.elapsed = elapsed
        self.status = status


# ======================================================================
# الواجهة العامة
# ======================================================================
def transcribe_audio_bytes(audio_bytes, mime_type="audio/mp4"):
    """
    تفريغ بايتات الصوت عبر أفضل الموديلات المتاحة مع Failover سريع.
    يرجع (text, model, key_name). يرفع RuntimeError برسالة واضحة عند الفشل.
    للمعلومات الإضافية (الزمن، الموديلات المتخطاة) استخدم transcribe_audio_bytes_ex.
    """
    text, model, key_name, _info = transcribe_audio_bytes_ex(audio_bytes, mime_type)
    return text, model, key_name


def transcribe_audio_bytes_ex(audio_bytes, mime_type="audio/mp4"):
    """نفس transcribe_audio_bytes + قاموس info: {elapsed, skipped:[...], attempts:n}."""
    if not audio_bytes:
        raise ValueError("بيانات الصوت فارغة!")
    if not key_manager.data.get("keys"):
        raise RuntimeError("لا توجد أي مفاتيح API مسجلة في المنظومة! أرسل مفتاح Gemini في الشات أو اضبط GEMINI_API_KEYS.")

    started = time.time()
    b64_data = base64.b64encode(audio_bytes).decode("utf-8")
    timeout = (Config.GEMINI_CONNECT_TIMEOUT, dynamic_read_timeout(len(audio_bytes)))

    info = {"skipped": [], "attempts": 0, "elapsed": 0.0}
    last = None              # آخر Attempt
    reasons = {}             # model -> سبب الفشل الأخير (للرسالة النهائية)
    network_retries = 0

    def _done(att, model, key_str, key_name):
        key_manager.record_success(key_str, model=model)
        info["elapsed"] = round(time.time() - started, 1)
        return att.text, model, key_name, info

    rounds = 1 + max(0, int(Config.TRANSIENT_RETRY_ROUNDS))
    for rnd in range(rounds):
        saw_transient = False

        for model in Config.MODELS:
            left = model_circuit.remaining(model)
            if left:
                reason = model_circuit.reason(model) or "مقفول"
                info["skipped"].append(f"{_short(model)}: {reason} (يفتح بعد {_fmt_wait(left)})")
                reasons[model] = f"مزدحم ({reason}) — يفتح بعد {_fmt_wait(left)}"
                continue

            payload = _build_payload(b64_data, mime_type, model)
            keys = key_manager.get_active_keys(model=model)
            if not keys:
                reasons.setdefault(model, "كل المفاتيح مقفولة/نفدت كوتاها لهذا الموديل")

            for key_entry in keys:
                key_str = key_entry.get("key")
                key_name = key_entry.get("name", "Project")

                att = _attempt(model, key_str, payload, timeout)
                info["attempts"] += 1
                model_circuit.record_attempt(model, key_name, att.outcome, att.elapsed)
                _log_attempt(model, key_name, att)
                last = att

                # ---- مشكلة شبكة من عندنا: لا تلمس أي حالة، أعد مرة واحدة ثم ارفع ----
                if att.outcome == NETWORK:
                    if network_retries > 0:
                        raise RuntimeError(_NETWORK_MSG)
                    network_retries += 1
                    time.sleep(1)
                    att = _attempt(model, key_str, payload, timeout)
                    info["attempts"] += 1
                    model_circuit.record_attempt(model, key_name, att.outcome, att.elapsed)
                    _log_attempt(model, key_name, att)
                    last = att
                    if att.outcome == NETWORK:
                        raise RuntimeError(_NETWORK_MSG)
                    # لو الإعادة رجّعت نوع تاني من الخطأ نكمّل بالتصنيف الجديد أدناه

                if att.outcome == SUCCESS:
                    return _done(att, model, key_str, key_name)

                # ---- مشكلة مفتاح: جرّب المفتاح التالي لنفس الموديل ----
                if att.outcome == KEY_EXHAUSTED_DAILY:
                    key_manager.mark_key_exhausted(key_str, model=model, daily=True)
                    reasons[model] = "الكوتا اليومية خلصت"
                    continue
                if att.outcome == KEY_RATE_LIMITED:
                    key_manager.mark_key_exhausted(key_str, model=model, retry_after=att.retry_after)
                    reasons[model] = "تهدئة 429"
                    continue
                if att.outcome == KEY_INVALID:
                    key_manager.mark_key_invalid(key_str, reason=att.detail)
                    reasons[model] = "مفتاح مرفوض من Google"
                    continue

                # ---- مشكلة موديل أو طلب: اخرج من حلقة المفاتيح فوراً → الموديل التالي ----
                if att.outcome == MODEL_SLOW:
                    model_circuit.pause(model, Config.MODEL_PAUSE_ON_TIMEOUT, "timeout")
                    reasons[model] = f"بطيء (Timeout بعد {int(att.elapsed)} ث)"
                    saw_transient = True
                elif att.outcome == MODEL_DOWN:
                    model_circuit.pause(model, Config.MODEL_PAUSE_ON_5XX, f"{att.status or '5xx'}")
                    reasons[model] = f"Google مزدحمة ({att.status})"
                    saw_transient = True
                elif att.outcome == MODEL_NOT_FOUND:
                    model_circuit.pause(model, Config.MODEL_PAUSE_ON_404, "404")
                    reasons[model] = "غير متاح لهذا المشروع (404)"
                elif att.outcome == BAD_REQUEST:
                    reasons[model] = f"رفض الطلب (400): {att.detail[:80]}"
                elif att.outcome == BLOCKED:
                    reasons[model] = f"رجّع رداً فارغاً/محجوباً ({att.detail[:60]})"
                break  # أي حالة من اللي فوق (أو غير متوقعة): لا تحرق مفاتيح تانية

        # نهاية جولة: هل يستحق الأمر جولة تانية؟
        if not saw_transient or rnd == rounds - 1:
            break
        delay = _backoff(rnd)
        print(f"⏳ كل الموديلات مزدحمة مؤقتاً — جولة {rnd + 2}/{rounds} بعد {delay} ث...")
        time.sleep(delay)
        # لو القواطع لسه مقفولة بعد الانتظار مفيش فايدة من جولة (هتتخطّى كلها)
        if all(model_circuit.is_open(m) for m in Config.MODELS):
            break

    info["elapsed"] = round(time.time() - started, 1)
    raise RuntimeError(_final_message(reasons, last, tried_any=info["attempts"] > 0))


# ======================================================================
# محاولة واحدة + التصنيف
# ======================================================================
def _attempt(model, key_str, payload, timeout):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": key_str, "Content-Type": "application/json"}
    t0 = time.time()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.exceptions.ReadTimeout:
        return Attempt(MODEL_SLOW, detail="read timeout", elapsed=time.time() - t0)
    except requests.exceptions.Timeout:
        # ConnectTimeout: ما قدرناش نفتح الاتصال أصلاً → شبكة
        return Attempt(NETWORK, detail="connect timeout", elapsed=time.time() - t0)
    except requests.exceptions.RequestException as e:
        return Attempt(NETWORK, detail=str(e)[:100], elapsed=time.time() - t0)
    att = classify_response(resp)
    att.elapsed = time.time() - t0
    return att


def classify_response(resp):
    """يحوّل رد HTTP إلى Attempt مصنَّف (بدون أي أثر جانبي)."""
    status = getattr(resp, "status_code", 0)
    body = _safe_json(resp)
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    msg = str(err.get("message") or "")
    err_status = str(err.get("status") or "")
    raw = f"{msg} {err_status}".lower()

    if status == 200:
        text = _extract_text(body)
        if text:
            return Attempt(SUCCESS, text=text, status=200)
        return Attempt(BLOCKED, detail=_block_reason(body), status=200)

    if status == 429:
        retry_after = _parse_retry_delay(resp, body)
        if _DAILY_RE.search(raw):
            return Attempt(KEY_EXHAUSTED_DAILY, detail=msg[:150], retry_after=retry_after, status=429)
        return Attempt(KEY_RATE_LIMITED, detail=msg[:150], retry_after=retry_after, status=429)

    if status in (401, 403, 402):
        return Attempt(KEY_INVALID, detail=msg[:150] or f"HTTP {status}", status=status)

    if status == 400:
        if any(h in raw for h in _KEY_HINTS):
            return Attempt(KEY_INVALID, detail=msg[:150], status=400)
        return Attempt(BAD_REQUEST, detail=msg[:150], status=400)

    if status == 404:
        return Attempt(MODEL_NOT_FOUND, detail=msg[:150], status=404)

    if status >= 500 or status == 408:
        return Attempt(MODEL_DOWN, detail=msg[:150] or f"HTTP {status}", status=status)

    # أي كود تاني غير متوقع: نعامله كمشكلة طلب (لا نحرق مفاتيح)
    return Attempt(BAD_REQUEST, detail=f"HTTP {status}: {msg[:120] or str(getattr(resp, 'text', ''))[:120]}", status=status)


# ======================================================================
# بناء الطلب
# ======================================================================
def _build_payload(b64_data, mime_type, model=None):
    """
    الوضع الافتراضي (المجرَّب): القواعد كجزء نصي بجانب الصوت.
    الوضع الاختياري (Config.USE_SYSTEM_INSTRUCTION): القواعد في system_instruction.
    thinkingConfig يُرسل فقط لو الموديل مذكور في Config.MODEL_THINKING_LEVEL (قرار المالك: فاضي).
    """
    audio_part = {"inline_data": {"mime_type": mime_type, "data": b64_data}}
    if Config.USE_SYSTEM_INSTRUCTION:
        payload = {
            "system_instruction": {"parts": [{"text": Config.SYSTEM_PROMPT}]},
            "contents": [{"parts": [audio_part, {"text": "فرّغ هذا التسجيل الصوتي."}]}],
        }
    else:
        payload = {"contents": [{"parts": [audio_part, {"text": Config.SYSTEM_PROMPT}]}]}

    gen = {}
    if Config.TEMPERATURE is not None:
        gen["temperature"] = Config.TEMPERATURE
    level = (getattr(Config, "MODEL_THINKING_LEVEL", {}) or {}).get(model)
    if level:
        gen["thinkingConfig"] = {"thinkingLevel": str(level).upper()}
    if gen:
        payload["generationConfig"] = gen
    return payload


def dynamic_read_timeout(size_bytes):
    """مهلة القراءة تتناسب مع حجم الملف: min(MAX, BASE + PER_MB × MB)."""
    mb = max(0.0, float(size_bytes)) / (1024 * 1024)
    t = Config.GEMINI_READ_TIMEOUT_BASE + Config.GEMINI_READ_TIMEOUT_PER_MB * mb
    return int(min(Config.GEMINI_READ_TIMEOUT_MAX, max(Config.GEMINI_READ_TIMEOUT_BASE, t)))


# ======================================================================
# قراءة الرد
# ======================================================================
def _safe_json(resp):
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_text(data):
    """يستخرج النص من رد Gemini (حقل text أو audioTranscription)."""
    try:
        part = data["candidates"][0]["content"]["parts"][0]
    except (KeyError, IndexError, TypeError):
        return ""
    if "text" in part:
        return (part.get("text") or "").strip()
    if "audioTranscription" in part:
        return (part["audioTranscription"].get("text") or "").strip()
    return ""


def _block_reason(data):
    try:
        pf = data.get("promptFeedback") or {}
        if pf.get("blockReason"):
            return f"blockReason={pf['blockReason']}"
        cand = (data.get("candidates") or [{}])[0]
        if cand.get("finishReason"):
            return f"finishReason={cand['finishReason']}"
    except Exception:
        pass
    return "رد بدون نص"


def _parse_retry_delay(resp, body=None):
    """
    مدة الانتظار من رد 429: Header `Retry-After` (ثوانٍ) أو error.details[].retryDelay مثل "37s".
    يرجع ثوانٍ أو None.
    """
    try:
        ra = resp.headers.get("Retry-After") if getattr(resp, "headers", None) else None
        if ra and str(ra).strip().isdigit():
            return int(ra)
    except Exception:
        pass
    try:
        body = body if body is not None else _safe_json(resp)
        for d in (body.get("error", {}).get("details") or []):
            rd = d.get("retryDelay")
            if isinstance(rd, str):
                m = re.match(r"^\s*([\d.]+)\s*s\s*$", rd)
                if m:
                    return int(float(m.group(1)))
    except Exception:
        pass
    return None


# ======================================================================
# لوج ورسائل
# ======================================================================
def _short(m):
    return m.replace("gemini-", "")


def _fmt_wait(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} ث"
    return f"{max(1, seconds // 60)} د"


def _backoff(rnd):
    seq = Config.TRANSIENT_RETRY_BACKOFF or (2,)
    return seq[min(rnd, len(seq) - 1)]


def _log_attempt(model, key_name, att):
    icon = "✅" if att.outcome == SUCCESS else "⚠️"
    extra = f" — {att.detail}" if att.detail and att.outcome != SUCCESS else ""
    print(f"{icon} [{_short(model)}] [{key_name}] {att.outcome} في {att.elapsed:.1f} ث{extra}")


def _final_message(reasons, last, tried_any):
    parts = [f"{_short(m)}: {r}" for m, r in reasons.items()]
    detail = " · ".join(parts) if parts else (last.detail if last else "")

    if reasons and all(model_circuit.is_open(m) for m in Config.MODELS):
        wait, m = model_circuit.earliest_open(Config.MODELS)
        return (
            f"⛔ Google مزدحمة على كل الموديلات حالياً. أقرب فتح: {_short(m)} بعد {_fmt_wait(wait)}. "
            f"اكتب /reset لتجاوز الانتظار.\n({detail})"
        )

    if not tried_any:
        keys = key_manager.data.get("keys", [])
        if keys and all(k.get("status") == "invalid" for k in keys):
            return "❌ كل المفاتيح مرفوضة من Google (مسرّبة/معطّلة). ابعت مفتاح جديد."
        wait = key_manager.earliest_availability()
        if wait:
            return (
                f"كل المفاتيح في فترة تهدئة أو استنفدت كوتاها اليومية. "
                f"أقرب مفتاح يعود للعمل بعد {max(1, wait // 60)} دقيقة (تصفير الكوتا: {key_manager.next_reset_display()})."
                + (f"\n({detail})" if detail else "")
            )

    if last and last.outcome == BLOCKED:
        return f"🚫 الموديل رفض المحتوى أو رجّع رداً فارغاً ({last.detail}).\n({detail})"

    return f"فشلت كافة المحاولات عبر جميع الموديلات والمفاتيح. {detail}"


# ======================================================================
# ملفات محلية
# ======================================================================
def transcribe_audio_file(file_path):
    """تفريغ ملف صوتي من القرص المحلي"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"الملف غير موجود: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".mp3": "audio/mp3",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".webm": "audio/webm",
        ".aac": "audio/aac",
        ".flac": "audio/flac"
    }
    mime_type = mime_map.get(ext, "audio/mp4")

    with open(file_path, "rb") as f:
        audio_bytes = f.read()

    return transcribe_audio_bytes(audio_bytes, mime_type)
