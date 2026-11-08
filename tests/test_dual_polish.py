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
    assert "compare_mode" in panel.SET_WHITELIST
    ok, val = panel.validate_setting("compare_mode", "merged")
    assert ok and val == "merged"
    ok, val = panel.validate_setting("compare_mode", "split")
    assert ok and val == "split"
    ok, val = panel.validate_setting("compare_mode", "off")
    assert ok and val == "off"
    ok, err = panel.validate_setting("compare_mode", "unknown")
    assert not ok and "القيمة غير صالحة" in err

    old, new = panel.apply_setting("compare_mode", "split")
    assert new == "split" and Config.COMPARE_MODE == "split"
    panel.apply_setting("compare_mode", "merged")
    assert panel.current_setting("compare_mode") == "merged"

    text, kb = panel.setting_view("compare_mode")
    assert "وضع مقارنة المنظم" in text
    buttons = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert any("merged" in b for b in buttons)
    assert any("split" in b for b in buttons)
    assert any("off" in b for b in buttons)


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
    settings.set_compare_mode("merged")
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")

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


def test_deliver_split_mode(monkeypatch):
    settings.set_compare_mode("split")
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")

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


def test_deliver_off_mode(monkeypatch):
    settings.set_compare_mode("off")
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")

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


def test_deliver_merged_oversized_fallback(monkeypatch):
    settings.set_compare_mode("merged")
    monkeypatch.setattr(Config, "LOCAL_POLISH_CMD", "dummy_local")

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
    settings.set_compare_mode("merged")

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
