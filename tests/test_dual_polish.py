# -*- coding: utf-8 -*-
"""
اختبارات ميزة التنظيم المتوازي والمقارنة المزدوجة (Dual Polish / Compare Mode).
تغطي:
1. إعدادات compare_mode (merged | split | off).
2. تكامل لوحة التحكم /set و/panel مع compare_mode.
3. المساعد النصي: polish_gemini وpolish_local.
4. خط الإنتاج (delivery.deliver):
   - الوضع المدمج (merged): رسالة جيميني أولاً ثم تعديل صامت لإضافة شلبي داخل كتلة <pre> مستقلة.
   - الوضع المنفصل (split): 3 رسائل (خام + جيميني + شلبي).
   - وضع الإيقاف (off): رسالة منظمة واحدة تقليدية.
   - أمان طول الرسالة (> 4000 حرف) والتحويل التلقائي لرسالة منفصلة.
5. دالة edit_telegram_message وصمودها أمام أخطاء التنسيق.
"""

from config import Config
import settings
import panel
import text_assist
import delivery
import telegram_bot


class FakeSender:
    def __init__(self):
        self.sent_texts = []
        self.edited_texts = []
        self.docs = []

    def send_text(self, text, reply_to, parse_mode=None):
        mid = len(self.sent_texts) + 1
        self.sent_texts.append((mid, text, reply_to, parse_mode))
        return mid

    def send_document(self, fn, data, mime, caption, reply_to):
        self.docs.append((fn, data, mime, caption, reply_to))
        return 999

    def edit_text(self, message_id, text, parse_mode="HTML"):
        self.edited_texts.append((message_id, text, parse_mode))
        return True

    def as_sender(self):
        return delivery.Sender(
            send_text=self.send_text,
            send_document=self.send_document,
            edit_text=self.edit_text,
        )


def test_settings_compare_mode():
    old = settings.compare_mode()
    try:
        assert settings.set_compare_mode("split") == "split"
        assert settings.compare_mode() == "split"
        assert settings.set_compare_mode("off") == "off"
        assert settings.compare_mode() == "off"
        assert settings.set_compare_mode("merged") == "merged"
        assert settings.compare_mode() == "merged"
        assert settings.set_compare_mode("invalid_mode") is None
        assert settings.compare_mode() == "merged"
    finally:
        settings.set_compare_mode(old)


def test_panel_compare_mode():
    old = settings.compare_mode()
    try:
        assert "compare_mode" in panel.SET_WHITELIST
        ok, val = panel.validate_setting("compare_mode", "merged")
        assert ok and val == "merged"
        ok, val = panel.validate_setting("compare_mode", "split")
        assert ok and val == "split"
        ok, val = panel.validate_setting("compare_mode", "off")
        assert ok and val == "off"
        ok, val = panel.validate_setting("compare_mode", "synthesizer")
        assert ok and val == "synthesizer"
        ok, err = panel.validate_setting("compare_mode", "unknown")
        assert not ok and "القيمة غير صالحة" in err

        old_val, new_val = panel.apply_setting("compare_mode", "split")
        assert new_val == "split" and Config.COMPARE_MODE == "split"
        panel.apply_setting("compare_mode", "synthesizer")
        assert panel.current_setting("compare_mode") == "synthesizer"

        text, kb = panel.setting_view("compare_mode")
        assert "وضع مقارنة المنظم" in text
        buttons = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert any("synthesizer" in b for b in buttons)
        assert any("merged" in b for b in buttons)
        assert any("split" in b for b in buttons)
        assert any("off" in b for b in buttons)
    finally:
        panel.apply_setting("compare_mode", old)


def test_text_assist_helpers(monkeypatch):
    import importlib
    real_ta = importlib.reload(text_assist)
    monkeypatch.setattr(real_ta, "generate", lambda s, u, timeout=None: ("جيميني منظم", None))
    gem_text, gem_err = real_ta.polish_gemini("نص تجريبي")
    assert gem_text == "جيميني منظم" and gem_err is None

    monkeypatch.setattr(real_ta, "_call_local_provider", lambda s, u: ("شلبي منظم", None))
    monkeypatch.setattr(real_ta.Config, "LOCAL_POLISH_CMD", "dummy_command")
    loc_text, loc_err = real_ta.polish_local("نص تجريبي")
    assert loc_text == "شلبي منظم" and loc_err is None


def test_deliver_merged_mode(monkeypatch):
    old = settings.compare_mode()
    settings.set_compare_mode("merged")
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    try:
        fs = FakeSender()
        res = delivery.deliver(
            raw_text="أنا سجلت فويس وبجرب التطبيق بعد الصلاة",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=10,
            polish_fn=lambda raw, role, gl, **kw: ("نسخة جيميني المنظمة", None),
            local_polish_fn=lambda raw, role, gl, **kw: ("نسخة شلبي المنظمة", None),
            archive_entry=False,
        )

        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        # الرسائل المرسلة: الخام (1) + المنظم الأولي (2)
        assert len(fs.sent_texts) == 2
        raw_msg = fs.sent_texts[0][1]
        init_msg = fs.sent_texts[1][1]

        assert "أنا سجلت فويس" in raw_msg
        assert "🤖 <b>[جيميني]</b>:" in init_msg
        assert "نسخة جيميني المنظمة</pre>" in init_msg
        assert "جاري تحضير شلبي محلياً" in init_msg

        # تم التعديل الصامت لإضافة شلبي
        assert len(fs.edited_texts) == 1
        edited_mid, edited_text, parse_mode = fs.edited_texts[0]
        assert edited_mid == res["polished_msg_id"]
        assert parse_mode == "HTML"
        assert "🤖 <b>[جيميني]</b>:" in edited_text
        assert "نسخة جيميني المنظمة</pre>" in edited_text
        assert "🦙 <b>[شلبي]</b>:" in edited_text
        assert "نسخة شلبي المنظمة</pre>" in edited_text
    finally:
        settings.set_compare_mode(old)


def test_deliver_split_mode(monkeypatch):
    old = settings.compare_mode()
    settings.set_compare_mode("split")
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    try:
        fs = FakeSender()
        res = delivery.deliver(
            raw_text="فويس تجربة الوضع المنفصل",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=20,
            polish_fn=lambda raw, role, gl, **kw: ("جيميني منفصل", None),
            local_polish_fn=lambda raw, role, gl, **kw: ("شلبي منفصل", None),
            archive_entry=False,
        )

        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        # 3 رسائل: الخام + جيميني + شلبي
        assert len(fs.sent_texts) == 3
        assert "فويس تجربة الوضع المنفصل" in fs.sent_texts[0][1]
        assert "🤖 <b>[جيميني]</b>" in fs.sent_texts[1][1]
        assert "جيميني منفصل</pre>" in fs.sent_texts[1][1]
        assert "🦙 <b>[شلبي]</b>" in fs.sent_texts[2][1]
        assert "شلبي منفصل</pre>" in fs.sent_texts[2][1]
    finally:
        settings.set_compare_mode(old)


def test_deliver_off_mode(monkeypatch):
    old = settings.compare_mode()
    settings.set_compare_mode("off")
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    try:
        fs = FakeSender()
        res = delivery.deliver(
            raw_text="فويس تجربة الوضع العادي",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=30,
            polish_fn=lambda raw, role, gl, **kw: ("منظم عادي", None),
            local_polish_fn=lambda raw, role, gl, **kw: ("شلبي لن يعمل", None),
            archive_entry=False,
        )

        assert res.get("compare_thread") is None
        # رسالتان فقط: خام ومنظم واحد تقليدي بدون وسوم مقارنة
        assert len(fs.sent_texts) == 2
        assert "🤖 <b>[جيميني]</b>" not in fs.sent_texts[1][1]
        assert "منظم عادي</pre>" in fs.sent_texts[1][1]
    finally:
        settings.set_compare_mode(old)


def test_deliver_merged_oversized_fallback(monkeypatch):
    old = settings.compare_mode()
    settings.set_compare_mode("merged")
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    try:
        fs = FakeSender()
        giant_shelby = "نص شلبي ضخم جداً " * 300  # يتجاوز 4000 حرف
        res = delivery.deliver(
            raw_text="فويس تجربة النص الطويل",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=40,
            polish_fn=lambda raw, role, gl, **kw: ("جيميني عادي", None),
            local_polish_fn=lambda raw, role, gl, **kw: (giant_shelby, None),
            archive_entry=False,
        )

        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        # لم يعدل الرسالة لتجنب تجاوز حد تليجرام، بل أرسل شلبي كرسالة/مستند مستقل
        assert len(fs.edited_texts) == 0
        # تم إرسال رسالة شلبي المستقلة
        assert len(fs.sent_texts) >= 3 or len(fs.docs) >= 1
    finally:
        settings.set_compare_mode(old)


def test_edit_telegram_message(monkeypatch):
    calls = []

    class MockResp:
        def __init__(self, code):
            self.status_code = code
            self.text = "mock"

    def mock_post(token, method, payload, timeout=None):
        calls.append((method, payload))
        if len(calls) == 1 and payload.get("parse_mode") == "HTML":
            return MockResp(400)  # أول محاولة تفشل بمشكلة HTML
        return MockResp(200)

    monkeypatch.setattr(telegram_bot, "_tg_post", mock_post)

    # التحقق من أن الدالة تعيد المحاولة تلقائياً بدون parse_mode لو رجع 400
    ok = telegram_bot.edit_telegram_message("token", 123, 456, "<b>نص غير مغلق", parse_mode="HTML")
    assert ok is True
    assert len(calls) == 2
    assert calls[0][1]["parse_mode"] == "HTML"
    assert "parse_mode" not in calls[1][1]


def test_compare_uses_gemini_not_local_for_main_message(monkeypatch):
    """الإنتاج الحقيقي: polish_fn غير محقون (None) + LOCAL_POLISH_CMD مضبوط → الرسالة الرئيسية تكون جيميني وليست شلبي."""
    import importlib
    real_ta = importlib.reload(text_assist)
    monkeypatch.setattr(real_ta, "_call_local_provider", lambda s, r: ("*** LOCAL/SHELBY OUTPUT ***", None))
    monkeypatch.setattr(real_ta, "generate", lambda s, u, timeout=None: ("*** PURE GEMINI OUTPUT ***", None))
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(real_ta.Config, "LOCAL_POLISH_CMD", "dummy_local")
    old = settings.compare_mode()
    settings.set_compare_mode("merged")
    try:
        fs = FakeSender()
        # استدعاء deliver بدون تمرير polish_fn (تماماً كما يناديه telegram_bot.py:480 في الإنتاج)
        res = delivery.deliver(
            raw_text="فويس اختبار مسار الإنتاج الحقيقي",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=50,
            archive_entry=False,
        )

        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        # الرسالة الأولى (جيميني): يجب أن تحتوي على ناتج جيميني فقط ولا تحتوي على شلبي
        init_msg = fs.sent_texts[1][1]
        assert "🤖 <b>[جيميني]</b>:" in init_msg
        assert "*** PURE GEMINI OUTPUT ***" in init_msg
        assert "*** LOCAL/SHELBY OUTPUT ***" not in init_msg

        # الرسالة بعد التعديل: تحتوي على الاثنين كل في كتلته الخاصة
        edited_msg = fs.edited_texts[0][1]
        assert "🤖 <b>[جيميني]</b>:" in edited_msg
        assert "*** PURE GEMINI OUTPUT ***" in edited_msg
        assert "🦙 <b>[شلبي]</b>:" in edited_msg
        assert "*** LOCAL/SHELBY OUTPUT ***" in edited_msg
    finally:
        settings.set_compare_mode(old)


def test_settings_and_panel_synthesizer_mode():
    old = settings.compare_mode()
    try:
        assert settings.set_compare_mode("synthesizer") == "synthesizer"
        assert settings.compare_mode() == "synthesizer"

        ok, val = panel.validate_setting("compare_mode", "synthesizer")
        assert ok and val == "synthesizer"

        old_cfg, new_cfg = panel.apply_setting("compare_mode", "synthesizer")
        assert new_cfg == "synthesizer" and Config.COMPARE_MODE == "synthesizer"
        assert panel.current_setting("compare_mode") == "synthesizer"

        text, kb = panel.setting_view("compare_mode")
        buttons = [b["text"] for row in kb["inline_keyboard"] for b in row]
        assert any("synthesizer" in b for b in buttons)
    finally:
        settings.set_compare_mode(old)


def test_text_assist_synthesize_success(monkeypatch):
    import importlib
    real_ta = importlib.reload(text_assist)
    monkeypatch.setattr(real_ta, "generate", lambda s, u, timeout=None: ("النسخة الذهبية المسبوكة", None))

    class MockGlossary:
        def apply(self, text, target):
            return text + " [مُعدل بالقاموس]", 1

    text, err = real_ta.synthesize(
        raw_text="نص خام صوتي",
        gemini_draft="مسودة جيميني",
        shelby_draft="مسودة شلبي",
        glossary=MockGlossary(),
    )
    assert err is None
    assert "النسخة الذهبية المسبوكة [مُعدل بالقاموس]" in text
    assert real_ta.stats().get("synth_ok", 0) >= 1


def test_deliver_synthesizer_happy_path(monkeypatch):
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    old_mode = settings.compare_mode()
    settings.set_compare_mode("synthesizer")
    try:
        import text_assist
        monkeypatch.setattr(text_assist, "synthesize", lambda raw, g, s, **kw: ("*** ناتج السبك الذهبي 100% ***", None))

        fs = FakeSender()
        res = delivery.deliver(
            raw_text="فويس فحص السبك الثلاثي",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=10,
            archive_entry=False,
            polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني الفورية", None),
            local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي المحلية", None),
        )
        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        # 1. الرسالة الأولى: تعرض مسودة جيميني مع شريط الانتظار
        assert len(fs.sent_texts) >= 2
        init_polished_msg = fs.sent_texts[1][1]
        assert "مسودة جيميني الفورية" in init_polished_msg
        assert "جاري توليد النسخة الذهبية" in init_polished_msg

        # 2. التعديل في مكانه: تم تعديل الرسالة بالنسخة الذهبية المسبوكة وتذييل التوقيت
        assert len(fs.edited_texts) == 1
        edited_msg = fs.edited_texts[0][1]
        assert "🏆 النسخة الذهبية المسبوكة" in edited_msg
        assert "*** ناتج السبك الذهبي 100% ***" in edited_msg
        assert "فلاش:" in edited_msg
        assert "شلبي:" in edited_msg
        assert "السبك:" in edited_msg
    finally:
        settings.set_compare_mode(old_mode)


def test_deliver_synthesizer_timeout_fallback(monkeypatch):
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    old_mode = settings.compare_mode()
    settings.set_compare_mode("synthesizer")
    try:
        fs = FakeSender()
        res = delivery.deliver(
            raw_text="فويس فحص مهلة شلبي",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=20,
            archive_entry=False,
            polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني المعتمدة", None),
            local_polish_fn=lambda r, ro, gl, **kw: (None, "تجاوز مهلة المزود المحلي (180.0ث)"),
        )
        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        assert len(fs.edited_texts) == 1
        edited_msg = fs.edited_texts[0][1]
        assert "مسودة جيميني المعتمدة" in edited_msg
        assert "تجاوز شلبي المهلة المحددة 180ث" in edited_msg
    finally:
        settings.set_compare_mode(old_mode)


def test_deliver_synthesizer_synthesis_failure_fallback(monkeypatch):
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    old_mode = settings.compare_mode()
    settings.set_compare_mode("synthesizer")
    try:
        import text_assist
        monkeypatch.setattr(text_assist, "synthesize", lambda raw, g, s, **kw: (None, "خطأ 503 في السبك"))

        fs = FakeSender()
        res = delivery.deliver(
            raw_text="فويس فحص فشل السبك والتراجع لـ merged",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=30,
            archive_entry=False,
            polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني المتاحة", None),
            local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي المتاحة", None),
        )
        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        # في حالة فشل السبّاك، نتراجع لعرض الاثنين بنمط merged لحماية الناتج
        assert len(fs.edited_texts) == 1
        edited_msg = fs.edited_texts[0][1]
        assert "مسودة جيميني المتاحة" in edited_msg
        assert "مسودة شلبي المتاحة" in edited_msg
    finally:
        settings.set_compare_mode(old_mode)


def test_deliver_synthesizer_oversized_fallback(monkeypatch):
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    old_mode = settings.compare_mode()
    settings.set_compare_mode("synthesizer")
    try:
        import text_assist
        huge_golden = "ذهبي " * 1200  # > 4000 حرف
        monkeypatch.setattr(text_assist, "synthesize", lambda raw, g, s, **kw: (huge_golden, None))

        fs = FakeSender()
        res = delivery.deliver(
            raw_text="فويس فحص الحجم الكبير للسبك",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=40,
            archive_entry=False,
            polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني", None),
            local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي", None),
        )
        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        # ناتج ضخم يتجاوز حد تليجرام -> لم يعدل بل أرسل كرسالة/مستند مستقل
        assert len(fs.edited_texts) == 0
        assert len(fs.sent_texts) >= 3 or len(fs.docs) >= 1
    finally:
        settings.set_compare_mode(old_mode)


def test_deliver_synthesizer_gemini_503_shelby_rescues(monkeypatch):
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    old_mode = settings.compare_mode()
    settings.set_compare_mode("synthesizer")
    try:
        fs = FakeSender()
        res = delivery.deliver(
            raw_text="فويس فحص إنقاذ شلبي عند سقوط جيميني 503",
            model="gemini-3.6-flash",
            key_name="TestKey",
            info={},
            sender=fs.as_sender(),
            reply_to_id=60,
            archive_entry=False,
            # محاكاة خطأ MODEL_DOWN 503 من جيميني
            polish_fn=lambda r, ro, gl, **kw: (None, "MODEL_DOWN (503)"),
            # شلبي ينجح محلياً
            local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي المنقذة للبشرية", None),
        )
        t = res.get("compare_thread")
        assert t is not None
        t.join(timeout=3)

        # 1. الرسالة الأولى كانت إشعار إنقاذ
        assert len(fs.sent_texts) >= 2
        rescue_init_msg = fs.sent_texts[1][1]
        assert "تعذر منظم جيميني" in rescue_init_msg
        assert "جاري تنظيم النص عبر شلبي" in rescue_init_msg

        # 2. تعديل الرسالة في مكانها بمسودة شلبي بعد نجاحه!
        assert len(fs.edited_texts) == 1
        edited_msg = fs.edited_texts[0][1]
        assert "مسودة شلبي المنقذة للبشرية" in edited_msg
        assert "🦙 <b>[شلبي]</b>" in edited_msg
    finally:
        settings.set_compare_mode(old_mode)


