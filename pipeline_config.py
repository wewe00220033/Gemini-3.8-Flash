# -*- coding: utf-8 -*-
"""
pipeline_config.py — محمّل إعدادات خط التنظيم متعدد المحركات (T3a — DEC-P2-08 / DEC-P2-09).

- غياب `DATA_DIR/pipeline.json` على القرص => `build_default_pipeline()` المطابق 100% للسلوك الحالي.
- وجود `pipeline.json` => مصدر الحقيقة الوحيد (يتجاهل المفاتيح المتداخلة في `settings.json` بتحذير واحد)،
  مع `deep-merge`، استبعاد `_example_*`، تفكيك `repeat`، وفحص كسول `(st_mtime_ns, st_size)` بفاصل تهدئة >= 5ث.
- فك متغيرات البيئة `${VAR}` (أو `${VAR:-default}`) يحدث داخل `providers` فقط **وقت تحميل الملف** ويُحفظ في الكاش
  (تغيير متغير بيئة بدون تعديل `pipeline.json` أو إعادة التشغيل لا يُطبَّق تلقائياً).
- فشل قراءة/تحليل تعديل جديد يُبقي آخر نسخة صالحة في الذاكرة مع تحذير واحد لكل بصمة ملف تالفة.
"""

import copy
import json
import logging
import math
import os
import re
import threading
import time

from config import Config
import pipeline_defaults
import store

logger = logging.getLogger("alsami.pipeline_config")

MAX_CALL_REPEAT = 20
_POLL_INTERVAL_S = 5.0
OVERLAPPING_SETTINGS_KEYS = ("compare_mode", "local_polish_timeout")

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

_lock = threading.RLock()
_cached_disk_cfg = None
_cached_path = None
_cached_sig = None
_bad_sig = None
_last_check_mono = 0.0


def build_default_pipeline():
    return pipeline_defaults.build_default_pipeline()


def _deep_merge_dict(base, override):
    out = copy.deepcopy(base)
    if not isinstance(override, dict):
        return out
    for k, v in override.items():
        if str(k).startswith("_comment"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _expand_env_value(val, missing_vars):
    if isinstance(val, str):
        def _repl(m):
            var_name, fallback = m.group(1), m.group(2)
            env_val = os.environ.get(var_name, "")
            if env_val != "":
                return env_val
            if fallback is not None:
                return fallback
            if var_name not in missing_vars:
                missing_vars.append(var_name)
            return ""
        return _ENV_VAR_RE.sub(_repl, val)
    if isinstance(val, dict):
        return {k: _expand_env_value(v, missing_vars) for k, v in val.items()}
    if isinstance(val, list):
        return [_expand_env_value(x, missing_vars) for x in val]
    return copy.deepcopy(val)


def _normalize_providers(raw_providers):
    defaults = pipeline_defaults.builtin_providers()
    builtin_unresolved = {k: list(v.pop("_unresolved", [])) for k, v in defaults.items()}

    merged_map = copy.deepcopy(defaults)
    if isinstance(raw_providers, dict):
        for name, p_cfg in raw_providers.items():
            if not isinstance(name, str) or name.startswith("_") or not isinstance(p_cfg, dict):
                continue
            p_type = str(p_cfg.get("type") or merged_map.get(name, {}).get("type") or "").strip()
            type_base = copy.deepcopy(pipeline_defaults.PROVIDER_TYPE_DEFAULTS.get(p_type, {}))
            base = _deep_merge_dict(type_base, merged_map.get(name, {}))
            merged_map[name] = _deep_merge_dict(base, p_cfg)

    resolved = {}
    for name, p_cfg in merged_map.items():
        missing = []
        inherited_builtin = not (isinstance(raw_providers, dict) and name in raw_providers) and bool(builtin_unresolved.get(name))
        if inherited_builtin:
            missing.extend(builtin_unresolved[name])
        expanded = _expand_env_value(p_cfg, missing)
        p_type = str(expanded.get("type") or "").strip()
        if not inherited_builtin:
            for req_field in pipeline_defaults.REQUIRED_PROVIDER_FIELDS.get(p_type, ()):
                f_val = str(expanded.get(req_field) or "").strip()
                if not f_val:
                    raw_field = str(p_cfg.get(req_field) or "")
                    ref_vars = [m.group(1) for m in _ENV_VAR_RE.finditer(raw_field)]
                    if not any(v in missing for v in ref_vars) and req_field not in missing:
                        missing.append(req_field)
        expanded["_unresolved"] = missing
        if missing and isinstance(raw_providers, dict) and name in raw_providers:
            logger.warning("⚠️ pipeline.json: المزود '%s' يفتقد متغيرات/حقول مطلوبة: %s", name, ", ".join(missing))
        resolved[name] = expanded
    return resolved


def _normalize_calls(raw_calls, providers):
    if not isinstance(raw_calls, list):
        return []
    expanded = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        prov_name = str(item.get("provider") or "").strip()
        if not prov_name or prov_name.startswith("_") or prov_name not in providers:
            logger.warning("⚠️ pipeline.json: تجاهل نداء لمزود غير معرّف '%s'", prov_name)
            continue
        raw_rep = item.get("repeat", 1)
        try:
            rep = int(raw_rep)
        except (TypeError, ValueError):
            rep = 1
        if rep > MAX_CALL_REPEAT:
            logger.warning("⚠️ pipeline.json: تقليص repeat من %s إلى السقف %d للمزود '%s'", raw_rep, MAX_CALL_REPEAT, prov_name)
            rep = MAX_CALL_REPEAT
        rep = max(1, rep)
        base_label = str(item.get("label") or prov_name).strip() or prov_name
        for i in range(1, rep + 1):
            c = copy.deepcopy(item)
            c.pop("repeat", None)
            c["provider"] = prov_name
            c["label"] = base_label if rep == 1 else f"{base_label} #{i}"
            c["repeat_index"] = i
            c["repeat_total"] = rep
            expanded.append(c)
    return expanded


def _normalize_stages(raw_stages, providers, fallback_stages):
    if raw_stages is None:
        return copy.deepcopy(fallback_stages)
    if not isinstance(raw_stages, list):
        raise ValueError("حقل stages يجب أن يكون قائمة")
    out = []
    for idx, st in enumerate(raw_stages):
        if not isinstance(st, dict):
            raise ValueError(f"عنصر المرحلة رقم {idx} ليس قاموساً")
        name = str(st.get("name") or f"stage_{idx + 1}").strip()
        stage_doc = {
            "name": name,
            "enabled": bool(st.get("enabled", True)),
            "prompt": str(st.get("prompt") or "polish"),
            "inputs": list(st.get("inputs") or ["raw"]),
            "min_success": max(1, int(st.get("min_success", 1) or 1)),
            "calls": _normalize_calls(st.get("calls", []), providers),
        }
        if "fallback" in st:
            stage_doc["fallback"] = st["fallback"]
        if "timeout_s" in st and st["timeout_s"] is not None:
            stage_doc["timeout_s"] = float(st["timeout_s"])
        out.append(stage_doc)
    return out


def _warn_settings_overlap_once():
    settings_path = getattr(Config, "SETTINGS_FILE", "")
    if not settings_path or not os.path.isfile(settings_path):
        return
    raw_settings = store.load_json_tolerant(settings_path, default={})
    if not isinstance(raw_settings, dict):
        return
    overlapped = [k for k in OVERLAPPING_SETTINGS_KEYS if k in raw_settings and raw_settings[k] is not None]
    if overlapped:
        logger.warning("⚠️ pipeline.json نشط — تجاهل المفاتيح المتداخلة في settings.json: %s", ", ".join(overlapped))


def _parse_pipeline_doc(raw_doc):
    if not isinstance(raw_doc, dict):
        raise ValueError("جذر pipeline.json يجب أن يكون كائن JSON (dict)")
    base = build_default_pipeline()
    providers = _normalize_providers(raw_doc.get("providers"))
    stages = _normalize_stages(raw_doc.get("stages"), providers, base["stages"])
    return {
        "version": int(raw_doc.get("version", 1) or 1),
        "display": _deep_merge_dict(pipeline_defaults.DEFAULT_DISPLAY, raw_doc.get("display")),
        "providers": providers,
        "stages": stages,
        "guards": _deep_merge_dict(pipeline_defaults.DEFAULT_GUARDS, raw_doc.get("guards")),
        "speakers": _deep_merge_dict(pipeline_defaults.DEFAULT_SPEAKERS, raw_doc.get("speakers")),
        "memory": _deep_merge_dict(pipeline_defaults.DEFAULT_MEMORY, raw_doc.get("memory")),
        "hot_reload": _deep_merge_dict(pipeline_defaults.DEFAULT_HOT_RELOAD, raw_doc.get("hot_reload")),
    }


def has_disk_pipeline():
    path = getattr(Config, "PIPELINE_FILE", "")
    return bool(path and os.path.isfile(path))


def reset_cache():
    """للاختبارات: تصفير الكاش وبصمات الملفات."""
    global _cached_disk_cfg, _cached_path, _cached_sig, _bad_sig, _last_check_mono
    with _lock:
        _cached_disk_cfg = None
        _cached_path = None
        _cached_sig = None
        _bad_sig = None
        _last_check_mono = 0.0


def _effective_poll_interval(cfg):
    if cfg is None:
        return _POLL_INTERVAL_S
    hr = cfg.get("hot_reload") or {}
    if not hr.get("enabled", True):
        return float("inf")
    if _POLL_INTERVAL_S < 5.0:
        # السماح للاختبارات بتصفير _POLL_INTERVAL_S عبر monkeypatch بدون sleep
        return max(0.0, float(_POLL_INTERVAL_S))
    return max(5.0, float(hr.get("poll_interval_s", _POLL_INTERVAL_S) or _POLL_INTERVAL_S))


def get(force=False):
    """يرجع نسخة عميقة آمنة من إعداد خط التنظيم الفعّال مع فحص كسول `(st_mtime_ns, st_size)`.
    عند وجود `pipeline.json` على القرص تُلتقط متغيرات البيئة `${VAR}` وقيم المزودات المدمجة وقت التحميل وتُحفظ في الكاش حتى يتغير الملف أو يُعاد التشغيل."""
    global _cached_disk_cfg, _cached_path, _cached_sig, _bad_sig, _last_check_mono
    path = getattr(Config, "PIPELINE_FILE", "")
    now = time.monotonic()

    if _cached_disk_cfg is not None and _cached_path == path and not force:
        if (now - _last_check_mono) < _effective_poll_interval(_cached_disk_cfg):
            return copy.deepcopy(_cached_disk_cfg)

    with _lock:
        now = time.monotonic()
        if _cached_disk_cfg is not None and _cached_path == path and not force:
            if (now - _last_check_mono) < _effective_poll_interval(_cached_disk_cfg):
                return copy.deepcopy(_cached_disk_cfg)

        _last_check_mono = now
        try:
            st = os.stat(path) if path else None
        except OSError:
            st = None

        if st is None:
            _cached_disk_cfg = None
            _cached_path = path
            _cached_sig = None
            _bad_sig = None
            return build_default_pipeline()

        sig = (st.st_mtime_ns, st.st_size)
        if _cached_disk_cfg is not None and _cached_path == path and _cached_sig == sig:
            return copy.deepcopy(_cached_disk_cfg)

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_doc = json.load(f)
            parsed = _parse_pipeline_doc(raw_doc)
        except Exception as e:
            if _bad_sig != sig:
                _bad_sig = sig
                logger.warning("⚠️ تعذر تحميل pipeline.json (%s: %s) — الإبقاء على آخر إعداد صالح", type(e).__name__, e)
            if _cached_disk_cfg is not None and _cached_path == path:
                return copy.deepcopy(_cached_disk_cfg)
            return build_default_pipeline()

        _cached_disk_cfg = parsed
        _cached_path = path
        _cached_sig = sig
        _bad_sig = None
        _warn_settings_overlap_once()
        return copy.deepcopy(_cached_disk_cfg)


def stage_deadline_sec(stage, providers=None):
    """سقف أمان زمني أعلى للمرحلة بالثواني (ليس تقديراً لزمن الاستجابة المعتاد)."""
    if not isinstance(stage, dict) or not stage.get("enabled", True):
        return 0.0
    if stage.get("timeout_s") is not None:
        return max(1.0, float(stage["timeout_s"]))
    calls = stage.get("calls") or []
    if not calls:
        return 0.0
    if providers is None:
        providers = get().get("providers", {})

    parallel_groups = {}
    fallback_chain_total = 0.0

    for c in calls:
        p_name = c.get("provider", "")
        p_cfg = providers.get(p_name) or {}
        t_sec = float(c.get("timeout_s") or p_cfg.get("timeout_s") or 60.0)
        if c.get("only_if_previous_failed"):
            fallback_chain_total += t_sec
            continue
        q_name = p_cfg.get("queue")
        if q_name or p_cfg.get("type") == "local_cli":
            key = f"queue:{q_name or p_name}"
            parallel_groups[key] = parallel_groups.get(key, 0.0) + t_sec
        else:
            key = f"api:{p_name}"
            grp = parallel_groups.setdefault(key, {"count": 0, "max_t": 0.0, "conc": max(1, int(p_cfg.get("max_concurrent", 2) or 2))})
            grp["count"] += 1
            grp["max_t"] = max(grp["max_t"], t_sec)

    max_parallel = 0.0
    for val in parallel_groups.values():
        if isinstance(val, dict):
            waves = math.ceil(val["count"] / val["conc"])
            max_parallel = max(max_parallel, waves * val["max_t"])
        else:
            max_parallel = max(max_parallel, float(val))

    return max_parallel + fallback_chain_total


def pipeline_deadline_sec(cfg=None):
    """سقف أمان زمني أعلى لخط التنظيم الكامل بالثواني (مجموع أسقف المراحل المفعّلة + deadline_margin_s؛ ليس تقديراً)."""
    cfg = get() if cfg is None else cfg
    providers = cfg.get("providers") or {}
    stages_sum = sum(stage_deadline_sec(st, providers) for st in (cfg.get("stages") or []) if st.get("enabled", True))
    if stages_sum <= 0.0:
        return 0.0
    margin = float((cfg.get("display") or {}).get("deadline_margin_s", 20.0) or 0.0)
    return stages_sum + margin


def queue_max_waiters(queue_name="gpu", cfg=None):
    """سعة انتظار منسق الطابور المحلي بناءً على عدد النداءات الموسّعة وخيوط BOT_WORKERS."""
    cfg = get() if cfg is None else cfg
    providers = cfg.get("providers") or {}
    calls_on_queue = 0
    for st in (cfg.get("stages") or []):
        if not st.get("enabled", True):
            continue
        for c in (st.get("calls") or []):
            p_name = c.get("provider", "")
            p_cfg = providers.get(p_name) or {}
            p_queue = p_cfg.get("queue") or (p_name if p_cfg.get("type") == "local_cli" else None)
            if p_queue == queue_name:
                calls_on_queue += 1
    workers = max(1, int(getattr(Config, "BOT_WORKERS", 2) or 2))
    computed = calls_on_queue * workers + workers
    return max(8, computed)  # T6: يُستبدل بـ computed فقط عند ربط المنسق
