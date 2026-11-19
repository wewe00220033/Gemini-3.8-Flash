# -*- coding: utf-8 -*-
"""
اختبارات T4 (DEC-P2-04 / DEC-P2-08 / DEC-P2-09):
- كبح 429 عبر RateLimitError في LiveMessageController.
- التجزئة الديناميكية max_chunks_per_output (0 = بلا سقف).
- التحكم في زر .md التفاعلي عبر show_md_button.
- الحفظ التلقائي للمسودات في drafts.py حتى عند فشل السبك وتوليد UUID بديل عند غياب entry_id.
- شرط القبول الصارم: اقتصار استيراد pipeline_config على delivery.py فقط بين ملفات التشغيل في الجذر.
"""

from datetime import datetime, timezone
import os
import re

import config
from delivery import LiveMessageController, RateLimitError, Sender
import drafts
import telegram_bot

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


class FakeSender:
    def __init__(self, edit_raises=None, edit_return=True):
        self.sent = []
        self.edits = []
        self.edit_raises = edit_raises
        self.edit_return = edit_return
        self.deleted = []

    def send_text(self, text, reply_to=None, parse_mode=None, reply_markup=None, **kwargs):
        mid = len(self.sent) + 100
        self.sent.append({"mid": mid, "text": text, "reply_to": reply_to, "markup": reply_markup})
        return mid

    def edit_text(self, message_id, text, parse_mode="HTML", reply_markup=None, **kwargs):
        self.edits.append({"mid": message_id, "text": text, "markup": reply_markup, "kwargs": kwargs})
        if self.edit_raises:
            if isinstance(self.edit_raises, list):
                if self.edit_raises:
                    exc = self.edit_raises.pop(0)
                    if exc is not None:
                        raise exc
            else:
                raise self.edit_raises
        return self.edit_return

    def delete_text(self, message_id):
        self.deleted.append(message_id)
        return True

    def as_sender(self):
        return Sender(
            send_text=self.send_text,
            edit_text=self.edit_text,
            delete_text=self.delete_text,
        )


def test_rate_limit_error_suppresses_interim_edits(fresh_env):
    """التقاط RateLimitError في update_interim يضبط مهلة التراجع ويكبح التحديثات التالية بلا شبكة."""
    fs = FakeSender(edit_raises=RateLimitError(retry_after=4.0))
    ctrl = LiveMessageController(fs.as_sender(), anchor=50, min_edit_interval=0.1)
    ctrl.live_mid = 100

    # التحديث الأول يرفع RateLimitError => update_interim يرجع False ويضبط _rate_limited_until
    ok1 = ctrl.update_interim("<b>تحديث أول</b>")
    assert ok1 is False
    assert len(fs.edits) == 1
    assert ctrl._rate_limited_until > 0

    # استدعاء ثانٍ فوري قبل انقضاء الـ 4 ثوانٍ: يرجع False فوراً دون استدعاء edit_text إطلاقاً
    ok2 = ctrl.update_interim("<b>تحديث ثانٍ محجوب</b>")
    assert ok2 is False
    assert len(fs.edits) == 1  # لم يُرسل طلب جديد للشبكة


def test_telegram_bot_edit_message_raise_429_compat(fresh_env, monkeypatch):
    """دالة edit_telegram_message ترفع RateLimitError عند raise_429=True وتبتلعه عند False للتوافق."""
    class Resp429:
        status_code = 429
        def json(self):
            return {"parameters": {"retry_after": 2.5}}

    monkeypatch.setattr(telegram_bot, "_tg_post", lambda *a, **k: Resp429())

    # المسار الجديد لمسارات المرحلة 2: يرفع RateLimitError مع زمن retry_after
    try:
        telegram_bot.edit_telegram_message("tok", 123, 456, "نص", raise_429=True)
        assert False, "كان يجب رفع RateLimitError"
    except RateLimitError as e:
        assert e.retry_after == 2.5

    # المسار القديم للتوافق: لا يرفع استثناءً بل يرجع False
    ok_legacy = telegram_bot.edit_telegram_message("tok", 123, 456, "نص", retry_429=False, raise_429=False)
    assert ok_legacy is False


def test_display_controls_chunks_limit_unlimited_and_capped(fresh_env, monkeypatch):
    """إعداد max_chunks_per_output: 0 يرسل كل القطع بلا بتر، و8 يقصها مع التنبيه."""
    fs = FakeSender()
    ctrl = LiveMessageController(fs.as_sender(), anchor=50, entry_id="rec_chunks_1")
    ctrl.live_mid = 100

    # توليد نص ضخم ينتج 13 قطعة
    long_golden = "فقرة طويلة جداً للتجربة\n\n" * 2000

    # 1) الافتراضي المدمج (max_chunks = 8): يُقص إلى 8 قطع فقط
    mids_capped = ctrl.finalize(long_golden, is_golden=True)
    assert len(mids_capped) == 8
    # القطعة الأخيرة تحوي التنبيه
    last_sent_text = fs.sent[-1]["text"]
    assert "تم إرسال أول 8 أجزاء فقط" in last_sent_text

    # 2) تفعيل السلوك الجديد (max_chunks = 0): لا بتر وتُرسل كامل القطع
    import pipeline_config
    monkeypatch.setattr(pipeline_config, "get", lambda force=False: {"display": {"max_chunks_per_output": 0, "show_md_button": False}})
    fs2 = FakeSender()
    ctrl2 = LiveMessageController(fs2.as_sender(), anchor=50, entry_id="rec_chunks_2")
    ctrl2.live_mid = 200

    mids_unlimited = ctrl2.finalize(long_golden, is_golden=True)
    assert len(mids_unlimited) > 8
    assert not any("تم إرسال أول" in s["text"] for s in fs2.sent)


def test_show_md_button_toggle(fresh_env, monkeypatch):
    """show_md_button=False يلغي زر .md تماماً، وTrue يربطه على القطعة الأخيرة."""
    import pipeline_config
    long_text = "سياق ذهبي طويل يتجاوز الحد الأدنى للزر " * 300

    # 1) عندما يكون show_md_button: False
    monkeypatch.setattr(pipeline_config, "get", lambda force=False: {"display": {"show_md_button": False, "max_chunks_per_output": 8}})
    fs_no_btn = FakeSender()
    ctrl_no_btn = LiveMessageController(fs_no_btn.as_sender(), anchor=50, entry_id="no_btn_1")
    ctrl_no_btn.live_mid = 300
    ctrl_no_btn.finalize(long_text, is_golden=True)
    assert not any(s.get("markup") is not None for s in fs_no_btn.sent)

    # 2) عندما يكون show_md_button: True
    monkeypatch.setattr(pipeline_config, "get", lambda force=False: {"display": {"show_md_button": True, "max_chunks_per_output": 8}})
    fs_with_btn = FakeSender()
    ctrl_with_btn = LiveMessageController(fs_with_btn.as_sender(), anchor=50, entry_id="with_btn_1")
    ctrl_with_btn.live_mid = 400
    ctrl_with_btn.finalize(long_text, is_golden=True)
    # القطعة الأخيرة تحوي الزر
    last_msg = fs_with_btn.sent[-1]
    assert last_msg["markup"] is not None
    assert "md:ask:" in str(last_msg["markup"])


def test_drafts_auto_save_on_finalize_and_uuid_fallback(fresh_env):
    """حفظ المسودة التلقائي في DATA_DIR/drafts/YYYY-MM-DD/<entry_id>.md مع دعم غياب المعرف وفشل السبك."""
    fs = FakeSender()
    ctrl = LiveMessageController(fs.as_sender(), anchor=50, entry_id="entry_golden_42", title="فويس تجريبي")
    ctrl.live_mid = 500

    mids = ctrl.finalize(
        "نص نهائي معتمد ومسبوك بدقة",
        is_golden=True,
        gemini_text="مسودة فلاش جيميني",
        shelby_text="مسودة شلبي المحلي",
    )
    assert len(mids) >= 1

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    expected_path = os.path.join(config.Config.DATA_DIR, "drafts", today_str, "entry_golden_42.md")
    assert os.path.isfile(expected_path)
    content = open(expected_path, "r", encoding="utf-8").read()
    assert "# entry_golden_42" in content
    assert "## ✅ النهائي" in content
    assert "نص نهائي معتمد ومسبوك بدقة" in content
    assert "### ⚡ فلاش" in content
    assert "مسودة فلاش جيميني" in content
    assert "### ⚡ شلبي" in content
    assert "مسودة شلبي المحلي" in content

    # تجربة غياب entry_id وحالة فشل السبك (is_golden=False)
    p_fallback = drafts.write(entry_id="", final_text=None, drafts=[("ملاحظة", "فشل التوليد")])
    assert p_fallback and os.path.isfile(p_fallback)
    fb_content = open(p_fallback, "r", encoding="utf-8").read()
    assert "تعذر السبك أو فشلت المعالجة" in fb_content
    assert "### ⚡ ملاحظة" in fb_content


def test_pipeline_config_import_restricted_to_delivery_only(fresh_env):
    """شرط قبول T4 الصارم: لا استيراد لـ pipeline_config من أي ملف تشغيلي في الجذر سوى delivery.py و transcriber.py (DEC-P2-01 T9)."""
    import_re = re.compile(r"^\s*(?:import\s+pipeline_config|from\s+pipeline_config\s+import)", re.MULTILINE)
    for fn in os.listdir(ROOT):
        if fn.endswith(".py") and fn not in ("delivery.py", "pipeline_config.py", "transcriber.py"):
            src = open(os.path.join(ROOT, fn), encoding="utf-8").read()
            assert not import_re.search(src), f"{fn} يستورد pipeline_config قبل أوانه (المصرح له في T4 هو delivery.py فقط)"
