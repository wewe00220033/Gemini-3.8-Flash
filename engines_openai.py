# -*- coding: utf-8 -*-
"""
engines_openai.py — مشغّل OpenAICompatDriver الموحد (T2 — DEC-P2-08 / DEC-P2-09).

يغطي Groq / Cerebras / DeepSeek / OpenRouter / OpenAI / Together / Ollama / LM Studio / vLLM
ببروتوكول `/chat/completions` واحد مع معالجة `<think>` و`reasoning_content` وترويسات `Retry-After`.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time

import requests

import netsession
import text_assist

_THINK_CLOSED_RE = re.compile(r"(?is)<think>.*?</think>")
_THINK_UNCLOSED_RE = re.compile(r"(?is)<think>.*$")
_DAILY_QUOTA_RE = re.compile(r"per[\s_]*day|daily|quota|insufficient_quota|billing", re.IGNORECASE)
_DURATION_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)", re.IGNORECASE)


def parse_duration_token(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        val = float(s)
        return max(0.0, val) if val >= 0 else None
    except ValueError:
        pass
    matches = list(_DURATION_PART_RE.finditer(s))
    if matches and "".join(m.group(0) for m in matches) == s.replace(" ", ""):
        total = 0.0
        for m in matches:
            num = float(m.group(1))
            unit = m.group(2).lower()
            if unit == "ms":
                total += num / 1000.0
            elif unit == "s":
                total += num
            elif unit == "m":
                total += num * 60.0
            elif unit == "h":
                total += num * 3600.0
        return max(0.0, total)
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def parse_retry_after(headers):
    if not headers:
        return None
    lowered = {str(k).lower(): v for k, v in dict(headers).items()}
    for key in ("retry-after", "retry-after-ms", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        if key not in lowered:
            continue
        raw = str(lowered[key]).strip()
        if key == "retry-after-ms":
            try:
                return max(0.0, float(raw) / 1000.0)
            except ValueError:
                continue
        parsed = parse_duration_token(raw)
        if parsed is not None:
            return round(parsed, 3)
    return None


def extract_openai_error(resp):
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or err.get("code") or err.get("type") or "")[:240]
            if isinstance(err, str):
                return err[:240]
        return str(data)[:240]
    except Exception:
        return str(getattr(resp, "text", "") or "")[:240]


def extract_openai_content(msg, strip_think_tags=True):
    raw_content = msg.get("content") if isinstance(msg, dict) else None
    parts = []
    if isinstance(raw_content, str):
        parts.append(raw_content)
    elif isinstance(raw_content, list):
        for item in raw_content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                btype = item.get("type")
                if btype in (None, "text", "output_text") and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    joined = "\n".join(p for p in parts if p)
    had_think = False
    if strip_think_tags and joined:
        if _THINK_CLOSED_RE.search(joined) or _THINK_UNCLOSED_RE.search(joined):
            had_think = True
        joined = _THINK_CLOSED_RE.sub("", joined)
        joined = _THINK_UNCLOSED_RE.sub("", joined)
    return joined.strip(), had_think


class OpenAICompatDriver:
    """مشغّل HTTP قياسي بلا تبعية دائرية مع engines.py."""

    def __init__(self, result_cls, get_semaphore_fn, get_queue_lock_fn, clean_error_fn, lock_wait_fn):
        self._result = result_cls
        self._get_sem = get_semaphore_fn
        self._get_queue = get_queue_lock_fn
        self._clean_err = clean_error_fn
        self._lock_wait = lock_wait_fn

    def complete(self, prov_name, prov_cfg, system_prompt, user_prompt, **opts):
        model = str(opts.get("model") or prov_cfg.get("model") or "").strip()
        base_url = str(prov_cfg.get("base_url") or "").strip().rstrip("/")
        api_key = str(prov_cfg.get("api_key") or "").strip()
        timeout_s = float(opts.get("timeout_s") or prov_cfg.get("timeout_s") or 60.0)
        connect_s = float(prov_cfg.get("connect_timeout_s") or 10.0)
        strip_think = bool(prov_cfg.get("strip_think_tags", True))
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": str(system_prompt)},
                {"role": "user", "content": str(user_prompt)},
            ],
        }
        temp = opts.get("temperature", prov_cfg.get("temperature"))
        if temp is not None:
            body["temperature"] = float(temp)
        max_tokens = opts.get("max_tokens", prov_cfg.get("max_tokens"))
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        extra_body = prov_cfg.get("extra_body")
        if isinstance(extra_body, dict):
            for k, v in extra_body.items():
                if k not in ("model", "messages"):
                    body[k] = v

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        post_fn = opts.get("post_fn") or netsession.post
        queue_name = str(prov_cfg.get("queue") or "").strip()

        t0 = time.monotonic()
        if queue_name:
            q_lock = self._get_queue(queue_name, max_waiters=opts.get("max_waiters", 8))
            lock_wait = self._lock_wait(prov_cfg, timeout_s, opts)
            if not q_lock.acquire(timeout=lock_wait, priority=bool(opts.get("priority", False))):
                return self._result("try_next", error=f"طابور '{queue_name}' مشغول (تجاوز انتظار القفل {lock_wait:.0f}ث)",
                                    provider=prov_name, model=model, elapsed_s=time.monotonic() - t0)
            try:
                return self._do_post(post_fn, url, body, headers, (connect_s, timeout_s), strip_think, prov_name, model, t0)
            finally:
                q_lock.release()

        sem = self._get_sem(prov_name, prov_cfg.get("max_concurrent", 2))
        with sem:
            return self._do_post(post_fn, url, body, headers, (connect_s, timeout_s), strip_think, prov_name, model, t0)

    def _do_post(self, post_fn, url, body, headers, timeout_tuple, strip_think, prov_name, model, t0):
        try:
            resp = post_fn(url, json=body, headers=headers, timeout=timeout_tuple)
        except (requests.exceptions.RequestException, OSError) as e:
            return self._result("retry_same", error=self._clean_err(f"شبكة: {type(e).__name__}"),
                                provider=prov_name, model=model, elapsed_s=time.monotonic() - t0)
        except Exception as e:
            return self._result("fatal", error=self._clean_err(f"خطأ غير متوقع: {type(e).__name__}"),
                                provider=prov_name, model=model, elapsed_s=time.monotonic() - t0)

        elapsed = time.monotonic() - t0
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            try:
                data = resp.json()
            except Exception:
                return self._result("try_next", error="استجابة غير JSON صالحة من المزود",
                                    provider=prov_name, model=model, elapsed_s=elapsed)
            choices = data.get("choices") if isinstance(data, dict) else None
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            msg = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            text, had_think = extract_openai_content(msg, strip_think_tags=strip_think)
            if text and not text_assist.is_effective_empty(text):
                return self._result("ok", text=text, provider=prov_name, model=model, elapsed_s=elapsed)
            has_reasoning = bool(had_think or msg.get("reasoning_content") or msg.get("reasoning") or choice.get("finish_reason") == "length")
            if has_reasoning:
                return self._result(
                    "fatal",
                    error="استنفد الموديل سقف التوكنات داخل التفكير دون نص نهائي — زوّد max_tokens أو استخدم موديلاً غير تفكيري",
                    provider=prov_name,
                    model=model,
                    elapsed_s=elapsed,
                )
            return self._result("try_next", error="رد فارغ من المزود", provider=prov_name, model=model, elapsed_s=elapsed)

        detail = extract_openai_error(resp)
        ra = parse_retry_after(getattr(resp, "headers", None))
        if status == 429:
            if ra is None and _DAILY_QUOTA_RE.search(detail):
                return self._result("try_next", error=self._clean_err(f"نفاد حصة المزود (429): {detail}"),
                                    provider=prov_name, model=model, elapsed_s=elapsed)
            return self._result("retry_same", error=self._clean_err(f"تهدئة 429: {detail}".strip()),
                                retry_after_s=ra, provider=prov_name, model=model, elapsed_s=elapsed)
        if status >= 500 or status == 408:
            return self._result("retry_same", error=self._clean_err(f"خطأ خادم المزود ({status}): {detail}".strip()),
                                retry_after_s=ra, provider=prov_name, model=model, elapsed_s=elapsed)
        if status in (400, 401, 403, 404, 422):
            return self._result("fatal", error=self._clean_err(f"رفض الطلب ({status}): {detail}".strip()),
                                provider=prov_name, model=model, elapsed_s=elapsed)
        return self._result("try_next", error=self._clean_err(f"رد غير متوقع ({status}): {detail}".strip()),
                            provider=prov_name, model=model, elapsed_s=elapsed)
