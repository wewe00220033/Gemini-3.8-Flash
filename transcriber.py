# -*- coding: utf-8 -*-
"""
محرك التفريغ الصوتي المتعدد الوسائط (Core Multimodal Transcription Engine)

الترتيب (من Config.MODELS): 3.6 Flash (أساسي) ➔ 3.5 Transcribe (احتياطي). 3.8 معطّل. Lite ممنوع نهائياً.

بعد حادثة 01 (تأخير 127 ث): كل محاولة تُصنَّف أولاً — هل المشكلة في المفتاح، أم في الموديل،
أم في الطلب نفسه، أم في الشبكة؟ — وبناءً على التصنيف نقرر: مفتاح تاني، أم موديل تاني فوراً،
أم نوقف. موديل واقع (5xx/Timeout) يُقفل مؤقتاً على كل المفاتيح (ModelCircuit) فلا يضيّع وقت
الطلبات التالية.

Failover v2 (مرحلة 2 — مواصفة 04 §2):
- 503 على (موديل، مفتاح): قبل قفل الموديل نقفز لمفتاح من **مشروع مختلف** (project_group) حتى
  MODEL_503_KEY_HOPS قفزة — الحمل قد يكون على المشروع لا الموديل. لو الكل 503 → قفل متدرج.
- قفل متدرج لـ 5xx: 20 → 45 → 90 → 180 ثانية حسب الضربات المتتالية؛ النجاح يصفّر العداد.
- انتظار ذكي بدل backoff الأعمى: ننتظر حتى أقرب فتح قاطع فقط لو يسمح به RETRY_BUDGET_SECONDS،
  وإلا نرفع DeferredError فيؤجل الطابور المهمة ويعيدها تلقائياً — بلا /reset يدوي.
"""

import os
import re
import threading
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

    info = {"skipped": [], "attempts": 0, "elapsed": 0.0, "hops": 0, "waited": 0, "rounds": 0,
            "context": "off", "strikes": 0}
    last = None              # آخر Attempt
    # المرحلة 4 (10 §2-3): L1 من الجلسة النشطة داخل طبقة A؛ الضربات بسبب السياق فقط (BLOCKED/400/فارغ/صدى)
    ctx = {"text": _session_context_text(), "strikes": 0}
    info["context"] = "full" if ctx["text"] else _session_context_mode()
    strikes_limit = int(getattr(Config, "CONTEXT_STRIKES_TO_SIMPLE", 2))
    reasons = {}             # model -> سبب الفشل الأخير (للرسالة النهائية)
    network_retries = 0
    budget = max(0, float(getattr(Config, "RETRY_BUDGET_SECONDS", 45)))
    max_hops = max(0, int(getattr(Config, "MODEL_503_KEY_HOPS", 2)))
    ladder = getattr(Config, "MODEL_PAUSE_LADDER_5XX", None) or (Config.MODEL_PAUSE_ON_5XX,)

    def _done(att, model, key_str, key_name):
        key_manager.record_success(key_str, model=model)
        model_circuit.mark_success(model)          # يصفّر عداد ضربات 5xx للموديل
        info["elapsed"] = round(time.time() - started, 1)
        _session_record_success(used_context=bool(ctx["text"]))
        return att.text, model, key_name, info

    def _try(model, key_str, key_name, payload):
        att = _attempt(model, key_str, payload, timeout)
        info["attempts"] += 1
        model_circuit.record_attempt(model, key_name, att.outcome, att.elapsed)
        _log_attempt(model, key_name, att)
        return att

    def _context_strike(reason):
        """ضربة سياق (10 §3). عند بلوغ الحد → المحاولات التالية بسيطة (بلا L1؛ L0 يبقى)."""
        ctx["strikes"] += 1
        info["strikes"] = ctx["strikes"]
        _session_record_strike(reason)
        if ctx["strikes"] >= strikes_limit:
            ctx["text"] = ""
            info["context"] = "simple"
            print(f"⚙️ ضربتا سياق ({reason}) — الوضع البسيط: إعادة بلا سياق الجلسة")

    def _context_retry(model, key_str, key_name, att):
        """
        10 §3: فشل قد يسببه السياق (BLOCKED/BAD_REQUEST/صدى) بينما L1 محقون →
        ضربة، ثم إعادة فورية بنفس المفتاح (بالسياق لو ضربة واحدة، بسيطة لو بلغنا الحد).
        يرجع Attempt الأخير (قد يكون SUCCESS غير صدى → ينجح المنادي).
        """
        while ctx["text"] or att.outcome == SUCCESS:
            is_echo = att.outcome == SUCCESS and ctx["text"] and _is_echo(att.text, ctx["text"])
            if att.outcome == SUCCESS and not is_echo:
                return att
            if not ctx["text"]:
                return att
            if att.outcome not in (SUCCESS, BAD_REQUEST, BLOCKED):
                return att                                   # 503/429/… ليست ضربات
            _context_strike("echo" if is_echo else att.outcome)
            att = _try(model, key_str, key_name, _build_payload(b64_data, mime_type, model, context_text=ctx["text"]))
            last_holder[0] = att
            if att.outcome == SUCCESS and not (ctx["text"] and _is_echo(att.text, ctx["text"])):
                return att
            if not ctx["text"]:
                return att                                   # المحاولة البسيطة هي الأخيرة
        return att

    last_holder = [None]

    rounds = 1 + max(0, int(Config.TRANSIENT_RETRY_ROUNDS))   # سقف أمان؛ الميزانية الزمنية هي الحاكم الفعلي
    for rnd in range(rounds):
        saw_transient = False
        info["rounds"] = rnd + 1

        for model in Config.MODELS:
            left = model_circuit.remaining(model)
            if left:
                reason = model_circuit.reason(model) or "مقفول"
                info["skipped"].append(f"{_short(model)}: {reason} (يفتح بعد {_fmt_wait(left)})")
                reasons[model] = f"مزدحم ({reason}) — يفتح بعد {_fmt_wait(left)}"
                continue

            payload = _build_payload(b64_data, mime_type, model, context_text=ctx["text"])
            keys = key_manager.get_active_keys(model=model)
            if not keys:
                reasons.setdefault(model, "كل المفاتيح مقفولة/نفدت كوتاها لهذا الموديل")

            down_groups = set()      # مجموعات مشاريع رجّعت 503 على هذا الموديل في هذه الجولة
            hops_used = 0

            for idx, key_entry in enumerate(keys):
                key_str = key_entry.get("key")
                key_name = key_entry.get("name", "Project")
                group = _group_of(key_entry)
                if down_groups and group in down_groups:
                    continue         # شقيق في مشروع رجّع 503 الآن — بلا فايدة (نفس الحمل)

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

                if ctx["text"] and att.outcome in (SUCCESS, BAD_REQUEST, BLOCKED):
                    att = _context_retry(model, key_str, key_name, att)
                    last = last_holder[0] or att

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
                    saw_transient = True
                    down_groups.add(group)
                    # 04 §2.1: قبل قفل الموديل جرّب مفتاحاً من مشروع مختلف (حتى MODEL_503_KEY_HOPS)
                    has_alt = any(_group_of(k) not in down_groups for k in keys[idx + 1:])
                    if hops_used < max_hops and has_alt:
                        hops_used += 1
                        info["hops"] += 1
                        reasons[model] = f"Google مزدحمة ({att.status}) — جرّبنا مشروعاً آخر"
                        print(f"↪️ [{_short(model)}] 503 على [{key_name}] — قفزة {hops_used}/{max_hops} لمفتاح من مشروع آخر")
                        continue
                    # 04 §2.2: كل المشاريع المتاحة (حتى الحد) 503 → قفل متدرج 20/45/90/180
                    locked = model_circuit.strike_5xx(model, ladder, str(att.status or "5xx"))
                    reasons[model] = f"Google مزدحمة ({att.status}) — مقفول {_fmt_wait(locked)}"
                elif att.outcome == MODEL_NOT_FOUND:
                    model_circuit.pause(model, Config.MODEL_PAUSE_ON_404, "404")
                    reasons[model] = "غير متاح لهذا المشروع (404)"
                elif att.outcome == BAD_REQUEST:
                    reasons[model] = f"رفض الطلب (400): {att.detail[:80]}"
                elif att.outcome == BLOCKED:
                    reasons[model] = f"رجّع رداً فارغاً/محجوباً ({att.detail[:60]})"
                break  # أي حالة من اللي فوق (أو غير متوقعة): لا تحرق مفاتيح تانية

        # نهاية جولة: هل يستحق الأمر جولة تانية؟ (04 §2.3 — انتظار ذكي داخل الميزانية)
        if not saw_transient or rnd == rounds - 1:
            break
        wait = _smart_wait(rnd, started, budget)
        if wait is None:
            print(f"⏳ ميزانية المحاولة ({int(budget)} ث) لا تكفي لانتظار فتح القاطع — تأجيل للطابور.")
            break
        print(f"⏳ كل الموديلات مزدحمة مؤقتاً — جولة {rnd + 2}/{rounds} بعد {wait} ث (أقرب فتح قاطع)...")
        time.sleep(wait)
        info["waited"] += wait

    info["elapsed"] = round(time.time() - started, 1)
    msg = _final_message(reasons, last, tried_any=info["attempts"] > 0)
    wait_s, kind = _deferral(reasons, tried_any=info["attempts"] > 0)
    if wait_s:
        raise DeferredError(msg, wait_s=wait_s, kind=kind)
    raise RuntimeError(msg)


def _group_of(key_entry):
    """مجموعة مشروع المفتاح (04 §2.4) — تحمّل key_manager بدون الدالة (توافق) بأن يعتبر كل مفتاح مجموعة."""
    fn = getattr(key_manager, "group_of", None)
    if fn is None:
        return "key:" + str(key_entry.get("key"))
    return fn(key_entry)


def _smart_wait(rnd, started, budget):
    """
    كم ننتظر قبل الجولة التالية؟ (04 §2.3)
      wait = max(أقرب فتح قاطع, backoff[rnd])   — لا معنى لجولة والقواطع لسه مقفولة
      لو wait يتجاوز المتبقي من RETRY_BUDGET_SECONDS → None (أوقف وأجّل في الطابور).
    """
    elapsed = time.time() - started
    remaining_budget = budget - elapsed
    earliest, _m = model_circuit.earliest_open(Config.MODELS)
    wait = max(int(earliest or 0), int(_backoff(rnd)))
    if wait <= 0 or wait > remaining_budget:
        return None
    return wait


class DeferredError(RuntimeError):
    """
    فشل مؤقت معروف المدة (03 §2.3): الطابور يؤجل المهمة إلى ready_at = now + wait_s بدل إفشالها.
    kind: "model" (قواطع 5xx/timeout — ثوانٍ/دقائق) أو "quota" (حصة/تهدئة مفاتيح — قد تكون ساعات → صمام سؤال المالك).
    """

    def __init__(self, message, wait_s, kind):
        super().__init__(message)
        self.wait_s = int(wait_s)
        self.kind = kind


def _deferral(reasons, tried_any):
    """(wait_s, kind) لو الفشل مؤقت ومعروف المدة، وإلا (0, None)."""
    if reasons and _all_models_blocked():
        wait, _m = model_circuit.earliest_open(Config.MODELS)
        if wait:
            return max(1, int(wait)), "model"
    if not tried_any:
        keys = key_manager.data.get("keys", [])
        if keys and all(k.get("status") == "invalid" for k in keys):
            return 0, None
        wait = key_manager.earliest_availability()
        if wait:
            return max(1, int(wait)), "quota"
    return 0, None


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
def _build_payload(b64_data, mime_type, model=None, context_text=""):
    """
    الوضع الافتراضي (المجرَّب): القواعد كجزء نصي بجانب الصوت.
    الوضع الاختياري (Config.USE_SYSTEM_INSTRUCTION): القواعد في system_instruction.
    thinkingConfig يُرسل فقط لو الموديل مذكور في Config.MODEL_THINKING_LEVEL (قرار المالك: فاضي).
    context_text (المرحلة 4، 10 §2): L1 سياق الجلسة — يُلحق بطبقة A عند وجوده فقط؛ "" = الوضع البسيط.
    """
    audio_part = {"inline_data": {"mime_type": mime_type, "data": b64_data}}
    prompt = transcription_prompt()
    if context_text:
        prompt = prompt + "\n\n" + context_text
    if Config.USE_SYSTEM_INSTRUCTION:
        payload = {
            "system_instruction": {"parts": [{"text": prompt}]},
            "contents": [{"parts": [audio_part, {"text": "فرّغ هذا التسجيل الصوتي."}]}],
        }
    else:
        payload = {"contents": [{"parts": [audio_part, {"text": prompt}]}]}

    gen = {}
    if Config.TEMPERATURE is not None:
        gen["temperature"] = Config.TEMPERATURE
    level = (getattr(Config, "MODEL_THINKING_LEVEL", {}) or {}).get(model)
    if level:
        gen["thinkingConfig"] = {"thinkingLevel": str(level).upper()}
    if gen:
        payload["generationConfig"] = gen
    return payload


def _session_context_text():
    """L1 من الجلسة النشطة — '' عند التعطيل/لا جلسة/وضع saver (10 §5: saver يعطّل L1)."""
    try:
        if not getattr(Config, "CONTEXT_ENABLED", True):
            return ""
        if key_manager.quota_mode() == "saver":
            return ""
        import sessions
        return sessions.context_prompt_text()
    except Exception as e:  # pragma: no cover
        print(f"⚠️ سياق الجلسة: {e} — بلا سياق")
        return ""


def _session_context_mode():
    try:
        import sessions
        return sessions.context_mode()
    except Exception:  # pragma: no cover
        return "off"


def _session_record_strike(reason):
    try:
        import sessions
        sessions.record_strike(reason)
    except Exception:  # pragma: no cover
        pass


def _session_record_success(used_context):
    try:
        import sessions
        sessions.record_success(used_context)
    except Exception:  # pragma: no cover
        pass


def _is_echo(text, l1_prompt_text):
    """كشف صدى السياق (10 §3) — نقارن بجسم L1 فقط (بلا سطر التعليمات)."""
    try:
        import sessions
        body = l1_prompt_text.split("\n", 1)[1] if "\n" in l1_prompt_text else l1_prompt_text
        return sessions.is_context_echo(text, body)
    except Exception:  # pragma: no cover
        return False


def transcription_prompt():
    """
    طبقة A (06 §1): Config.SYSTEM_PROMPT + سطر الدور الحالي + تلميح القاموس الإملائي (05 §5).
    بدون دور/قاموس = SYSTEM_PROMPT حرفياً (لا تغيير في السلوك المجرَّب). أي فشل → SYSTEM_PROMPT.
    """
    try:
        import prompts
        import settings
        import glossary
        hints = glossary.get_glossary().hints_for_prompt()
        try:
            import sessions
            th = sessions.terms_hint()
            if th:
                hints = (hints + "\n" + th) if hints else th
        except Exception:
            pass
        return prompts.compose_prompt("transcribe", settings.effective_role(), hints)
    except Exception as e:  # pragma: no cover
        print(f"⚠️ مركّب البرومبت: {e} — استخدام SYSTEM_PROMPT الأساسي")
        return Config.SYSTEM_PROMPT


def dynamic_read_timeout(size_bytes):
    """
    مهلة القراءة تتناسب مع حجم الملف: min(cap, BASE + PER_MB × MB).
    سقفان (04 §2.5b، 22 U3): ≤ 8MB → MAX_SMALL=90 (اكتشاف تعليق سريع للفويسات العادية)؛
    > 8MB → MAX_LARGE=180 (ملف 15MB يحتاج 135ث فعلاً — كان يُقصّ إلى 90 → timeout زائف).
    """
    mb = max(0.0, float(size_bytes)) / (1024 * 1024)
    t = Config.GEMINI_READ_TIMEOUT_BASE + Config.GEMINI_READ_TIMEOUT_PER_MB * mb
    cap = Config.GEMINI_READ_TIMEOUT_MAX_LARGE if mb > Config.GEMINI_LARGE_FILE_MB else Config.GEMINI_READ_TIMEOUT_MAX_SMALL
    return int(min(cap, max(Config.GEMINI_READ_TIMEOUT_BASE, t)))


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


_current_job = threading.local()


def set_current_job(job_id):
    """المرحلة 5 (09 §2): معرّف المهمة الجارية في هذا الخيط — كل محاولة Gemini تُسجَّل في jobs.ndjson لأمر /trace."""
    _current_job.job_id = job_id


def _log_attempt(model, key_name, att):
    icon = "✅" if att.outcome == SUCCESS else "⚠️"
    extra = f" — {att.detail}" if att.detail and att.outcome != SUCCESS else ""
    print(f"{icon} [{_short(model)}] [{key_name}] {att.outcome} في {att.elapsed:.1f} ث{extra}")
    jid = getattr(_current_job, "job_id", None)
    if jid:
        try:
            import jobs
            jobs.record(jid, "attempt", att_model=model, att_key=str(key_name), att_outcome=att.outcome,
                        att_ms=int(att.elapsed * 1000), att_status=getattr(att, "status", None),
                        att_detail=(att.detail or "")[:120] if att.outcome != SUCCESS else None)
        except Exception:
            pass


def _all_models_blocked():
    """كل موديل إمّا قاطعه مقفول أو لا مفتاح متاح له الآن — والقفل المؤقت موجود على واحد على الأقل."""
    any_circuit = False
    for m in Config.MODELS:
        if model_circuit.is_open(m):
            any_circuit = True
            continue
        if key_manager.get_active_keys(model=m):
            return False
    return any_circuit


GEMINI_PREFIX = "🤖 Gemini: "     # 04 §4: تمييز مصدر الخطأ للمستخدم


def _final_message(reasons, last, tried_any):
    parts = [f"{_short(m)}: {r}" for m, r in reasons.items()]
    detail = " · ".join(parts) if parts else (last.detail if last else "")

    wait, m = model_circuit.earliest_open(Config.MODELS) if reasons and _all_models_blocked() else (0, None)
    if wait and m:   # سباق نادر: القاطع انتهى بين الفحصين → نكمل للرسالة العامة بدل الانفجار
        return (
            f"{GEMINI_PREFIX}⏳ Google مزدحمة على كل الموديلات حالياً. أقرب فتح: {_short(m)} بعد {_fmt_wait(wait)}. "
            f"هأعيد المحاولة تلقائياً (أو اكتب /reset لتجاوز الانتظار).\n({detail})"
        )

    if not tried_any:
        keys = key_manager.data.get("keys", [])
        if keys and all(k.get("status") == "invalid" for k in keys):
            return f"{GEMINI_PREFIX}❌ كل المفاتيح مرفوضة من Google (مسرّبة/معطّلة). ابعت مفتاح جديد."
        wait = key_manager.earliest_availability()
        if wait:
            return (
                f"{GEMINI_PREFIX}كل المفاتيح في فترة تهدئة أو استنفدت كوتاها اليومية. "
                f"أقرب مفتاح يعود للعمل بعد {max(1, wait // 60)} دقيقة (تصفير الكوتا: {key_manager.next_reset_display()})."
                + (f"\n({detail})" if detail else "")
            )

    if last and last.outcome == BLOCKED:
        return f"{GEMINI_PREFIX}🚫 الموديل رفض المحتوى أو رجّع رداً فارغاً ({last.detail}).\n({detail})"

    return f"{GEMINI_PREFIX}فشلت كافة المحاولات عبر جميع الموديلات والمفاتيح. {detail}"


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
