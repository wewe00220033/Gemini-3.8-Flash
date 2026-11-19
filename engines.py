# -*- coding: utf-8 -*-
"""
engines.py — طبقة المحرّكات المتعددة القابلة للتركيب وعقد EngineResult الموحد (T2 — DEC-P2-08 / DEC-P2-09).

- سجل صريح `DRIVERS` في أسفل الملف بلا ديكوريتر (آمن مع `importlib.reload` وحزم النشر).
- 4 مشغّلات قياسية: `gemini_api`، `openai_compat` (في `engines_openai.py`)، `local_cli`، و`interactions_api`.
- كل الأخطاء تُنقَّح عبر `logkit.redact` ولا يخرج استثناء غير مصنَّف للمستدعي.
"""

from dataclasses import dataclass
import json
import os
import re
import shlex
import subprocess
import threading
import time
from typing import Optional

from config import Config
from engines_openai import OpenAICompatDriver
import interactions_client
import logkit
import text_assist
import transcriber

VALID_STATUSES = ("ok", "retry_same", "try_next", "fatal")

_LOCAL_PATH_RE = re.compile(r'([A-Za-z]:[\\/][^\s:"\'\)]+|/(?:home|var|usr|etc|root|data|tmp)[^\s:"\'\)]+)')

_state_lock = threading.Lock()
_semaphores = {}
_semaphore_caps = {}
_queue_locks = {}


@dataclass(frozen=True)
class EngineResult:
    status: str
    text: Optional[str] = None
    error: Optional[str] = None
    retry_after_s: Optional[float] = None
    provider: str = ""
    model: str = ""
    elapsed_s: float = 0.0

    def __post_init__(self):
        if self.status not in VALID_STATUSES:
            raise ValueError(f"حالة EngineResult غير صالحة: {self.status!r}")

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.text)


def reset_state():
    """للاختبارات: تصفير بوابات التوازي ومنسقات الطوابير."""
    with _state_lock:
        _semaphores.clear()
        _semaphore_caps.clear()
        _queue_locks.clear()


def get_semaphore(provider_name, max_concurrent=2):
    """بوابة توازي لكل مزود بالمفتاح provider_name فقط؛ وتحديث الكائن عند تغيّر cap (KI-01 — P3-02)."""
    name = str(provider_name)
    cap = max(1, int(max_concurrent or 1))
    with _state_lock:
        sem = _semaphores.get(name)
        old_cap = _semaphore_caps.get(name)
        if sem is None or old_cap != cap:
            sem = threading.BoundedSemaphore(cap)
            _semaphores[name] = sem
            _semaphore_caps[name] = cap
        return sem


def get_queue_lock(queue_name, max_waiters=8):
    q = str(queue_name or "gpu").strip() or "gpu"
    mw = max(1, int(max_waiters or 8))
    with _state_lock:
        coord = _queue_locks.get(q)
        if coord is None:
            coord = text_assist._ShelbyQueueCoordinator(max_waiters=mw)
            _queue_locks[q] = coord
        elif coord.max_waiters < mw:
            coord.max_waiters = mw
        return coord


def _clean_error(msg):
    if not msg:
        return None
    s = _LOCAL_PATH_RE.sub("<PATH>", str(msg))
    return logkit.redact(s)


def _effective_lock_wait(prov_cfg, timeout_s, opts):
    expected_calls = max(1, int(opts.get("expected_queue_calls") or opts.get("repeat_total") or 1))
    base_wait = float(prov_cfg.get("lock_wait_s", getattr(Config, "LOCAL_LOCK_WAIT_SEC", 45.0)) or 45.0)
    return max(base_wait, float(timeout_s) * max(0, expected_calls - 1))


def _argv_exceeds_os_limit(full_args, platform_name=None):
    plat = os.name if platform_name is None else platform_name
    if plat == "nt":
        return len(subprocess.list2cmdline([str(a) for a in full_args])) > 30000
    return any(len(str(a).encode("utf-8")) > 120 * 1024 for a in full_args)


class GeminiApiDriver:
    """يلفّ text_assist.generate المجرّبة دون إعادة كتابة حلقة المفاتيح أو منطق thinkingBudget."""

    def complete(self, prov_name, prov_cfg, system_prompt, user_prompt, **opts):
        model = str(opts.get("model") or prov_cfg.get("model") or Config.TEXT_ASSIST_MODEL).strip()
        timeout_s = float(opts.get("timeout_s") or prov_cfg.get("timeout_s") or Config.TEXT_ASSIST_TIMEOUT)
        connect_s = float(prov_cfg.get("connect_timeout_s") or Config.GEMINI_CONNECT_TIMEOUT)
        temp = opts.get("temperature", prov_cfg.get("temperature", 0.2))
        thinking_budget = opts.get("thinking_budget", prov_cfg.get("thinking_budget"))
        sem = get_semaphore(prov_name, prov_cfg.get("max_concurrent", 2))

        t0 = time.monotonic()
        try:
            text_assist.assert_not_forbidden_assist_model(model)
        except RuntimeError as e:
            return EngineResult("fatal", error=_clean_error(e), provider=prov_name, model=model, elapsed_s=time.monotonic() - t0)

        with sem:
            text, reason, outcome, retry_s = text_assist.generate(
                system_prompt,
                user_prompt,
                timeout=(connect_s, timeout_s),
                model=model,
                temperature=temp,
                thinking_budget=thinking_budget,
                return_outcome=True,
            )
        elapsed = time.monotonic() - t0
        if outcome == transcriber.SUCCESS and text and not text_assist.is_effective_empty(text):
            return EngineResult("ok", text=text.strip(), provider=prov_name, model=model, elapsed_s=elapsed)
        if outcome in (transcriber.KEY_RATE_LIMITED, transcriber.MODEL_DOWN, transcriber.MODEL_SLOW, transcriber.NETWORK):
            return EngineResult("retry_same", error=_clean_error(reason or outcome), retry_after_s=retry_s,
                                provider=prov_name, model=model, elapsed_s=elapsed)
        if outcome in (transcriber.BAD_REQUEST, transcriber.BLOCKED, transcriber.MODEL_NOT_FOUND):
            return EngineResult("fatal", error=_clean_error(reason or outcome), provider=prov_name, model=model, elapsed_s=elapsed)
        return EngineResult("try_next", error=_clean_error(reason or "تعذر استدعاء Gemini النصي"),
                            provider=prov_name, model=model, elapsed_s=elapsed)


class LocalCliDriver:
    """مشغّل محلي متعدد النسخ مع طابور مشترك `queue` وصيغ إدخال متعددة وقتل آمن لشجرة العمليات."""

    def complete(self, prov_name, prov_cfg, system_prompt, user_prompt, **opts):
        cmd = prov_cfg.get("command") or ""
        input_fmt = str(prov_cfg.get("input_format") or "stdin_delimited").strip()
        timeout_s = float(opts.get("timeout_s") or prov_cfg.get("timeout_s") or Config.LOCAL_POLISH_TIMEOUT)
        model_label = str(prov_cfg.get("model") or prov_name)
        t0 = time.monotonic()

        if input_fmt not in ("stdin_delimited", "stdin_json", "argv"):
            return EngineResult("fatal", error=f"صيغة input_format غير مدعومة: {input_fmt!r}",
                                provider=prov_name, model=model_label, elapsed_s=0.0)

        stdin_payload = None
        if input_fmt == "argv":
            if isinstance(cmd, str):
                base_args = [a.strip('"') for a in shlex.split(cmd, posix=(os.name != "nt"))]
            else:
                base_args = [str(a) for a in cmd]
            cmd_args = [*base_args, str(system_prompt), str(user_prompt)]
            if _argv_exceeds_os_limit(cmd_args, platform_name=opts.get("_platform_name")):
                return EngineResult(
                    "fatal",
                    error="النص يتجاوز سقف argv لنظام التشغيل — استخدم stdin_delimited أو stdin_json",
                    provider=prov_name,
                    model=model_label,
                    elapsed_s=time.monotonic() - t0,
                )
        else:
            cmd_args = cmd if (os.name == "nt" and isinstance(cmd, str)) else (shlex.split(cmd) if isinstance(cmd, str) else list(cmd))
            if input_fmt == "stdin_json":
                stdin_payload = json.dumps({"system": str(system_prompt), "user": str(user_prompt)}, ensure_ascii=False)
            else:
                stdin_payload = f"{system_prompt}\n\n===USER_INPUT===\n{user_prompt}"

        queue_name = str(prov_cfg.get("queue") or prov_name).strip()
        q_lock = get_queue_lock(queue_name, max_waiters=opts.get("max_waiters", 8))
        lock_wait = _effective_lock_wait(prov_cfg, timeout_s, opts)
        if not q_lock.acquire(timeout=lock_wait, priority=bool(opts.get("priority", False))):
            return EngineResult("try_next", error=f"طابور '{queue_name}' مشغول (تجاوز انتظار القفل {lock_wait:.0f}ث)",
                                provider=prov_name, model=model_label, elapsed_s=time.monotonic() - t0)

        popen_fn = opts.get("process_factory") or subprocess.Popen
        popen_kwargs = {
            "stdin": subprocess.PIPE if stdin_payload is not None else subprocess.DEVNULL,
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

        proc = None
        try:
            proc = popen_fn(cmd_args, **popen_kwargs)
            try:
                stdout_data, stderr_data = proc.communicate(input=stdin_payload, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                text_assist._kill_process_tree(proc)
                try:
                    proc.communicate(timeout=2)
                except Exception:
                    pass
                return EngineResult("retry_same", error=f"تجاوز مهلة المحرك المحلي ({timeout_s:.0f}ث)",
                                    provider=prov_name, model=model_label, elapsed_s=time.monotonic() - t0)

            elapsed = time.monotonic() - t0
            rc = 0 if proc.returncode is None else proc.returncode
            out_str = (stdout_data or "").strip()
            err_str = (stderr_data or "").strip()
            if rc == 0 and out_str and not text_assist.is_effective_empty(out_str):
                return EngineResult("ok", text=out_str, provider=prov_name, model=model_label, elapsed_s=elapsed)

            if err_str:
                raw_err = err_str.splitlines()[-1][:140]
            elif rc == 0:
                raw_err = "خرج بكود 0 لكن stdout فارغ"
            else:
                raw_err = f"خروج غير صفري ({rc})"
            return EngineResult("try_next", error=_clean_error(f"فشل المحرك المحلي: {raw_err}"),
                                provider=prov_name, model=model_label, elapsed_s=elapsed)
        except FileNotFoundError:
            return EngineResult("fatal", error=_clean_error(f"أمر المحرك المحلي غير موجود ({prov_name})"),
                                provider=prov_name, model=model_label, elapsed_s=time.monotonic() - t0)
        except Exception as e:
            if proc is not None:
                text_assist._kill_process_tree(proc)
            return EngineResult("try_next", error=_clean_error(f"خطأ استدعاء المحرك المحلي ({type(e).__name__})"),
                                provider=prov_name, model=model_label, elapsed_s=time.monotonic() - t0)
        finally:
            q_lock.release()


class InteractionsDriver:
    """يلفّ interactions_client.run مع فك قيد FEATURE_PLAN ودعم اسم وكيل مخصص."""

    def complete(self, prov_name, prov_cfg, system_prompt, user_prompt, **opts):
        agent_name = str(opts.get("agent") or prov_cfg.get("agent") or prov_cfg.get("model") or Config.AGENT_NAME).strip()
        timeout_s = float(opts.get("timeout_s") or prov_cfg.get("timeout_s") or Config.AGENT_TIMEOUT)
        connect_s = float(prov_cfg.get("connect_timeout_s") or Config.GEMINI_CONNECT_TIMEOUT)
        combined = f"{system_prompt}\n\n{user_prompt}".strip() if system_prompt else str(user_prompt)
        sem = get_semaphore(prov_name, prov_cfg.get("max_concurrent", 1))

        t0 = time.monotonic()
        with sem:
            text, reason, kind, retry_s = interactions_client.run(
                combined,
                timeout=(connect_s, timeout_s),
                post_fn=opts.get("post_fn"),
                agent_name=agent_name,
                ignore_feature_flag=True,
                return_outcome=True,
            )
        elapsed = time.monotonic() - t0
        if kind == "ok" and text and not text_assist.is_effective_empty(text):
            return EngineResult("ok", text=text.strip(), provider=prov_name, model=agent_name, elapsed_s=elapsed)
        status = kind if kind in VALID_STATUSES else "try_next"
        return EngineResult(status, error=_clean_error(reason), retry_after_s=retry_s,
                            provider=prov_name, model=agent_name, elapsed_s=elapsed)


DRIVERS = {
    "gemini_api": GeminiApiDriver(),
    "openai_compat": OpenAICompatDriver(EngineResult, get_semaphore, get_queue_lock, _clean_error, _effective_lock_wait),
    "local_cli": LocalCliDriver(),
    "interactions_api": InteractionsDriver(),
}


def complete(provider_name, provider_cfg, system_prompt, user_prompt, **opts):
    """نقطة الدخول الموحدة لكل المحرّكات: تفحص `_unresolved` ونوع المشغّل ثم تفوّض للـ Driver."""
    prov_name = str(provider_name or "").strip()
    if not isinstance(provider_cfg, dict):
        return EngineResult("fatal", error=f"إعداد المزود '{prov_name}' غير صالح", provider=prov_name)
    unresolved = provider_cfg.get("_unresolved") or []
    if unresolved:
        return EngineResult(
            "fatal",
            error=_clean_error(f"إعداد المزود '{prov_name}' يفتقد: {', '.join(str(u) for u in unresolved)}"),
            provider=prov_name,
            model=str(provider_cfg.get("model") or ""),
        )
    driver_type = str(provider_cfg.get("type") or "").strip()
    driver = DRIVERS.get(driver_type)
    if driver is None:
        return EngineResult("fatal", error=f"مشغّل غير معروف '{driver_type}' للمزود '{prov_name}'", provider=prov_name)
    return driver.complete(prov_name, provider_cfg, system_prompt, user_prompt, **opts)
