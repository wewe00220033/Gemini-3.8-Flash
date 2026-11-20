# -*- coding: utf-8 -*-
"""
tests/test_p3_03_ki04_ki05.py — اختبارات حزمة P3-03 (KI-04 و KI-05).

- KI-04: تخصيص أيقونات المحركات عبر display.icons في pipeline_defaults و _format_icon_label و pipeline.example.json.
- KI-05: توحيد سقف الاقتطاع لسياق المالك في مصدره المعماري الصارم (memory.get_owner_context بالبايت)
         وتمرير max_owner_chars=None من delivery لمنع القص المزدوج أو إتلاف أحرف UTF-8.
"""

import json
import os

import delivery
import pipeline_defaults
import text_assist


def test_format_icon_label_custom_icons_ki_04():
    """KI-04 (P3-03): قراءة display.icons المخصصة أولاً قبل الأسماء الثابتة."""
    custom_icons = {"ollama_gpu": "🐋", "my_flash": "✨"}

    # 1) مزود مخصص بأيقونة محددة
    assert delivery._format_icon_label("ollama_gpu", "ollama_gpu", icons=custom_icons) == "🐋 ollama_gpu"
    assert delivery._format_icon_label("نموذج تجريبي", "my_flash", icons=custom_icons) == "✨ نموذج تجريبي"

    # 2) مزود جديد غير مدرج في icons يأخذ 🤖 افتراضياً
    assert delivery._format_icon_label("ollama_gpu", "ollama_gpu", icons={}) == "🤖 ollama_gpu"
    assert delivery._format_icon_label("unknown_prov", "unknown_prov") == "🤖 unknown_prov"

    # 3) المزودات القياسية تحتفظ بأيقوناتها كـ fallback عند غياب التخصيص
    assert delivery._format_icon_label("فلاش", "flash_lite", icons={}) == "⚡ فلاش"
    assert delivery._format_icon_label("شلبي", "shelby", icons={}) == "🦙 شلبي"
    assert delivery._format_icon_label("سبك", "synth", icons={}) == "🏆 سبك"

    # 4) الحفاظ على الأيقونة القائمة إذا كان الوسم يحتوي بالفعل على إيموجي
    assert delivery._format_icon_label("⚡ مسودة جاهزة", "flash_lite", icons=custom_icons) == "⚡ مسودة جاهزة"


def test_pipeline_defaults_has_empty_icons_dict_ki_04():
    """KI-04 (P3-03): DEFAULT_DISPLAY يحتوي على قاموس icons فارغ افتراضياً."""
    assert "icons" in pipeline_defaults.DEFAULT_DISPLAY
    assert pipeline_defaults.DEFAULT_DISPLAY["icons"] == {}


def test_pipeline_example_json_has_icons_example_ki_04():
    """KI-04 (P3-03): ملف القالب pipeline.example.json يعرض أمثلة لضبط icons."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    example_path = os.path.join(root, "pipeline.example.json")
    with open(example_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    disp = data.get("display") or {}
    assert "icons" in disp, "حقل icons مفقود في display داخل pipeline.example.json"
    assert disp["icons"].get("flash_lite") == "⚡"
    assert disp["icons"].get("shelby") == "🦙"
    assert disp["icons"].get("_example_groq") == "🤖"


def test_inject_system_context_max_owner_chars_none_preserves_full_context_ki_05():
    """KI-05 (P3-03): تمرير max_owner_chars=None يحقن كامل سياق المالك دون أي قص بالحروف."""
    base_sys = "برومبت النظام"
    long_owner = "أحمد مهندس برمجيات متخصص في بايثون وهندسة النظم " * 20
    res = text_assist.inject_system_context(
        base_sys,
        owner_context=long_owner,
        mode="polish",
        max_owner_chars=None,
    )
    assert long_owner.strip() in res
    assert "<owner_context>" in res


def test_delivery_pipeline_passes_max_owner_chars_none_behaviorally_ki_05(monkeypatch, tmp_path):
    """KI-05 (A4 — DEC-P3-01): اختبار سلوكي يحل محل الاختبار النصي السابق.
    يشغّل delivery._deliver_synthesizer بخط إنتاج وهمي (Stubs) مع memory.enabled=True وسياق مالك حقيقي
    على القرص، ويلتقط كل نداء فعلي لـ text_assist.inject_system_context ويتحقق أن:
      1) max_owner_chars الممرَّر هو None في كل مرحلة (لا قص مزدوج بالحروف في delivery)،
      2) سياق المالك وصل كاملاً كما قصّته memory.get_owner_context بالبايت (المصدر الوحيد للقص).
    """
    import threading
    import time
    from config import Config
    import memory
    import pipeline_config
    import pipeline_runner
    from pipeline_runner import CallResult, StageResult

    # 1) سياق مالك حقيقي على القرص (يُقرأ عبر memory.get_owner_context بالبايت)
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    owner_md = "# ثوابت المالك\nالاسم: زيزو — مهندس برمجيات متخصص في بايثون وهندسة النظم.\n" * 3
    (mem_dir / "MEMORY.md").write_text(owner_md, encoding="utf-8")
    monkeypatch.setattr(Config, "MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(Config, "MEMORY_FILE", str(mem_dir / "MEMORY.md"))
    monkeypatch.setattr(memory.Config, "MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(memory.Config, "MEMORY_FILE", str(mem_dir / "MEMORY.md"))
    memory.reset_state()

    # 2) خط إنتاج وهمي بمرحلتين مع تفعيل الذاكرة
    pipe_file = tmp_path / "pipeline.json"
    pipe_file.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setattr(Config, "PIPELINE_FILE", str(pipe_file))
    monkeypatch.setattr(pipeline_config.Config, "PIPELINE_FILE", str(pipe_file))
    pipeline_config.reset_cache()
    mem_cfg = {"enabled": True, "inject_max_lines": 200, "inject_max_bytes": 25600,
               "topics": ["coding", "family", "notes"], "context_per_topic_n": 3, "context_max_chars": 800}
    custom_cfg = {
        "display": {"max_parallel": 2, "live_cap_utf16": 3400, "style": "single_live"},
        "providers": {"prov_a": {}, "prov_b": {}},
        "stages": [
            {"name": "proposers", "enabled": True, "calls": [{"provider": "prov_a", "label": "فلاش"}]},
            {"name": "final", "enabled": True, "prompt": "synthesis", "inputs": ["raw", "proposers"],
             "calls": [{"provider": "prov_b", "label": "شلبي"}]},
        ],
        "guards": {"dedupe_ratio": 0.98, "forbid_new_speaker_tags": True},
        "speakers": {"enabled": False},
        "memory": mem_cfg,
    }
    monkeypatch.setattr(pipeline_config, "get", lambda: custom_cfg)

    def mock_run_stage(stage_name, stage_config, inputs, **kwargs):
        return StageResult(
            stage_name=stage_name, status="ok",
            calls=[CallResult(call_id="c", provider="prov_a", model="m", status="ok",
                              text="نص منظم", label="فلاش", elapsed_s=0.1)],
            final_output="نص منظم",
        )
    monkeypatch.setattr(pipeline_runner, "run_stage", mock_run_stage)

    # 3) التقاط كل نداء فعلي لـ inject_system_context مع تمرير النداء للأصل (لا تغيير في السلوك)
    captured = []
    real_inject = text_assist.inject_system_context

    def spy_inject(system_prompt, context_summary=None, owner_context=None, mode="polish", max_owner_chars=None):
        captured.append({"mode": mode, "owner_context": owner_context, "max_owner_chars": max_owner_chars})
        return real_inject(system_prompt, context_summary=context_summary, owner_context=owner_context,
                           mode=mode, max_owner_chars=max_owner_chars)
    monkeypatch.setattr(text_assist, "inject_system_context", spy_inject)

    class DummySender:
        def __init__(self):
            self._mid = 10
        def send_text(self, text, reply_to_id=None, parse_mode=None, **kw):
            self._mid += 1
            return {"message_id": self._mid}
        def edit_text(self, text, message_id, **kw):
            return {"message_id": message_id}

    th = delivery._deliver_synthesizer(
        raw="نص أصلي", polished="مسودة مسبقة", role_id="general", gl=None, l2=None,
        session_name="sess", sender=DummySender(), anchor=1, local_polish_fn=None,
        t0_flow=time.monotonic(), gemini_elapsed=0.5, res_lock=threading.Lock(),
        result={"title": "اختبار", "raw": "نص أصلي"},
        _bg_finalize=lambda app, arch, **kw: None,
    )
    th.join(timeout=5.0)

    # 4) التحقق السلوكي
    assert captured, "لم يُستدعَ inject_system_context إطلاقاً — المسار لم يعمل"
    assert all(c["max_owner_chars"] is None for c in captured), captured
    expected_owner = memory.get_owner_context(mem_cfg, force=True)
    assert expected_owner and "زيزو" in expected_owner
    assert all(c["owner_context"] == expected_owner for c in captured), "سياق المالك لم يصل كما قصّته memory بالبايت"
    assert {c["mode"] for c in captured} >= {"polish", "synthesis"}
