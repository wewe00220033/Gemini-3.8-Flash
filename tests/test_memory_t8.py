# -*- coding: utf-8 -*-
"""
اختبارات محرك الذاكرة السلبي وعزل المواضيع والتقرير الأسبوعي (T8 — DEC-P2-03/04).
"""
import os
import datetime
import pytest
import memory
import prompts
import guards
from config import Config


@pytest.fixture(autouse=True)
def _reset_memory_state():
    memory.reset_state()
    yield
    memory.reset_state()


def test_owner_context_disabled_by_default(tmp_path, monkeypatch):
    """التحقق من أن سياق المالك يرجع None افتراضياً إذا كانت الذاكرة معطلة."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text("# ثوابت المالك\nالاسم: زيزو", encoding="utf-8")
    monkeypatch.setattr(memory.Config, "MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(Config, "MEMORY_DIR", str(mem_dir))

    # الذاكرة معطلة (افتراضي)
    assert memory.get_owner_context({"enabled": False}) is None
    assert memory.get_owner_context({}) is None


def test_owner_context_truncation_lines_and_bytes(tmp_path, monkeypatch):
    """التحقق من قص MEMORY.md بأول 200 سطر ثم 25600 بايت UTF-8."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    monkeypatch.setattr(memory.Config, "MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(Config, "MEMORY_DIR", str(mem_dir))

    # كتابة ملف به 300 سطر عربي
    lines = [f"سطر رقم {i}: معلومات شخصية عن المشروع والمطور زيزو" for i in range(1, 301)]
    (mem_dir / "MEMORY.md").write_text("\n".join(lines), encoding="utf-8")

    mem_cfg = {"enabled": True, "inject_max_lines": 50, "inject_max_bytes": 1000}
    ctx = memory.get_owner_context(mem_cfg, force=True)
    assert ctx is not None
    # التحقق من أن عدد الأسطر لا يتجاوز 50
    assert len(ctx.splitlines()) <= 50
    # التحقق من أن الحجم بالبايت لا يتجاوز 1000 بايت UTF-8
    assert len(ctx.encode("utf-8")) <= 1000


def test_extract_and_strip_topic_valid_and_fallback():
    """التحقق من استخراج وتجريد وسم #topic والارتداد الآمن إلى notes."""
    raw = "هذا نص منظم للتجربة.\n#topic: coding"
    clean, top = memory.extract_and_strip_topic(raw, ["coding", "family", "notes"])
    assert clean == "هذا نص منظم للتجربة."
    assert top == "coding"
    assert "#topic" not in clean

    # حالة عدم وجود وسم
    raw_no_topic = "نص بدون أي وسوم إطلاقاً"
    clean2, top2 = memory.extract_and_strip_topic(raw_no_topic)
    assert clean2 == raw_no_topic
    assert top2 == "notes"

    # حالة وسم غير معروف (خارج القائمة المسموحة) -> fallback إلى notes
    raw_unknown = "نص حول موضوع غريب\n#topic: astronomy"
    clean3, top3 = memory.extract_and_strip_topic(raw_unknown, ["coding", "family", "notes"])
    assert clean3 == "نص حول موضوع غريب"
    assert top3 == "notes"

    # فحص عدم الحساسية لحالة الأحرف (Case-Insensitive)
    raw_upper = "نص تقني متقدم\n#TOPIC: Coding"
    clean4, top4 = memory.extract_and_strip_topic(raw_upper, ["coding", "notes"])
    assert clean4 == "نص تقني متقدم"
    assert top4 == "coding"


def test_topic_strip_before_subset_guard_zero_warnings():
    """
    إثبات تصحيح 2: تجريد #topic قبل الحارس الحتمي يمنع الإنذارات الكاذبة
    لأن الكلمة اللاتينية (مثل coding) ليست في الخام.
    """
    raw_arabic = "تحدثنا اليوم عن تطوير الأنظمة والبرمجيات"
    golden_with_tag = "تحدثنا اليوم عن تطوير الأنظمة والبرمجيات باحترافية.\n#topic: coding"

    # لو فُحصت الذهبية مباشرة قبل التجريد، فالحارس سيرصد coding ككلمة لاتينية مستحدثة
    warns_before = guards.numbers_latin_subset_check(
        golden_text=golden_with_tag,
        raw_text=raw_arabic,
        drafts=[],
    )
    assert "coding" in warns_before

    # بعد التجريد بـ memory.extract_and_strip_topic:
    clean_golden, top = memory.extract_and_strip_topic(golden_with_tag)
    assert top == "coding"
    warns_after = guards.numbers_latin_subset_check(
        golden_text=clean_golden,
        raw_text=raw_arabic,
        drafts=[],
    )
    # الحارس يعيد قائمة فارغة تماماً (صفر إنذارات كاذبة)
    assert warns_after == []


def test_prompts_topic_instruction_only_when_memory_enabled():
    """التحقق من أن سطر تصنيف الموضوع لا يظهر إلا عند تفعيل الذاكرة صراحة، وحظره في السبك."""
    # 1. عند التعطيل (الافتراضي) -> صفر تغيير في البرومبت
    p_disabled = prompts.compose_prompt("polish", role_id="general", memory_enabled=False)
    assert "#topic:" not in p_disabled
    assert "تصنيف الموضوع" not in p_disabled

    # 2. عند التفعيل -> يظهر السطر والخيارات المسموحة
    p_enabled = prompts.compose_prompt("polish", role_id="general", memory_enabled=True, topics=["coding", "notes"])
    assert "تصنيف الموضوع:" in p_enabled
    assert "#topic: <topic>" in p_enabled
    assert "(coding|notes)" in p_enabled

    # 3. في السبك والمُنقّح المجمع -> حظر قاطع لخروج الوسم
    s_sys, _ = prompts.compose_synthesis_prompt(raw_text="خام", gemini_draft="مسودة 1", shelby_draft="مسودة 2")
    assert "تنبيه قاطع: لا تُخرج سطر #topic في ناتجك نهائياً." in s_sys

    m_sys, _ = prompts.compose_polish_multi_prompt(raw_text="خام", drafts=[("فلاش", "م1")])
    assert "تنبيه قاطع: لا تُخرج سطر #topic في ناتجك نهائياً." in m_sys


def test_append_daily_single_line_format(tmp_path, monkeypatch):
    """التحقق من كتابة سطر واحد فقط لكل فويس في daily/YYYY-MM-DD.md بتنسيق الفهرس النظيف."""
    mem_dir = tmp_path / "memory"
    monkeypatch.setattr(Config, "MEMORY_DIR", str(mem_dir))

    mem_cfg = {"enabled": True}
    preview_long = "هذا نص مسبوك طويل جداً ومفصل يتجاوز طوله ثمانين حرفاً بكثير للتحقق من قصه كمعاينة نظيفة دون تكرار النص الكامل"
    fixed_ts = datetime.datetime(2026, 11, 19, 14, 30).timestamp()

    memory.append_daily("voice_abc123", "coding", preview_long, timestamp=fixed_ts, memory_cfg=mem_cfg)

    daily_file = mem_dir / "daily" / "2026-11-19.md"
    assert daily_file.exists()
    content = daily_file.read_text(encoding="utf-8").strip()

    # سطر واحد بالضبط
    lines = content.splitlines()
    assert len(lines) == 1
    line = lines[0]

    # التنسيق: HH:MM · #topic · <entry_id> · <معاينة <= 80 حرف>
    parts = line.split("·")
    assert len(parts) == 4
    assert parts[0].strip() == "14:30"
    assert parts[1].strip() == "#coding"
    assert parts[2].strip() == "voice_abc123"
    assert len(parts[3].strip()) <= 80


def test_topic_context_rolling_fifo_compacted(tmp_path, monkeypatch):
    """التحقق من إدارة السياق المتحرك context/<topic>.md بنظام Rolling FIFO وقص الحجم."""
    mem_dir = tmp_path / "memory"
    monkeypatch.setattr(memory.Config, "MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(Config, "MEMORY_DIR", str(mem_dir))

    mem_cfg = {
        "enabled": True,
        "context_per_topic_n": 3,
        "context_max_chars": 200,
    }

    # إضافة 4 نصوص متعاقبة لموضوع coding
    memory.update_topic_context("coding", "المقطوعة الأولى سياق قديم جداً", memory_cfg=mem_cfg)
    memory.update_topic_context("coding", "المقطوعة الثانية كود بايثون نظيف", memory_cfg=mem_cfg)
    memory.update_topic_context("coding", "المقطوعة الثالثة خوارزميات وبحث سريع", memory_cfg=mem_cfg)
    memory.update_topic_context("coding", "المقطوعة الرابعة بنية معمارية سحابية", memory_cfg=mem_cfg)

    ctx = memory.get_topic_context("coding", memory_cfg=mem_cfg)
    assert ctx is not None
    # المقطوعة الأولى سقطت بـ FIFO
    assert "المقطوعة الأولى" not in ctx
    # المقطوعات الأخيرة حاضرة
    assert "المقطوعة الثانية" in ctx
    assert "المقطوعة الثالثة" in ctx
    assert "المقطوعة الرابعة" in ctx
    # الطول الكلي أقل من أو يساوي 200 حرف
    assert len(ctx) <= 200


def test_maybe_write_weekly_review(tmp_path, monkeypatch):
    """التحقق من توليد تقرير المراجعة الأسبوعي review/YYYY-WW.md سلبياً مع فلترة الأسبوع وتجميع تحذيرات الحارس."""
    mem_dir = tmp_path / "memory"
    monkeypatch.setattr(memory.Config, "MEMORY_DIR", str(mem_dir))
    monkeypatch.setattr(Config, "MEMORY_DIR", str(mem_dir))

    mem_cfg = {"enabled": True}
    fixed_now = datetime.datetime(2026, 11, 23, 10, 0)  # أسبوع W47
    # الأسبوع المنصرم هو W46 (من 2026-11-16 إلى 2026-11-22)
    ts_w46 = datetime.datetime(2026, 11, 18, 12, 0).timestamp()
    # ملف من أسبوع قديم سابق W45 (2026-11-09)
    ts_w45 = datetime.datetime(2026, 11, 9, 12, 0).timestamp()

    # تسجيل مدخلات في الأسبوع السابق W46
    memory.append_daily("v1", "coding", "كود 1", timestamp=ts_w46, memory_cfg=mem_cfg)
    memory.append_daily("v2", "coding", "كود 2", timestamp=ts_w46, memory_cfg=mem_cfg)
    memory.append_daily("v3", "family", "عائلة 1", timestamp=ts_w46, memory_cfg=mem_cfg)
    memory.append_guard_warning("Docker", timestamp=ts_w46, memory_cfg=mem_cfg)
    memory.append_guard_warning("Docker", timestamp=ts_w46, memory_cfg=mem_cfg)
    memory.append_guard_warning("FastAPI", timestamp=ts_w46, memory_cfg=mem_cfg)

    # تسجيل في أسبوع أقدم W45 للتأكد من فلترته وعدم احتسابه
    memory.append_daily("v_old", "coding", "كود قديم", timestamp=ts_w45, memory_cfg=mem_cfg)
    memory.append_guard_warning("OldWord", timestamp=ts_w45, memory_cfg=mem_cfg)

    # وهمي للقاموس بمدخلات معلقة
    class FakeGlossary:
        def entries(self):
            return [
                {"patterns": ["فلاش"], "replacement": "Gemini Flash", "status": "confirmed", "hits": 10},
                {"patterns": ["شلبي"], "replacement": "ChatGPT", "status": "pending", "hits": 5},
            ]

    rev_path = memory.maybe_write_weekly_review(
        glossary=FakeGlossary(),
        memory_cfg=mem_cfg,
        now=fixed_now,
    )

    assert rev_path is not None
    assert os.path.exists(rev_path)
    content = open(rev_path, "r", encoding="utf-8").read()

    # التحقق من المحتويات (الأسبوع W46 فقط)
    assert "# 📋 تقرير المراجعة الأسبوعي" in content
    assert "#coding`: 2 تسجيل" in content   # 2 من W46 فقط (وليس 3 مع W45)
    assert "#family`: 1 تسجيل" in content
    assert "شلبي` ➔ `ChatGPT` (تكرار: 5)" in content
    assert "Docker`: 2 مرات" in content
    assert "FastAPI`: 1 مرات" in content
    assert "OldWord" not in content         # مفلترة بنجاح

    # استدعاء ثانٍ في نفس الأسبوع يعيد None دون إعادة الكتابة (Idempotent)
    assert memory.maybe_write_weekly_review(glossary=FakeGlossary(), memory_cfg=mem_cfg, now=fixed_now) is None


def test_owner_context_from_custom_memory_file(tmp_path, monkeypatch):
    """التحقق من قراءة MEMORY_FILE المخصص عبر البيئة (نمط PIPELINE_FILE)."""
    custom_memory = tmp_path / "custom_memory.md"
    custom_memory.write_text("# ثوابت مخصصة\nالمالك: زيزو المعماري", encoding="utf-8")
    monkeypatch.setattr(memory.Config, "MEMORY_FILE", str(custom_memory))
    monkeypatch.setattr(Config, "MEMORY_FILE", str(custom_memory))

    mem_cfg = {"enabled": True}
    ctx = memory.get_owner_context(mem_cfg, force=True)
    assert ctx is not None
    assert "المالك: زيزو المعماري" in ctx

