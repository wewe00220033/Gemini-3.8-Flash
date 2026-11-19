# -*- coding: utf-8 -*-
"""
pipeline_defaults.py — الثوابت القياسية وبناء خط التنظيم المدمج المطابق للسلوك الحالي (T3a — DEC-P2-08).

مفصول عن `pipeline_config.py` للحفاظ على وضوح القراءة الكاملة لكل وحدة دون ضغط التعليقات.
"""

import copy

from config import Config
import settings

REQUIRED_PROVIDER_FIELDS = {
    "gemini_api": ("model",),
    "openai_compat": ("base_url", "model", "api_key"),
    "local_cli": ("command",),
    "interactions_api": (),
}

PROVIDER_TYPE_DEFAULTS = {
    "gemini_api": {
        "max_concurrent": 2,
        "timeout_s": 40.0,
        "temperature": 0.2,
    },
    "openai_compat": {
        "max_concurrent": 2,
        "timeout_s": 60.0,
        "connect_timeout_s": 10.0,
        "strip_think_tags": True,
        "extra_body": {},
    },
    "local_cli": {
        "queue": "gpu",
        "input_format": "stdin_delimited",
        "timeout_s": 180.0,
        "lock_wait_s": 45.0,
    },
    "interactions_api": {
        "max_concurrent": 1,
        "timeout_s": 180.0,
    },
}

DEFAULT_DISPLAY = {
    "mode": "single_live",
    "show_stages": ["final"],
    "show_md_button": True,
    "max_chunks_per_output": 8,
    "edit_interval_s": 1.5,
    "live_cap_utf16": 3400,
    "chunk_limit_utf16": 4000,
    "silent_followups": True,
    "write_drafts_md": True,
    "max_parallel": 4,
    "deadline_margin_s": 20.0,
}

DEFAULT_GUARDS = {
    "dedupe_ratio": 0.98,
    "noop_ratio": 0.98,
    "forbid_new_speaker_tags": True,
    "numbers_latin_subset_check": True,
}

DEFAULT_SPEAKERS = {
    "enabled": False,
    "tag_regex": "^🗣️(\\d+):",
    "strip_if_single": True,
}

DEFAULT_MEMORY = {
    "enabled": False,
    "inject_max_lines": 200,
    "inject_max_bytes": 25600,
    "topics": ["coding", "family", "friends", "education", "religion", "notes"],
    "context_per_topic_n": 3,
    "context_max_chars": 800,
    "echo_ratio_strike": 0.60,
}

DEFAULT_HOT_RELOAD = {
    "enabled": True,
    "poll_interval_s": 5.0,
}


def builtin_providers():
    local_cmd = str(getattr(Config, "LOCAL_POLISH_CMD", "") or "").strip()
    return {
        "flash_lite": {
            "type": "gemini_api",
            "model": str(getattr(Config, "TEXT_ASSIST_MODEL", "gemini-3.5-flash-lite")),
            "max_concurrent": 2,
            "timeout_s": float(getattr(Config, "TEXT_ASSIST_TIMEOUT", 40) or 40),
            "temperature": 0.2,
            "thinking_budget": int(getattr(Config, "TEXT_ASSIST_THINKING_BUDGET", 2048) or 2048),
            "_unresolved": [],
        },
        "shelby": {
            "type": "local_cli",
            "command": local_cmd,
            "queue": "gpu",
            "input_format": "stdin_delimited",
            "timeout_s": float(getattr(Config, "LOCAL_POLISH_TIMEOUT", 180.0) or 180.0),
            "lock_wait_s": float(getattr(Config, "LOCAL_LOCK_WAIT_SEC", 45.0) or 45.0),
            "_unresolved": [] if local_cmd else ["LOCAL_POLISH_CMD"],
        },
    }


def build_default_pipeline():
    """يبني خط التشغيل الافتراضي المدمج المطابق 100% لتفرع delivery.py:1331-1338 وtext_assist.py الحالي."""
    providers = builtin_providers()
    has_local = bool(providers["shelby"]["command"])
    cmp_mode = settings.compare_mode()
    display = copy.deepcopy(DEFAULT_DISPLAY)

    if not has_local:
        stages = [
            {"name": "proposers", "enabled": True, "prompt": "polish", "inputs": ["raw"], "min_success": 1,
             "calls": [{"provider": "flash_lite", "label": "فلاش", "repeat_index": 1, "repeat_total": 1}]},
            {"name": "polishers", "enabled": False, "prompt": "polish_multi", "inputs": ["raw", "proposers"], "min_success": 1, "calls": []},
            {"name": "final", "enabled": False, "prompt": "synthesis", "inputs": ["raw", "proposers", "polishers"], "min_success": 1, "calls": []},
        ]
    elif cmp_mode == "synthesizer":
        stages = [
            {"name": "proposers", "enabled": True, "prompt": "polish", "inputs": ["raw"], "min_success": 1,
             "calls": [
                 {"provider": "flash_lite", "label": "فلاش", "repeat_index": 1, "repeat_total": 1},
                 {"provider": "shelby", "label": "شلبي", "repeat_index": 1, "repeat_total": 1},
             ]},
            {"name": "polishers", "enabled": False, "prompt": "polish_multi", "inputs": ["raw", "proposers"], "min_success": 1, "calls": []},
            {"name": "final", "enabled": True, "prompt": "synthesis", "inputs": ["raw", "proposers", "polishers"],
             "min_success": 1, "fallback": "best_available",
             "calls": [
                 {"provider": "shelby", "label": "سبك شلبي", "priority": True, "repeat_index": 1, "repeat_total": 1},
                 {"provider": "flash_lite", "label": "سبك فلاش", "only_if_previous_failed": True, "repeat_index": 1, "repeat_total": 1},
             ]},
        ]
    elif cmp_mode == "off":
        stages = [
            {"name": "proposers", "enabled": True, "prompt": "polish", "inputs": ["raw"], "min_success": 1,
             "calls": [
                 {"provider": "shelby", "label": "شلبي", "repeat_index": 1, "repeat_total": 1},
                 {"provider": "flash_lite", "label": "فلاش", "only_if_previous_failed": True, "repeat_index": 1, "repeat_total": 1},
             ]},
            {"name": "polishers", "enabled": False, "prompt": "polish_multi", "inputs": ["raw", "proposers"], "min_success": 1, "calls": []},
            {"name": "final", "enabled": False, "prompt": "synthesis", "inputs": ["raw", "proposers", "polishers"], "min_success": 1, "calls": []},
        ]
    else:
        # merged | split: مسودتان متوازيتان بدون مرحلة سبك نهائي
        display["legacy_compare_mode"] = cmp_mode
        stages = [
            {"name": "proposers", "enabled": True, "prompt": "polish", "inputs": ["raw"], "min_success": 1,
             "calls": [
                 {"provider": "flash_lite", "label": "فلاش", "repeat_index": 1, "repeat_total": 1},
                 {"provider": "shelby", "label": "شلبي", "repeat_index": 1, "repeat_total": 1},
             ]},
            {"name": "polishers", "enabled": False, "prompt": "polish_multi", "inputs": ["raw", "proposers"], "min_success": 1, "calls": []},
            {"name": "final", "enabled": False, "prompt": "synthesis", "inputs": ["raw", "proposers", "polishers"], "min_success": 1, "calls": []},
        ]

    return {
        "version": 1,
        "display": display,
        "providers": providers,
        "stages": stages,
        "guards": copy.deepcopy(DEFAULT_GUARDS),
        "speakers": copy.deepcopy(DEFAULT_SPEAKERS),
        "memory": copy.deepcopy(DEFAULT_MEMORY),
        "hot_reload": copy.deepcopy(DEFAULT_HOT_RELOAD),
    }
