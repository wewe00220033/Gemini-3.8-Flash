# -*- coding: utf-8 -*-
"""
pipeline_runner.py — محرك تشغيل مراحل الـ Pipeline المعزول (T6a — DEC-P2-05 / DEC-P2-08 / DEC-P2-09).
- محرك نقي (Pure Engine) معزول تماماً عن Telegram و delivery و web_service.
- لا يعتمد على أي ملفات تكوين وقت الاستيراد (Zero Import-Time Config Dependency).
- يدير التوازي المتوازن عبر ThreadPoolExecutor مع إغلاق رشيق.
- يدير سلاسل البدائل المتعاقبة (only_if_previous_failed) بدقة.
- ينفذ صمامات المهل الزمنية و deadline و min_success وإلغاء cancel_event.
- يصدر أحداث on_progress(event, call) لـ (started, ok, failed, skipped).
"""

from dataclasses import dataclass
import importlib
from typing import Optional, Callable, List, Dict, Any
import concurrent.futures
import logging
import threading
import time

logger = logging.getLogger("alsami.pipeline_runner")

VALID_CALL_STATUSES = {"started", "ok", "failed", "skipped", "fatal"}
VALID_STAGE_STATUSES = {"ok", "partial", "failed"}


@dataclass(frozen=True)
class CallResult:
    call_id: str
    provider: str
    model: str
    status: str          # "ok" | "failed" | "skipped" | "fatal"
    text: Optional[str] = None
    label: Optional[str] = None
    elapsed_s: float = 0.0
    error: Optional[str] = None


@dataclass(frozen=True)
class StageResult:
    stage_name: str
    status: str          # "ok" | "partial" | "failed"
    calls: List[CallResult]
    final_output: Optional[str] = None
    elapsed_s: float = 0.0
    error: Optional[str] = None


def _partition_chains(calls: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    يقسم قائمة نداءات المرحلة إلى سلاسل تنفيذ مستقلة:
    - النداء الأساسي يبدأ سلسلة جديدة تنفذ بالتوازي مع باقي السلاسل.
    - النداء الذي يحمل only_if_previous_failed ينضم لسلسلة النداء السابق كبديل متعاقب.
    """
    chains = []
    current_chain = []
    for c in calls:
        if c.get("only_if_previous_failed") and current_chain:
            current_chain.append(c)
        else:
            if current_chain:
                chains.append(current_chain)
            current_chain = [c]
    if current_chain:
        chains.append(current_chain)
    return chains


def _execute_call_with_retry(
    call_cfg: Dict[str, Any],
    providers: Dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    deadline_ts: float,
    default_retry_s: float,
    on_progress: Optional[Callable[[str, CallResult], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> CallResult:
    """تنفيذ نداء فردي مع دعم إعادة المحاولة لمرة واحدة عند retry_same (DEC-P2-05/09)."""
    call_id = str(call_cfg.get("id") or f"{call_cfg.get('provider')}_{call_cfg.get('repeat_index', 1)}")
    provider_name = str(call_cfg.get("provider") or "").strip()
    label = call_cfg.get("label") or provider_name
    p_cfg = dict(providers.get(provider_name) or {})
    model = str(call_cfg.get("model") or p_cfg.get("model") or "")

    for k in ("model", "temperature", "thinking_budget"):
        if k in call_cfg and call_cfg[k] is not None:
            p_cfg[k] = call_cfg[k]

    sys_p = call_cfg.get("system_prompt") if call_cfg.get("system_prompt") is not None else system_prompt
    usr_p = call_cfg.get("user_prompt") if call_cfg.get("user_prompt") is not None else user_prompt

    opts = {}
    for k in ("priority", "repeat_index", "repeat_total", "expected_queue_calls"):
        if k in call_cfg and call_cfg[k] is not None:
            opts[k] = call_cfg[k]

    call_timeout = float(call_cfg.get("timeout_s") or p_cfg.get("timeout_s") or 60.0)
    rem_deadline = max(0.01, deadline_ts - time.monotonic())
    opts["timeout_s"] = min(call_timeout, rem_deadline)

    # إصدار حدث البداية
    start_res = CallResult(
        call_id=call_id,
        provider=provider_name,
        model=model,
        status="started",
        label=label,
    )
    if on_progress:
        try:
            on_progress("started", start_res)
        except Exception as e:
            logger.debug("on_progress('started') error: %s", e)

    eng_mod = importlib.import_module("engines")
    t0 = time.monotonic()
    eng_res = eng_mod.complete(provider_name, p_cfg, sys_p, usr_p, **opts)
    elapsed = time.monotonic() - t0

    # 1. حالة النجاح
    if eng_res.status == "ok" and eng_res.text:
        res = CallResult(
            call_id=call_id,
            provider=provider_name,
            model=eng_res.model or model,
            status="ok",
            text=eng_res.text,
            label=label,
            elapsed_s=elapsed,
        )
        if on_progress:
            try:
                on_progress("ok", res)
            except Exception:
                pass
        return res

    # 2. حالة retry_same (إعادة محاولة لمرة واحدة فقط إن سمحت الميزانية — تصحيح 1)
    if eng_res.status == "retry_same":
        wait_s = float(eng_res.retry_after_s) if eng_res.retry_after_s is not None else default_retry_s
        rem = deadline_ts - time.monotonic()
        can_retry = (wait_s < rem) and not (cancel_event and cancel_event.is_set())
        if can_retry:
            if cancel_event:
                cancel_event.wait(timeout=wait_s)
            else:
                time.sleep(wait_s)

            if not (cancel_event and cancel_event.is_set()) and time.monotonic() < deadline_ts:
                t1 = time.monotonic()
                rem2 = max(0.01, deadline_ts - time.monotonic())
                opts["timeout_s"] = min(call_timeout, rem2)
                eng_res2 = eng_mod.complete(provider_name, p_cfg, sys_p, usr_p, **opts)
                elapsed += (time.monotonic() - t1)
                if eng_res2.status == "ok" and eng_res2.text:
                    res = CallResult(
                        call_id=call_id,
                        provider=provider_name,
                        model=eng_res2.model or model,
                        status="ok",
                        text=eng_res2.text,
                        label=label,
                        elapsed_s=elapsed,
                    )
                    if on_progress:
                        try:
                            on_progress("ok", res)
                        except Exception:
                            pass
                    return res
                else:
                    st = "fatal" if eng_res2.status == "fatal" else "failed"
                    res = CallResult(
                        call_id=call_id,
                        provider=provider_name,
                        model=eng_res2.model or model,
                        status=st,
                        label=label,
                        elapsed_s=elapsed,
                        error=eng_res2.error or "فشل بعد إعادة المحاولة (retry_same)",
                    )
                    if on_progress:
                        try:
                            on_progress("failed", res)
                        except Exception:
                            pass
                    return res

        # تعذر الانتظار بسبب ضيق الميزانية أو الإلغاء
        res = CallResult(
            call_id=call_id,
            provider=provider_name,
            model=eng_res.model or model,
            status="failed",
            label=label,
            elapsed_s=elapsed,
            error=eng_res.error or f"تجاوز المهلة المتاحة لإعادة المحاولة ({wait_s:.1f}s)",
        )
        if on_progress:
            try:
                on_progress("failed", res)
            except Exception:
                pass
        return res

    # 3. حالة fatal (خطأ تكوين أو عطل حاسم لا يقبل الإعادة — تصحيح 3)
    if eng_res.status == "fatal":
        res = CallResult(
            call_id=call_id,
            provider=provider_name,
            model=eng_res.model or model,
            status="fatal",
            label=label,
            elapsed_s=elapsed,
            error=eng_res.error or "خطأ إعداد أو رصيد غير قابل للمحاولة (fatal)",
        )
        if on_progress:
            try:
                on_progress("failed", res)
            except Exception:
                pass
        return res

    # 4. حالة try_next أو فشل عادي
    res = CallResult(
        call_id=call_id,
        provider=provider_name,
        model=eng_res.model or model,
        status="failed",
        label=label,
        elapsed_s=elapsed,
        error=eng_res.error or f"فشل النداء ({eng_res.status})",
    )
    if on_progress:
        try:
            on_progress("failed", res)
        except Exception:
            pass
    return res


def run_stage(
    stage_name: str,
    stage_config: Dict[str, Any],
    inputs: Optional[Dict[str, Any]] = None,
    providers: Optional[Dict[str, Any]] = None,
    deadline_s: Optional[float] = None,
    on_progress: Optional[Callable[[str, CallResult], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    system_prompt: str = "",
    user_prompt: str = "",
    default_retry_s: float = 3.0,
) -> StageResult:
    """
    تشغيل مرحلة Pipeline معزولة كاملة (T6a — DEC-P2-05 / DEC-P2-08 / DEC-P2-09).
    - تنفذ السلاسل المتوازية وفق max_concurrent.
    - تراعي min_success دون إلغاء النداءات المتوازية الجارية إلا عند نفاد المهلة.
    - تدعم سلاسل fallback (only_if_previous_failed) وتتخطاها عند نجاح السابق.
    - ترجع StageResult بحالة ok | partial | failed.
    """
    stage_t0 = time.monotonic()
    if not isinstance(stage_config, dict) or not stage_config.get("enabled", True):
        return StageResult(stage_name=stage_name, status="ok", calls=[], final_output=None, elapsed_s=0.0)

    calls = list(stage_config.get("calls") or [])
    if not calls:
        return StageResult(stage_name=stage_name, status="ok", calls=[], final_output=None, elapsed_s=0.0)

    prov_map = dict(providers or {})

    # حساب سقف المهلة إذا لم يُمرر صراحة
    if deadline_s is None:
        if stage_config.get("timeout_s") is not None:
            deadline_s = float(stage_config["timeout_s"])
        else:
            deadline_s = 60.0
    deadline_s = max(1.0, float(deadline_s or 60.0))
    deadline_ts = stage_t0 + deadline_s

    # استخراج النصوص من المدخلات إذا لم تُمرر
    if not user_prompt and inputs and isinstance(inputs, dict):
        user_prompt = str(inputs.get("raw") or "")

    chains = _partition_chains(calls)
    max_concurrent = int(stage_config.get("max_concurrent") or len(chains) or 1)
    max_concurrent = max(1, min(max_concurrent, len(chains)))

    chain_results: List[Optional[List[CallResult]]] = [None] * len(chains)

    def _chain_worker(chain_idx: int, chain: List[Dict[str, Any]]):
        results = []
        prev_succeeded = False
        for call_cfg in chain:
            # التحقق من الإلغاء أو انتهاء المهلة
            if (cancel_event and cancel_event.is_set()) or time.monotonic() >= deadline_ts:
                skip_res = CallResult(
                    call_id=str(call_cfg.get("id") or f"{call_cfg.get('provider')}_{call_cfg.get('repeat_index', 1)}"),
                    provider=str(call_cfg.get("provider", "")),
                    model=str(call_cfg.get("model", "")),
                    status="skipped",
                    label=call_cfg.get("label"),
                    error="تخطي بسبب انتهاء المهلة أو الإلغاء",
                )
                results.append(skip_res)
                if on_progress:
                    try:
                        on_progress("skipped", skip_res)
                    except Exception:
                        pass
                continue

            # تخطي البديل إذا نجح النداء السابق
            if call_cfg.get("only_if_previous_failed") and prev_succeeded:
                skip_res = CallResult(
                    call_id=str(call_cfg.get("id") or f"{call_cfg.get('provider')}_{call_cfg.get('repeat_index', 1)}"),
                    provider=str(call_cfg.get("provider", "")),
                    model=str(call_cfg.get("model", "")),
                    status="skipped",
                    label=call_cfg.get("label"),
                )
                results.append(skip_res)
                if on_progress:
                    try:
                        on_progress("skipped", skip_res)
                    except Exception:
                        pass
                continue

            call_res = _execute_call_with_retry(
                call_cfg=call_cfg,
                providers=prov_map,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                deadline_ts=deadline_ts,
                default_retry_s=default_retry_s,
                on_progress=on_progress,
                cancel_event=cancel_event,
            )
            results.append(call_res)
            prev_succeeded = (call_res.status == "ok")

        return chain_idx, results

    # تشغيل متوازن داخل ThreadPoolExecutor يغلق فور انتهاء المرحلة
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent) as executor:
        futures = [executor.submit(_chain_worker, i, ch) for i, ch in enumerate(chains)]
        for fut in concurrent.futures.as_completed(futures):
            try:
                idx, res_list = fut.result()
                chain_results[idx] = res_list
            except Exception as e:
                logger.warning("Pipeline chain worker failure: %s", e)

    # تجميع النتائج بترتيب التعريف الأصلي للنداءات
    all_calls: List[CallResult] = []
    for rlist in chain_results:
        if rlist:
            all_calls.extend(rlist)

    min_success = int(stage_config.get("min_success", 1))
    successful_calls = [c for c in all_calls if c.status == "ok"]
    n_success = len(successful_calls)

    if n_success >= min_success:
        stage_status = "ok"
        err = None
    elif n_success > 0:
        stage_status = "partial"
        err = f"نجح {n_success} فقط من أصل {min_success} نداء مطلوب"
    else:
        stage_status = "failed"
        err = "فشلت كافة النداءات في المرحلة"

    # الناتج النهائي: أول نداء ناجح وفق ترتيب الأولوية
    final_text = successful_calls[0].text if successful_calls else None
    elapsed_total = time.monotonic() - stage_t0

    return StageResult(
        stage_name=stage_name,
        status=stage_status,
        calls=all_calls,
        final_output=final_text,
        elapsed_s=elapsed_total,
        error=err,
    )
