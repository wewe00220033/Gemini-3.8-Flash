# -*- coding: utf-8 -*-
"""
المرحلة 3 — اختبارات P3-01:
التحقق من دعم متغير البيئة MODELS_ORDER لإعادة ترتيب أولوية الموديلات
من لوحة الاستضافة دون لمس الكود، لإنفاذ قرار المالك (PHASE2-CLOSE §ج-4).
"""
import importlib
import os
import pytest


def test_models_order_default_without_env_var(fresh_env, monkeypatch):
    """P3-02: test isolation — (أ) في حال عدم ضبط المتغير: الترتيب الافتراضي يبقى gemini-3.6-flash أولاً بنسبة 100%."""
    monkeypatch.delenv("MODELS_ORDER", raising=False)
    import config
    importlib.reload(config)
    assert config.Config.MODELS[0] == "gemini-3.6-flash"
    assert "gemini-3.5-transcribe" in config.Config.MODELS


def test_models_order_with_env_var_transcribe_first(fresh_env, monkeypatch):
    """P3-02: test isolation — (ب) عند ضبط MODELS_ORDER=gemini-3.5-transcribe,gemini-3.6-flash يصبح transcribe أولاً."""
    monkeypatch.setenv("MODELS_ORDER", "gemini-3.5-transcribe,gemini-3.6-flash")
    import config
    importlib.reload(config)
    assert config.Config.MODELS[0] == "gemini-3.5-transcribe"
    assert config.Config.MODELS[1] == "gemini-3.6-flash"


def test_models_order_ignores_unknown_model_names(fresh_env, monkeypatch):
    """P3-02: test isolation — (ج) الأسماء غير الموجودة في _ALL_MODELS تُتجاهل بصمت لحماية البوت من الأخطاء الإملائية."""
    monkeypatch.setenv("MODELS_ORDER", "xyz_fake_model,gemini-3.5-transcribe,unknown_abc")
    import config
    importlib.reload(config)
    assert config.Config.MODELS[0] == "gemini-3.5-transcribe"
    assert "xyz_fake_model" not in config.Config.MODELS
    assert "unknown_abc" not in config.Config.MODELS


def test_models_order_respects_disabled_models(fresh_env, monkeypatch):
    """P3-02: test isolation — (د) DISABLED_MODELS تُطبّق دائماً بعد الترتيب لاستبعاد الموديلات المعطلة كلياً."""
    monkeypatch.setenv("MODELS_ORDER", "gemini-3.8-flash,gemini-3.5-transcribe")
    import config
    importlib.reload(config)
    assert "gemini-3.8-flash" in config.Config.DISABLED_MODELS
    assert "gemini-3.8-flash" not in config.Config.MODELS
    assert config.Config.MODELS[0] == "gemini-3.5-transcribe"

