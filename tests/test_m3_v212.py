# -*- coding: utf-8 -*-
"""
حزمة اختبارات المرحلة الثالثة (M3) وفق خطة التنفيذ المعتمدة v2.12:
- منسق الرسالة الحية الفردية (LiveMessageController) وقفل التسلسل (Single-Writer).
- حماية ما بعد الاعتماد (Finalized Protection) والفاصل الزمني (Debouncing).
- موازنة وسوم HTML مع وسم <code> عبر القطع (_html_chunks).
- دقة حساب وحدات UTF-16 Code Units (_utf16_len <= 4000).
- سقف تجزئة المسبوك حتى 8 قطع (MAX_FINAL_CHUNKS = 8) مع تنبيه التجاوز.
- مرونة تواقيع Sender وقبول reply_markup مع التوافق العكسي لموكات الاختبارات القديمة.
- العقد 7: صفر ملفات .md تلقائية للنسخة الذهبية المسبوكة.
- حراسة انحدار أوضاع off / merged / split وعتبة الخام 12000 حرف.
"""

import pytest
import config
import delivery
from delivery import LiveMessageController, Sender, MAX_FINAL_CHUNKS, _utf16_len
import telegram_bot
import settings
import text_assist


pytestmark = pytest.mark.usefixtures("fresh_env")


class MockDeliverySender:
    def __init__(self, can_edit=True, can_delete=True):
        self.sent = []
        self.edits = []
        self.deletes = []
        self.docs = []
        self.can_edit = can_edit
        self.can_delete = can_delete
        self._next_id = 100

    def send_text(self, text, reply_to=None, parse_mode=None, reply_markup=None):
        mid = self._next_id
        self._next_id += 1
        self.sent.append({
            "mid": mid, "text": text, "reply_to": reply_to,
            "parse_mode": parse_mode, "reply_markup": reply_markup
        })
        return mid

    def edit_text(self, message_id, text, parse_mode="HTML", reply_markup=None):
        if not self.can_edit:
            return False
        self.edits.append({
            "mid": message_id, "text": text,
            "parse_mode": parse_mode, "reply_markup": reply_markup
        })
        return True

    def delete_text(self, message_id):
        if not self.can_delete:
            return False
        self.deletes.append(message_id)
        return True

    def send_document(self, fn, data, mime, caption, reply_to):
        self.docs.append({"filename": fn, "data": data, "caption": caption, "reply_to": reply_to})
        return 999

    def as_sender(self):
        return Sender(
            send_text=self.send_text,
            send_document=self.send_document,
            edit_text=self.edit_text if self.can_edit else None,
            delete_text=self.delete_text if self.can_delete else None,
        )


def test_live_message_controller_single_writer_and_finalized_protection():
    """الكاتب الواحد: منع التحديثات الوسيطة بعد finalized=True، وعدم تكرار فتح الحية."""
    mock = MockDeliverySender()
    sender = mock.as_sender()
    ctrl = LiveMessageController(sender, anchor=50, title="فحص الحية")

    # 1. فتح الحية لأول مرة
    mid1 = ctrl.start_live("<b>بداية</b>")
    assert mid1 == 100
    assert len(mock.sent) == 1

    # 2. نداء start_live مرة أخرى يرجع نفس المعرف ولا يرسل رسالة جديدة
    mid2 = ctrl.start_live("<b>بداية مكررة</b>")
    assert mid2 == 100
    assert len(mock.sent) == 1

    # 3. تحديث وسيط مسموح به
    ok_interim = ctrl.update_interim("<i>وسيط 1</i>", force=True)
    assert ok_interim is True
    assert len(mock.edits) == 1

    # 4. الاعتماد النهائي
    mids = ctrl.finalize("النص الذهبي النهائي", is_golden=True)
    assert len(mids) >= 1
    assert ctrl.finalized is True

    # 5. أي تحديث وسيط بعد finalize يُرفض فوراً لحماية الرسالة
    blocked = ctrl.update_interim("<i>وسيط متأخر مرفوض</i>", force=True)
    assert blocked is False
    assert len(mock.edits) == 2  # edit 1 للوسيط، وedit 2 للنهائي


def test_live_message_controller_debouncing():
    """فرض فاصل زمني (min_edit_interval) بين تعديلات الرسالة الحية لمنع خطأ 429."""
    mock = MockDeliverySender()
    sender = mock.as_sender()
    ctrl = LiveMessageController(sender, anchor=50, min_edit_interval=1.0)

    ctrl.start_live("<b>بداية</b>")

    # تحديث سريع بعد البداية مباشرة بدون force -> يُرفض
    first_quick = ctrl.update_interim("<i>سريع 1</i>", force=False)
    assert first_quick is False

    # مع force=True -> يُقبل فوراً
    forced = ctrl.update_interim("<i>إجباري</i>", force=True)
    assert forced is True


def test_html_chunks_code_tag_balanced():
    """إزالة 'code' من _VOID_TAGS تضمن توازن وفتح/إغلاق <pre><code> عبر قطع الرسائل."""
    assert "code" not in telegram_bot._VOID_TAGS
    assert "pre" not in telegram_bot._VOID_TAGS

    # بناء نص طويل محاط بـ <pre><code> يتجاوز حد 500 حرف للتجزئة
    code_body = "x = 10\n" * 150
    html_input = f"<pre><code>{code_body}</code></pre>"
    chunks = list(telegram_bot._html_chunks(html_input, limit=400))
    assert len(chunks) > 1

    for chunk in chunks:
        # التأكد من التوازن التام لوسوم pre و code داخل كل قطعة
        assert chunk.count("<pre>") == chunk.count("</pre>")
        assert chunk.count("<code>") == chunk.count("</code>")
        assert _utf16_len(chunk) <= 400


def test_utf16_len_exact_code_units():
    """حساب وحدات UTF-16 بدقة تليجرام مع الإيموجي ورموز الزوج البديل (Surrogate Pairs)."""
    # الحروف اللاتينية والعربية = 1 code unit لكل حرف
    assert _utf16_len("Hello") == 5
    assert _utf16_len("مرحبا") == 5

    # الإيموجي مثل 🚀 طوله في بايثون 1 لكن في UTF-16 = 2 code units
    rocket = "🚀"
    assert len(rocket) == 1
    assert _utf16_len(rocket) == 2

    # مزيج إيموجي وعربي
    mixed = "مرحبا 🚀 تليجرام"
    assert _utf16_len(mixed) == len("مرحبا  تليجرام") + 2


def test_max_final_chunks_cap_at_8_with_alert():
    """سقف 8 قطع كحد أقصى (MAX_FINAL_CHUNKS = 8) مع حجز تنبيه القطعة الثامنة."""
    mock = MockDeliverySender()
    sender = mock.as_sender()
    ctrl = LiveMessageController(sender, anchor=50)
    ctrl.start_live("<b>انتظار</b>")

    # ناتج ضخم جداً يولد أكثر من 8 قطع عند حد 4000
    huge_golden = ("فقرة ذهبية مسبوكة طويلة جداً لشرح مفاهيم المنظومة.\n" * 120) * 10
    assert _utf16_len(huge_golden) > 4000 * 8

    mids = ctrl.finalize(huge_golden, is_golden=True)
    assert len(mids) <= MAX_FINAL_CHUNKS
    assert len(mids) == 8

    # التحقق من أن القطعة الأخيرة تحتوي على تنبيه التجاوز
    last_sent = mock.sent[-1]
    assert "[...تم إرسال أول 8 أجزاء فقط لاستيفاء الحد الأقصى للشات. يمكنك طلب الملف كاملاً...]" in last_sent["text"]


def test_sender_signature_backward_compat_and_markup():
    """مرونة تواقيع Sender وقبول موكات قديمة ذات 3 أو 4 معاملات دون TypeError."""
    calls_3arg = []
    # موك قديم يقبل 3 معاملات فقط (text, reply_to, parse_mode)
    legacy_send = lambda t, r, p: calls_3arg.append((t, r, p)) or 111
    # موك قديم للتعديل يقبل 3 معاملات فقط (mid, text, parse_mode)
    legacy_edit = lambda m, t, p: calls_3arg.append((m, t, p)) or True

    s = Sender(send_text=legacy_send, edit_text=legacy_edit)

    # النداء مع reply_markup لا يرفع TypeError ويتراجع بسلام
    mid = s.send_text("نص", 10, "HTML", reply_markup={"inline_keyboard": []})
    assert mid == 111
    assert calls_3arg[0] == ("نص", 10, "HTML")

    ok = s.edit_text(111, "تعديل", "HTML", reply_markup={"inline_keyboard": []})
    assert ok is True
    assert calls_3arg[1] == (111, "تعديل", "HTML")


def test_contract_7_zero_automatic_md_for_golden_synthesis(monkeypatch):
    """العقد 7: صفر ملفات .md تلقائية لمخرجات السبّاك، والتسليم نصي بحت في الشات."""
    settings.set_compare_mode("synthesizer")
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")

    long_golden = ("سطر من النسخة الذهبية المسبوكة بدقة فائقة.\n" * 120)  # > 4000 حرف
    assert _utf16_len(long_golden) > 4000
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: (long_golden, None))

    mock = MockDeliverySender()
    res = delivery.deliver(
        raw_text="فويس فحص العقد 7 بدون ملفات تلقائية",
        model="gemini-3.5-flash-lite",
        key_name="Key1",
        info={},
        sender=mock.as_sender(),
        reply_to_id=90,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي", None),
    )
    t = res.get("compare_thread")
    assert t is not None
    t.join(timeout=4)

    # التأكيد القاطع: صفر ملفات .md تلقائية
    assert len(mock.docs) == 0
    # تم التقسيم النصي في الشات
    assert len(mock.edits) >= 1 or len(mock.sent) >= 2


def test_regression_guard_off_merged_split_modes(monkeypatch):
    """حراسة الانحدار: أوضاع off و merged و split تعمل بكامل وظائفها دون أي تأثر."""
    mock = MockDeliverySender()
    sender = mock.as_sender()

    # 1. وضع off
    settings.set_compare_mode("off")
    res_off = delivery.deliver(
        raw_text="فويس وضع off",
        model="gemini-3.5-flash-lite",
        key_name="Key1",
        info={},
        sender=sender,
        reply_to_id=1,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("منظم off فردي", None),
    )
    assert res_off.get("compare_thread") is None
    assert "منظم off فردي" in res_off["polished"]

    # 2. وضع merged
    settings.set_compare_mode("merged")
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    res_merged = delivery.deliver(
        raw_text="فويس وضع merged",
        model="gemini-3.5-flash-lite",
        key_name="Key1",
        info={},
        sender=sender,
        reply_to_id=2,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني merged", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي merged", None),
    )
    tm = res_merged.get("compare_thread")
    assert tm is not None
    tm.join(timeout=3)
    assert res_merged["polished"] == "مسودة جيميني merged"

    # 3. وضع split
    settings.set_compare_mode("split")
    res_split = delivery.deliver(
        raw_text="فويس وضع split",
        model="gemini-3.5-flash-lite",
        key_name="Key1",
        info={},
        sender=sender,
        reply_to_id=3,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني split", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي split", None),
    )
    ts = res_split.get("compare_thread")
    assert ts is not None
    ts.join(timeout=3)
    assert res_split["polished"] == "مسودة جيميني split"


def test_regression_guard_raw_12000_char_threshold(monkeypatch):
    """العقد 10: عتبة 12000 حرف للخام ترسل transcript.md كالمعتاد ولا ينطبق عليها منع .md المسبوك."""
    settings.set_compare_mode("off")
    mock = MockDeliverySender()
    sender = mock.as_sender()

    huge_raw = "كلام خام فائق الطول يتجاوز 12000 حرف لتأكيد إرسال ملف التفريغ الخام.\n" * 250
    assert len(huge_raw) > delivery.Config.POLISH_FILE_THRESHOLD

    res = delivery.deliver(
        raw_text=huge_raw,
        model="gemini-3.5-flash-lite",
        key_name="Key1",
        info={},
        sender=sender,
        reply_to_id=10,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("منظم قصير", None),
    )
    assert res is not None
    # الخام الطويل يرسل مستند transcript.md
    assert any(d["filename"] == "transcript.md" for d in mock.docs)
