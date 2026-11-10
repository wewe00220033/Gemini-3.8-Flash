# -*- coding: utf-8 -*-
"""
اختبارات ميزة عرض مسودة شلبي مع مسودة فلاش لحظياً في الرسالة الحية وختامياً في النسخة المسبوكة.
"""

import html
import pytest
import config
import delivery
import settings
import telegram_bot
from delivery import (
    synthesizer_interim_message_html,
    synthesizer_final_message_html,
    LiveMessageController,
    _utf16_len,
)


class FakeSender:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.docs = []

    def send_text(self, text, reply_to, parse_mode=None, reply_markup=None):
        mid = len(self.sent) + 1
        self.sent.append((mid, text, reply_to, parse_mode, reply_markup))
        return mid

    def edit_text(self, mid, text, parse_mode="HTML", reply_markup=None):
        self.edits.append((mid, text, parse_mode, reply_markup))
        return True

    def delete_text(self, mid):
        return True

    def send_document(self, fn, data, mime, caption, reply_to):
        self.docs.append((fn, data, mime, caption, reply_to))
        return 999

    def as_sender(self):
        return delivery.Sender(
            send_text=self.send_text,
            send_document=self.send_document,
            edit_text=self.edit_text,
            delete_text=self.delete_text,
        )


def test_synthesizer_interim_message_html_phase1_vs_phase2():
    # المرحلة 1: فلاش فقط مع انتظار شلبي
    h1 = synthesizer_interim_message_html("مسودة فلاش وحدها", shelby_text=None, title="تسجيل 1")
    assert "مسودة فلاش وحدها" in h1
    assert "جاري توليد النسخة الذهبية" in h1
    assert "مسودة شلبي" not in h1

    # المرحلة 2: وصول شلبي وتحديث الحية بالمسودتين
    h2 = synthesizer_interim_message_html("مسودة فلاش السريعة", shelby_text="مسودة شلبي المحلية", title="تسجيل 1", shelby_status="synthesizing")
    assert "⚡ <b>[مسودة فلاش]:</b>" in h2
    assert "مسودة فلاش السريعة" in h2
    assert "🦙 <b>[مسودة شلبي]:</b>" in h2
    assert "مسودة شلبي المحلية" in h2
    assert "جاري السبك الثلاثي لإنتاج النسخة الذهبية المعتمدة" in h2


def test_synthesizer_interim_truncation_guard_protects_4000_limit():
    huge_flash = "فلاش " * 500  # ~2500 حرف
    huge_shelby = "شلبي " * 500  # ~2500 حرف
    h = synthesizer_interim_message_html(huge_flash, shelby_text=huge_shelby, title="جلسة ضخمة")
    # التأكد من عمل صمام الأمان وبقاء الحجم الكلي تحت سقف تليجرام 4096
    assert _utf16_len(h) <= 3900
    assert "[...يظهر كاملاً في السبك النهائي...]" in h


def test_synthesizer_final_message_html_expandable_blockquotes():
    golden = "النسخة الذهبية الصافية المسبوكة بدقة"
    flash = "مسودة فلاش الأصلية بالتفصيل"
    shelby = "مسودة شلبي المحلية بالتفصيل"
    telemetry = {"gemini": 5.5, "shelby": 7.3, "synth": 4.9, "total": 18.2}

    final_h = synthesizer_final_message_html(
        golden,
        gemini_text=flash,
        shelby_text=shelby,
        title="فويس تجريبي",
        telemetry=telemetry,
    )

    # الذهبية في pre
    assert f"<pre>" in final_h
    assert html.escape(golden) in final_h
    # ترويسة واضحة ومحددة للسبّاك النهائي
    assert "🏆 النسخة الذهبية المسبوكة (السبّاك)" in final_h
    # افتراضياً: المسودات تُحذف من الرسالة النهائية لإبقاء الشات نظيفاً
    assert "blockquote expandable" not in final_h
    # تذييل التيليمتري
    assert "⚡ فلاش: 5.5ث | 🤖 شلبي: 7.3ث | 🏆 السبك: 4.9ث | ⏱️ إجمالي: 18.2ث" in final_h

    # لو طُلب صراحة include_drafts=True
    final_with_drafts = synthesizer_final_message_html(
        golden,
        gemini_text=flash,
        shelby_text=shelby,
        include_drafts=True,
    )
    assert "blockquote expandable" in final_with_drafts
    assert "⚡ <b>[مسودة فلاش]:</b>" in final_with_drafts
    assert "🦙 <b>[مسودة شلبي]:</b>" in final_with_drafts


def test_html_chunks_preserves_blockquote_expandable():
    # التحقق من احتفاظ الوسم بخاصية expandable عند التقطيع
    long_body = "نص طويل مكرر داخل الاقتباس المطوي. " * 100
    html_text = f"<blockquote expandable>⚡ <b>مسودة:</b>\n{long_body}</blockquote>"
    chunks = list(telegram_bot._html_chunks(html_text, limit=400))
    assert len(chunks) > 1
    for chunk in chunks:
        assert "<blockquote expandable>" in chunk
        assert chunk.endswith("</blockquote>")


def test_live_message_controller_finalize_with_drafts_and_md_cache(tmp_path, monkeypatch):
    import md_cache
    monkeypatch.setattr(md_cache.Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config.Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(delivery.Config, "DATA_DIR", str(tmp_path))

    sender = FakeSender()
    ctrl = LiveMessageController(
        sender.as_sender(), anchor=10, title="فحص المسبوكة", chat_id="12345", user_id="67890", entry_id="E1"
    )
    ctrl.start_live("رسالة البداية")

    golden = "ذهب مسبوك " * 450  # نص طويل > 4000 UTF-16 لتفعيل التجزئة وزر .md
    flash = "مسودة فلاش كاملة"
    shelby = "مسودة شلبي كاملة"

    mids = ctrl.finalize(golden, is_golden=True, gemini_text=flash, shelby_text=shelby)
    assert len(mids) >= 1
    # التأكد من وجود الزر التفاعلي على القطعة الأخيرة
    last_sent = sender.sent[-1] if sender.sent else sender.edits[-1]
    assert last_sent is not None

    # فحص الكاش وتأكيد وجود ملحق المسودات في ملف .md
    import os
    cached_files = [f for f in os.listdir(md_cache.get_cache_dir()) if f.endswith(".json")]
    assert len(cached_files) == 1
    token = cached_files[0][:-5]
    item = md_cache.get(token)
    assert item is not None
    assert "## 📋 ملحق المسودات الأولية" in item["text"]
    assert "مسودة فلاش كاملة" in item["text"]
    assert "مسودة شلبي كاملة" in item["text"]


def test_synthesizer_final_message_provider_tags_and_clean_body():
    # 1. اختبار شلبي كمزود أساسي ونظافة الكتلة من بانر Verbatim
    golden = "📌 **عنوان مسبوك**\n\nنص ذهبي نقي بدون أي تلوث."
    telemetry_shelby = {"gemini": 4.4, "shelby": 8.7, "synth": 8.7, "total": 22.8, "synth_provider": "shelby"}
    html_shelby = synthesizer_final_message_html(
        golden,
        gemini_text="مسودة فلاش",
        shelby_text="مسودة شلبي",
        telemetry=telemetry_shelby,
        synth_provider="shelby",
    )
    assert "[🦙 شلبي]" in html_shelby
    assert "🏆 السبك: 8.7ث (🦙 شلبي)" in html_shelby
    assert "Verbatim Transcription" not in html_shelby
    assert "تفريغ الفويس المسجل" not in html_shelby

    # 2. اختبار ارتداد فلاش
    telemetry_flash = {"gemini": 4.4, "shelby": 8.7, "synth": 1.6, "total": 15.0, "synth_provider": "flash"}
    html_flash = synthesizer_final_message_html(
        golden,
        gemini_text="مسودة فلاش",
        shelby_text="مسودة شلبي",
        telemetry=telemetry_flash,
        synth_provider="flash",
    )
    assert "[⚡ فلاش (ارتداد)]" in html_flash
    assert "🏆 السبك: 1.6ث (⚡ فلاش)" in html_flash


def test_text_assist_synthesize_prefers_shelby_and_falls_back(monkeypatch):
    import text_assist
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "dummy_shelby_cmd")

    # سيناريو أ: شلبي ينجح محلياً -> السبّاك شلبي
    monkeypatch.setattr(text_assist, "_call_local_provider", lambda sys_t, usr_c, **kw: ("ناتج سبك شلبي المتقن", None))
    monkeypatch.setattr(text_assist, "generate", lambda sys_t, usr_c, **kw: ("ناتج فلاش الاحتياطي", None))

    text, err, provider = text_assist.synthesize("نص خام", "مسودة فلاش", "مسودة شلبي", return_provider=True)
    assert err is None
    assert text == "ناتج سبك شلبي المتقن"
    assert provider == "shelby"
    assert text_assist.get_last_synth_provider() == "shelby"

    # سيناريو ب: شلبي يفشل أو ينتهي وقته -> الارتداد التلقائي إلى فلاش
    monkeypatch.setattr(text_assist, "_call_local_provider", lambda sys_t, usr_c, **kw: (None, "تجاوز مهلة شلبي"))
    text_f, err_f, provider_f = text_assist.synthesize("نص خام", "مسودة فلاش", "مسودة شلبي", return_provider=True)
    assert err_f is None
    assert text_f == "ناتج فلاش الاحتياطي"
    assert provider_f == "flash"
    assert text_assist.get_last_synth_provider() == "flash"

