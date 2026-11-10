# -*- coding: utf-8 -*-
"""
المساعد النصي (Text Assist) — مواصفة 02 §1 عمود 3 و07 (المرحلة 3c).

`gemini-3.5-flash-lite` مسموح **نصياً فقط** (تنظيم/تسمية/أدوار) بأقصى تفكير — **ممنوع في التفريغ**
(اختبار `test_lite_never_used_for_transcription` يحرس التقاطع).

- `polish(raw_text, role)`: نص خام → نص منظم عبر طبقة B (prompts.compose_prompt). لا صوت أبداً.
- يدور على كل المفاتيح المتاحة؛ 429 يومي على lite يُسجَّل في `exhausted_models` للمفتاح (موديل مستقل بحصته).
- فشل التنظيم لا يرفع استثناءً للمنادي العادي: `polish()` ترجع (None, reason) — الخام لا يتأثر (07 §2.2).
- Spike (17): flash-lite يستخدم `thinkingConfig.thinkingBudget` لا `thinkingLevel`. لو رجع 400 بسبب thinkingConfig
  نعيد المحاولة مرة واحدة بدونه (TEXT_ASSIST_THINKING_FALLBACK) ونسجّل ذلك — لا نخمّن الصيغة بصمت.
"""

import time
import threading
import re
import difflib
import os
import signal
import subprocess
import shlex
import logging

import requests

from config import Config
from key_manager import key_manager
import prompts
import transcriber
import logkit

logger = logging.getLogger("text_assist")


class _ShelbyQueueCoordinator:
    """
    منسّق صف FIFO عادل لاستدعاءات المزود المحلي (شلبي) لمنع استنزاف موارد النظام (GPU/CPU/RAM).
    يدعم:
      - سقف أقصى للمنتظرين (MAX_WAITERS = 8).
      - مهلة انتظار منفصلة للقفل (LOCAL_LOCK_WAIT_SEC = 45.0ث).
      - أسبقية اختيارية للفويس النشط في مرحلة السبك النهائي (priority=True).
      - توافق كامل كبديل لـ Lock العادي (.acquire(timeout=...), .release()).
    """
    def __init__(self, max_waiters=8):
        self.max_waiters = max_waiters
        self._lock = threading.Lock()
        self._busy = False
        self._waiters = []  # list of [ticket_id, event, is_priority]
        self._ticket_counter = 0

    def acquire(self, timeout=None, priority=False):
        """
        طلب دور لتشغيل المزود المحلي. يرجع True عند الحصول على القفل، أو False عند انتهاء المهلة أو امتلاء الصف.
        """
        if timeout is None:
            timeout = getattr(Config, "LOCAL_LOCK_WAIT_SEC", 45.0)
        else:
            timeout = float(timeout)

        with self._lock:
            if not self._busy and not self._waiters:
                self._busy = True
                return True

            if len(self._waiters) >= self.max_waiters:
                return False

            self._ticket_counter += 1
            ticket_id = self._ticket_counter
            evt = threading.Event()
            entry = [ticket_id, evt, priority]
            if priority:
                idx = 0
                while idx < len(self._waiters) and self._waiters[idx][2]:
                    idx += 1
                self._waiters.insert(idx, entry)
            else:
                self._waiters.append(entry)

        signaled = evt.wait(timeout=max(0.0, timeout))
        if not signaled and evt.is_set():
            signaled = True

        with self._lock:
            if signaled:
                self._busy = True
                return True
            else:
                still_in = any(w[0] == ticket_id for w in self._waiters)
                if still_in:
                    self._waiters = [w for w in self._waiters if w[0] != ticket_id]
                    return False
                else:
                    if self._waiters:
                        _tid, next_evt, _prio = self._waiters.pop(0)
                        self._busy = True
                        next_evt.set()
                    else:
                        self._busy = False
                    return False

    def release(self):
        """إفساح المجال للمنتظر التالي في الصف."""
        with self._lock:
            if self._waiters:
                _tid, next_evt, _prio = self._waiters.pop(0)
                self._busy = True
                next_evt.set()
            else:
                self._busy = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


_lock = threading.RLock()
_shelby_cli_lock = _ShelbyQueueCoordinator(max_waiters=8)
_state = {
    "thinking_ok": None,
    "calls": 0,
    "ok": 0,
    "fail": 0,
    "last_error": None,
    "local_calls": 0,
    "local_ok": 0,
    "local_fail": 0,
    "local_last_error": None,
    "local_noop": 0,
    "synth_calls": 0,
    "synth_ok": 0,
    "synth_fail": 0,
    "synth_last_error": None,
    "synth_fallback_calls": 0,
}
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\ufeff\u202a-\u202e\s]+")


def is_effective_empty(text):
    """يكتشف إن كان النص فارغاً أو لا يحوي سوى مسافات أو أحرف يونيكود غير مرئية (مثل RLM/BOM)."""
    if not text:
        return True
    return len(_INVISIBLE_RE.sub("", str(text))) == 0


def model_name():
    return Config.TEXT_ASSIST_MODEL


def assert_not_transcription_model():
    """حارس: موديل المساعد النصي لا يجوز أن يكون ضمن موديلات التفريغ ولا أن يحوي 'lite' موديلٌ للتفريغ."""
    if model_name() in Config.MODELS:
        raise RuntimeError(f"TEXT_ASSIST_MODEL={model_name()} موجود في Config.MODELS — Lite ممنوع للتفريغ")
    for m in Config.MODELS:
        if "lite" in m.lower():
            raise RuntimeError(f"موديل Lite ({m}) داخل سلسلة التفريغ — ممنوع بقرار المالك")


def assert_not_forbidden_assist_model(model=None):
    """حارس برمجي وقت التشغيل: منع موديلات التفريغ الصوتي أو النماذج المحظورة كـ gemini-3.6-flash للمساعد النصي."""
    m = model or model_name()
    if m in Config.MODELS:
        raise RuntimeError(f"الموديل {m} موجود في Config.MODELS — ممنوع للمساعد النصي")
    if m in ("gemini-3.6-flash", "gemini-3.8-flash"):
        raise RuntimeError(f"الموديل {m} محظور للمساعد النصي — المسموح flash-lite فقط")
    if getattr(Config, "_ALL_MODELS", None) and m in Config._ALL_MODELS:
        raise RuntimeError(f"الموديل {m} من موديلات الصوت — ممنوع للمساعد النصي")


def _payload(system_text, user_text, with_thinking=True):
    payload = {
        "system_instruction": {"parts": [{"text": system_text}]},
        "contents": [{"parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.2},
    }
    if with_thinking and Config.TEXT_ASSIST_THINKING_BUDGET:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": int(Config.TEXT_ASSIST_THINKING_BUDGET)}
    return payload


def generate(system_text, user_text, timeout=None):
    """
    نداء نصي واحد عبر أفضل مفتاح متاح لموديل المساعد. يرجع (text, None) أو (None, reason).
    لا يلمس قواطع موديلات التفريغ (model_circuit) — موديل مستقل.
    """
    assert_not_forbidden_assist_model()
    model = model_name()
    timeout = timeout or (Config.GEMINI_CONNECT_TIMEOUT, Config.TEXT_ASSIST_TIMEOUT)
    keys = key_manager.get_active_keys(model=model)
    if not keys:
        return None, "لا مفتاح متاح للمساعد النصي الآن"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    last_reason = "فشل غير معروف"
    for key_entry in keys:
        key_str, key_name = key_entry.get("key"), key_entry.get("name", "Project")
        with_thinking = _state["thinking_ok"] is not False
        for attempt in range(2):
            with _lock:
                _state["calls"] += 1
            t0 = time.monotonic()
            try:
                resp = requests.post(url, json=_payload(system_text, user_text, with_thinking),
                                     headers={"x-goog-api-key": key_str, "Content-Type": "application/json"},
                                     timeout=timeout)
            except requests.exceptions.RequestException as e:
                last_reason = f"شبكة: {type(e).__name__}"
                break
            att = transcriber.classify_response(resp)
            elapsed = time.monotonic() - t0
            if att.outcome == transcriber.SUCCESS:
                with _lock:
                    _state["ok"] += 1
                    if with_thinking:
                        _state["thinking_ok"] = True
                key_manager.record_success(key_str, model=model)
                print(f"✨ [{model.replace('gemini-', '')}] [{key_name}] polish في {elapsed:.1f} ث")
                return att.text, None
            # thinkingConfig غير مقبول؟ (400 يذكر thinking) → إعادة بدون تفكير مرة واحدة
            if att.outcome == transcriber.BAD_REQUEST and with_thinking and "thinking" in (att.detail or "").lower():
                with _lock:
                    _state["thinking_ok"] = False
                print(f"ℹ️ المساعد النصي: {model} رفض thinkingConfig ({att.detail[:80]}) — إعادة بدون تفكير")
                with_thinking = False
                continue
            if att.outcome == transcriber.KEY_EXHAUSTED_DAILY:
                key_manager.mark_key_exhausted(key_str, model=model, daily=True)
                last_reason = "حصة المساعد النصي اليومية نفدت على هذا المفتاح"
            elif att.outcome == transcriber.KEY_RATE_LIMITED:
                key_manager.mark_key_exhausted(key_str, model=model, retry_after=att.retry_after)
                last_reason = "تهدئة 429 للمساعد النصي"
            elif att.outcome == transcriber.KEY_INVALID:
                key_manager.mark_key_invalid(key_str, reason=att.detail)
                last_reason = "مفتاح مرفوض"
            else:
                last_reason = f"{att.outcome} ({att.status})"
                if att.outcome in transcriber.MODEL_OUTCOMES or att.outcome in (transcriber.BAD_REQUEST, transcriber.BLOCKED):
                    with _lock:
                        _state["fail"] += 1
                        _state["last_error"] = last_reason
                    return None, last_reason     # مشكلة موديل/طلب: مفاتيح أخرى لن تساعد
            break   # مشكلة مفتاح → المفتاح التالي
    with _lock:
        _state["fail"] += 1
        _state["last_error"] = last_reason
    return None, last_reason


def _kill_process_tree(proc):
    """قتل شجرة العمليات بالكامل (العملية وأي أبناء متفرعين) على Windows وPOSIX."""
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    if not pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except Exception:
            pass
    else:
        try:
            pgid = os.getpgid(pid)
            sig = getattr(signal, "SIGKILL", 9)
            os.killpg(pgid, sig)
        except (OSError, ProcessLookupError, PermissionError):
            pass
    try:
        proc.kill()
    except (OSError, ProcessLookupError):
        pass


def _call_local_provider(system_text, raw_text, stage="polish", timeout=None, process_factory=None, priority=False):
    """
    استدعاء المزود المحلي المستقل كـ Black-Box CLI عبر subprocess.Popen بمهلة وإغلاق آمن وقتل شجرة العمليات.
    يرجع (text, None) عند النجاح، أو (None, reason) عند الفشل/المهلة.
    """
    cmd = getattr(Config, "LOCAL_POLISH_CMD", "") or ""
    if not cmd:
        return None, "لا يوجد مزود محلي مهيأ"

    if timeout is None:
        timeout = getattr(Config, "LOCAL_POLISH_TIMEOUT", 180.0) or 180.0
    else:
        timeout = float(timeout)

    payload = f"{system_text}\n\n===USER_INPUT===\n{raw_text}"

    lock_wait = min(getattr(Config, "LOCAL_LOCK_WAIT_SEC", 45.0), timeout)
    acquired = _shelby_cli_lock.acquire(timeout=lock_wait, priority=priority)
    if not acquired:
        return None, f"المزود المحلي مشغول بمهمة أخرى (تجاوز انتظار القفل {lock_wait}ث)"

    cmd_args = cmd
    t0 = time.monotonic()
    proc = None
    try:
        if isinstance(cmd, str):
            cmd_args = cmd if os.name == "nt" else shlex.split(cmd)
        else:
            cmd_args = cmd

        popen_fn = process_factory or subprocess.Popen
        popen_kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "shell": False,
            "env": dict(os.environ),
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        proc = popen_fn(cmd_args, **popen_kwargs)
        try:
            stdout_data, stderr_data = proc.communicate(input=payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                proc.communicate(timeout=2)
            except Exception:
                pass
            return None, f"تجاوز مهلة المزود المحلي ({timeout}ث)"

        elapsed = time.monotonic() - t0
        returncode = proc.returncode if proc.returncode is not None else 0
        stdout_str = (stdout_data or "").strip()
        stderr_str = (stderr_data or "").strip()

        if returncode == 0 and stdout_str:
            logger.info("✨ [local] %s في %.1f ث", stage, elapsed)
            return stdout_str, None

        if stderr_str:
            raw_err = stderr_str.splitlines()[-1][:120]
        elif returncode == 0:
            raw_err = "المزود خرج بنجاح لكن stdout فارغ (لا نص منظم)"
        else:
            raw_err = f"خروج غير صفري ({returncode})"

        clean_err = re.sub(r'([A-Za-z]:[\\/][^\s:"\'\)]+|/(?:home|var|usr|etc|root|data)[^\s:"\'\)]+)', '<PATH>', raw_err)
        clean_err = logkit.redact(clean_err)
        return None, f"فشل المزود المحلي: {clean_err}"

    except FileNotFoundError:
        exec_name = cmd_args[0] if isinstance(cmd_args, list) and cmd_args else str(cmd)
        return None, logkit.redact(f"أمر المزود المحلي غير موجود (FileNotFoundError: {exec_name})")
    except Exception as e:
        if proc is not None:
            _kill_process_tree(proc)
        return None, f"خطأ استدعاء المزود المحلي ({type(e).__name__})"
    finally:
        _shelby_cli_lock.release()


_AR_DIACRITICS_RE = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_NOOP_PUNCT_RE = re.compile(r"[\.\,\،\؛\:\!\؟\?\-\–\—\"\'\(\)\[\]\{\}«»…]+")


def _normalize_for_noop(text):
    s = str(text or "").strip().lower()
    s = _AR_DIACRITICS_RE.sub("", s)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = _NOOP_PUNCT_RE.sub(" ", s)
    return " ".join(s.split())


def is_shelby_noop(raw_text, local_text, role_id=None):
    """هل أعاد المزود المحلي النص كما هو دون تنظيم فعلي؟ (BUG-05 / B-10)"""
    if not raw_text or not local_text:
        return False
    if role_id == "smart_verbatim":
        return False
    if str(raw_text).strip().split() == str(local_text).strip().split():
        return True
    norm_raw = _normalize_for_noop(raw_text)
    norm_local = _normalize_for_noop(local_text)
    if not norm_raw or not norm_local:
        return False
    if len(norm_raw) >= 80 and len(norm_raw.split()) >= 12:
        return difflib.SequenceMatcher(None, norm_raw, norm_local).ratio() >= 0.98
    return False


def note_shelby_noop():
    """يسجّل حدوث استجابة No-Op من المزود المحلي (thread-safe)."""
    with _lock:
        _state["local_noop"] = _state.get("local_noop", 0) + 1


def polish_gemini(raw_text, role_id=None, glossary=None, context_summary=None):
    """تنظيم نصي مباشر عبر Gemini Flash-Lite (السحابي فقط) مع تطبيق القاموس."""
    assert_not_forbidden_assist_model()
    if is_effective_empty(raw_text):
        return None, "نص فارغ"
    role_id = role_id or Config.DEFAULT_ROLE
    system_text = prompts.compose_prompt("polish", role_id)
    if context_summary:
        system_text += ("\n\nملخص الجلسة حتى الآن (للفهم فقط — لا تضفه ولا تعِد كتابته، نظّم النص الجديد فقط):\n"
                        + str(context_summary)[:Config.SESSION_SUMMARY_MAX_CHARS])
    text, reason = generate(system_text, raw_text)
    if text is None:
        return None, logkit.redact(reason)
    if glossary is not None:
        text, _n = glossary.apply(text, target="polished")
    return text.strip(), None


def polish_local(raw_text, role_id=None, glossary=None, context_summary=None):
    """تنظيم نصي مباشر عبر المزود المحلي المستقل (LOCAL_POLISH_CMD) مع تطبيق القاموس."""
    if is_effective_empty(raw_text):
        return None, "نص فارغ"
    if not getattr(Config, "LOCAL_POLISH_CMD", ""):
        return None, "لا يوجد مزود محلي مهيأ"
    role_id = role_id or Config.DEFAULT_ROLE
    system_text = prompts.compose_prompt("polish", role_id)
    if context_summary:
        system_text += ("\n\nملخص الجلسة حتى الآن (للفهم فقط — لا تضفه ولا تعِد كتابته، نظّم النص الجديد فقط):\n"
                        + str(context_summary)[:Config.SESSION_SUMMARY_MAX_CHARS])
    text, reason = _call_local_provider(system_text, raw_text)
    if text:
        if is_shelby_noop(raw_text, text, role_id=role_id):
            with _lock:
                _state["local_calls"] = _state.get("local_calls", 0) + 1
                _state["local_noop"] = _state.get("local_noop", 0) + 1
            return None, "أعاد شلبي النص دون تنظيم فعلي"
        with _lock:
            _state["local_calls"] = _state.get("local_calls", 0) + 1
            _state["local_ok"] = _state.get("local_ok", 0) + 1
        if glossary is not None:
            text, _n = glossary.apply(text, target="polished")
        return text.strip(), None
    reason = logkit.redact(reason)
    with _lock:
        _state["local_calls"] = _state.get("local_calls", 0) + 1
        _state["local_fail"] = _state.get("local_fail", 0) + 1
        _state["local_last_error"] = str(reason or "")
    return None, reason


def polish(raw_text, role_id=None, glossary=None, context_summary=None):
    """
    يحوّل الخام إلى منظم (طبقة B). يرجع (polished, None) أو (None, reason). لا يلمس الخام ولا الصوت.
    القاموس (scope polished) يُطبَّق على الناتج.
    context_summary (10 §2 L2): ملخص الجلسة التراكمي — يُضاف لنص النظام «للفهم فقط»؛ لا يُعاد كتابته ولا يُدمج في الناتج.
    يدعم المزود المحلي المستقل (LOCAL_POLISH_CMD) مع التحويل التلقائي لـ Gemini Flash-Lite.
    """
    assert_not_forbidden_assist_model()
    if is_effective_empty(raw_text):
        return None, "نص فارغ"
    role_id = role_id or Config.DEFAULT_ROLE
    system_text = prompts.compose_prompt("polish", role_id)
    if context_summary:
        system_text += ("\n\nملخص الجلسة حتى الآن (للفهم فقط — لا تضفه ولا تعِد كتابته، نظّم النص الجديد فقط):\n"
                        + str(context_summary)[:Config.SESSION_SUMMARY_MAX_CHARS])

    text = None
    reason = None
    # 1. محاولة المزود المحلي أولاً إذا كان معرّفاً
    if getattr(Config, "LOCAL_POLISH_CMD", ""):
        text, reason = _call_local_provider(system_text, raw_text)
        if text:
            if is_shelby_noop(raw_text, text, role_id=role_id):
                with _lock:
                    _state["local_calls"] = _state.get("local_calls", 0) + 1
                    _state["local_noop"] = _state.get("local_noop", 0) + 1
                logger.warning("⚠️ المزود المحلي أعاد النص دون تنظيم فعلي (No-Op) — تحويل تلقائي إلى Gemini...")
                text = None
            else:
                with _lock:
                    _state["local_calls"] = _state.get("local_calls", 0) + 1
                    _state["local_ok"] = _state.get("local_ok", 0) + 1
        else:
            reason = logkit.redact(reason)
            with _lock:
                _state["local_calls"] = _state.get("local_calls", 0) + 1
                _state["local_fail"] = _state.get("local_fail", 0) + 1
                _state["local_last_error"] = str(reason or "")
            logger.warning("⚠️ المزود المحلي: %s — تحويل تلقائي فوري إلى Gemini...", reason)

    # 2. المسار السحابي الافتراضي (Gemini Flash-Lite) لو المحلي غير مفعل أو فشل
    if text is None:
        text, reason = generate(system_text, raw_text)

    if text is None:
        return None, logkit.redact(reason)
    if glossary is not None:
        text, _n = glossary.apply(text, target="polished")
    return text.strip(), None


def get_last_synth_provider():
    with _lock:
        return _state.get("synth_last_provider", "shelby")


def synthesize(raw_text, gemini_draft=None, shelby_draft=None, role_id=None, glossary=None, context_summary=None, return_provider=False):
    """
    يستدعي الوكيل الثالث (السبّاك النهائي Master Synthesizer) لسبك مسودتي جيميني وشلبي
    في ناتج ذهبي موحد فائق القوة والدقة.
    المحرك الأساسي: شلبي محلياً (_call_local_provider(stage="synthesis")).
    الاحتياطي التلقائي: جيميني فلاش (generate) في حال تعذر شلبي أو انتهاء مهلته أو No-Op.
    يرجع (golden_text, None) عند النجاح، أو (None, reason) عند الفشل.
    إذا طُلب return_provider=True يرجع (golden_text, err, provider).
    """
    assert_not_forbidden_assist_model()
    if is_effective_empty(raw_text):
        return (None, "نص فارغ", "none") if return_provider else (None, "نص فارغ")

    role_id = role_id or Config.DEFAULT_ROLE
    system_text, user_content = prompts.compose_synthesis_prompt(
        raw_text=raw_text,
        gemini_draft=gemini_draft,
        shelby_draft=shelby_draft,
        role_id=role_id,
    )
    if context_summary:
        system_text += (
            "\n\nملخص الجلسة حتى الآن (للفهم فقط — لا تضفه ولا تعِد كتابته، اسبك النص الجديد فقط):\n"
            + str(context_summary)[:Config.SESSION_SUMMARY_MAX_CHARS]
        )

    with _lock:
        _state["synth_calls"] = _state.get("synth_calls", 0) + 1

    local_cmd = getattr(Config, "LOCAL_POLISH_CMD", "") or ""
    shelby_success = False
    text = None
    reason = None
    provider = "none"

    # 1. المحاولة الأساسية: شلبي محلياً
    if local_cmd:
        shelby_timeout = float(getattr(Config, "LOCAL_POLISH_TIMEOUT", 180.0) or 180.0)
        text, reason = _call_local_provider(
            system_text,
            user_content,
            stage="synthesis",
            timeout=shelby_timeout,
            priority=True,
        )
        if text:
            if not is_shelby_noop(raw_text, text, role_id=role_id):
                shelby_success = True
                provider = "shelby"
            else:
                note_shelby_noop()
                logger.warning("⚠️ شلبي أعاد النص دون تنظيم فعلي في السبك (No-Op) — ارتداد لفلاش")
                text = None
                reason = "شلبي أعاد نصاً غير مسبوك (No-Op)"
        else:
            logger.warning("⚠️ تعذر سبك شلبي محلياً (%s) — ارتداد لفلاش", reason)

    # 2. الاحتياطي التلقائي: جيميني فلاش
    if not shelby_success:
        with _lock:
            _state["synth_fallback_calls"] = _state.get("synth_fallback_calls", 0) + 1
        text, reason = generate(system_text, user_content)
        if text:
            provider = "flash"

    if text:
        with _lock:
            _state["synth_ok"] = _state.get("synth_ok", 0) + 1
            _state["synth_last_provider"] = provider
        if glossary is not None:
            text, _n = glossary.apply(text, target="polished")
        out_text = text.strip()
        return (out_text, None, provider) if return_provider else (out_text, None)

    reason = logkit.redact(reason)
    with _lock:
        _state["synth_fail"] = _state.get("synth_fail", 0) + 1
        _state["synth_last_error"] = str(reason or "")
        _state["synth_last_provider"] = "none"
    return (None, reason, "none") if return_provider else (None, reason)


def synthesize_ex(raw_text, gemini_draft=None, shelby_draft=None, role_id=None, glossary=None, context_summary=None, flow_start=None):
    """
    آلة حالات السبك المحكمة (Master Synthesizer State Machine) بميزانية مطلقة 420ث:
      1. SYNTH_SHELBY2: محاولة السبك بمزود شلبي المحلي (timeout <= min(180, remaining)).
      2. SYNTH_FLASH_FALLBACK: ارتداد فوري إلى Gemini Flash-Lite ضمن المتبقي فقط.
      3. RESCUE_DRAFT_OR_RAW: إنقاذ بأفضل مسودة متوفرة أو النص الخام.
    يرجع قاموساً موحداً:
      {
        "text": <str>,
        "source_tag": "shelby2" | "flash_fallback" | "rescue_shelby_draft" | "rescue_gemini_draft" | "rescue_raw" | "empty",
        "fallback_used": <bool>,
        "error_reason": <str or None>
      }
    """
    assert_not_forbidden_assist_model()
    if is_effective_empty(raw_text):
        return {
            "text": None,
            "source_tag": "empty",
            "fallback_used": False,
            "error_reason": "نص فارغ",
        }

    flow_start = flow_start if flow_start is not None else time.monotonic()
    total_budget = getattr(Config, "SYNTH_TOTAL_BUDGET_SEC", 420.0)
    total_budget_deadline = flow_start + total_budget
    min_exec = getattr(Config, "MIN_SHELBY_EXEC_SEC", 5.0)

    role_id = role_id or Config.DEFAULT_ROLE
    system_text, user_content = prompts.compose_synthesis_prompt(
        raw_text=raw_text,
        gemini_draft=gemini_draft,
        shelby_draft=shelby_draft,
        role_id=role_id,
    )
    if context_summary:
        system_text += (
            "\n\nملخص الجلسة حتى الآن (للفهم فقط — لا تضفه ولا تعِد كتابته، اسبك النص الجديد فقط):\n"
            + str(context_summary)[:Config.SESSION_SUMMARY_MAX_CHARS]
        )

    last_error = None

    # المرحلة 2 - أ: السبّاك الأساسي شلبي محلياً
    local_cmd = getattr(Config, "LOCAL_POLISH_CMD", "") or ""
    now = time.monotonic()
    remaining = total_budget_deadline - now

    if local_cmd and remaining >= min_exec:
        shelby_timeout = min(getattr(Config, "LOCAL_POLISH_TIMEOUT", 180.0), remaining)
        with _lock:
            _state["synth_calls"] = _state.get("synth_calls", 0) + 1

        text, reason = _call_local_provider(
            system_text,
            user_content,
            stage="synthesis",
            timeout=shelby_timeout,
            priority=True,
        )
        if text:
            if not is_shelby_noop(raw_text, text, role_id=role_id):
                with _lock:
                    _state["synth_ok"] = _state.get("synth_ok", 0) + 1
                if glossary is not None:
                    text, _n = glossary.apply(text, target="polished")
                return {
                    "text": text.strip(),
                    "source_tag": "shelby2",
                    "fallback_used": False,
                    "error_reason": None,
                }
            else:
                note_shelby_noop()
                last_error = "المزود المحلي أعاد النص دون تنظيم فعلي في السبك (No-Op)"
                with _lock:
                    _state["synth_fail"] = _state.get("synth_fail", 0) + 1
                    _state["synth_last_error"] = last_error
        else:
            last_error = reason
            with _lock:
                _state["synth_fail"] = _state.get("synth_fail", 0) + 1
                _state["synth_last_error"] = str(reason or "")
    elif local_cmd and remaining < min_exec:
        last_error = f"نفاد ميزانية السبك لشلبي (المتبقي {remaining:.1f}ث < {min_exec}ث)"
        logger.warning("⏱️ %s", last_error)

    # المرحلة 2 - ب: احتياطي السبك Gemini Flash-Lite
    now = time.monotonic()
    remaining = total_budget_deadline - now
    if remaining > 0:
        with _lock:
            _state["synth_calls"] = _state.get("synth_calls", 0) + 1
            _state["synth_fallback_calls"] = _state.get("synth_fallback_calls", 0) + 1

        text, reason = generate(system_text, user_content)
        if text:
            with _lock:
                _state["synth_ok"] = _state.get("synth_ok", 0) + 1
            if glossary is not None:
                text, _n = glossary.apply(text, target="polished")
            return {
                "text": text.strip(),
                "source_tag": "flash_fallback",
                "fallback_used": True,
                "error_reason": None,
            }
        else:
            last_error = reason or last_error
            with _lock:
                _state["synth_fail"] = _state.get("synth_fail", 0) + 1
                _state["synth_last_error"] = str(reason or "")
    else:
        last_error = f"نفاد الميزانية الإجمالية للسبك ({total_budget}ث)"

    # المرحلة 2 - ج: إنقاذ بأفضل مسودة متوفرة أو النص الخام
    s_clean = (shelby_draft or "").strip()
    g_clean = (gemini_draft or "").strip()
    raw_clean = (raw_text or "").strip()

    if s_clean and not is_shelby_noop(raw_clean, s_clean, role_id=role_id):
        rescue_text = s_clean
        source_tag = "rescue_shelby_draft"
    elif g_clean:
        rescue_text = g_clean
        source_tag = "rescue_gemini_draft"
    else:
        rescue_text = raw_clean
        source_tag = "rescue_raw"

    if glossary is not None:
        rescue_text, _n = glossary.apply(rescue_text, target="polished")

    return {
        "text": rescue_text,
        "source_tag": source_tag,
        "fallback_used": True,
        "error_reason": logkit.redact(str(last_error or "فشل السبك والارتداد للمسودة/الخام")),
    }


def stats():
    with _lock:
        return dict(_state)
