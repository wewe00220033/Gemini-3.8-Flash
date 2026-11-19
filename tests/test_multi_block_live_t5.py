# -*- coding: utf-8 -*-
"""
اختبارات T5: العرض الحي المتعدد الكتل وكتم الإشعارات وإغلاق ثغرات T4 (DEC-P2-04/09).
- اختبار 1: التطابق الحرفي مع السلوك القديم لـ synthesizer_interim_message_html.
- اختبار 2: الميزانية الذكية والقص الآمن بـ _safe_cut (إيموجي، كيانات &amp;، إسقاط الكتل عند <200، صفر <i> داخل <pre>).
- اختبار 3: كتم الإشعارات (silent_followups في finalize، send_output_message، وصول disable_notification لـ FakeTelegram).
- اختبار 4: التحقق من تصليح ثغرات T4 (استخدام Config.DRAFTS_DIR، النوم خارج القفل، عدم النوم في _try_edit_text).
- اختبار 5: قيد الاستيراد المعماري الصارم (pipeline_config محصور في delivery.py فقط بين ملفات التشغيل في الجذر).
"""

import ast
import html
import os
import re
import time
import delivery
from delivery import (
    LiveMessageController,
    RateLimitError,
    render_live_html,
    send_output_message,
    synthesizer_interim_message_html,
    _utf16_len,
)
import telegram_bot
from tests.fake_telegram import FakeTelegram


# =====================================================================
# اختبار 1: التطابق الحرفي مع السلوك القديم (Parity Test)
# =====================================================================

def test_synthesizer_interim_parity_and_wrapping():
    """
    التحقق من أن synthesizer_interim_message_html كـ wrapper لـ render_live_html
    يرجع نفس النص بالحرف للسلوك القديم في كافة الحالات العادية دون أي انحراف (Parity).
    """
    role_id = "general"
    session_name = "جلسة_عمل"
    title = "تقرير تجربة"

    # الحالة 1: المرحلة 1 (فلاش فقط) مع كافة حالات انتظار شلبي
    for status in ("waiting", "timeout", "error", "noop"):
        res = synthesizer_interim_message_html(
            gemini_text="مسودة فلاش الأولى للتجربة",
            shelby_text=None,
            role_id=role_id,
            session_name=session_name,
            title=title,
            shelby_status=status,
            error_detail="timeout after 180s" if status == "error" else None,
        )
        assert f"🏷️ <b>{html.escape(title)}</b>" in res
        assert "<b>📝 النسخة المنظمة</b>" in res
        assert f"جلسة: «{session_name}»" in res
        assert "<pre>مسودة فلاش الأولى للتجربة</pre>" in res
        assert "<pre>" in res and "</pre>" in res
        # التأكد التام من عدم وجود <i> داخل <pre>
        assert not re.search(r"<pre>(?:(?!</pre>).)*<i>", res, re.DOTALL)

    # الحالة 2: المرحلة 2 (فلاش + شلبي معاً)
    res_both = synthesizer_interim_message_html(
        gemini_text="نص فلاش المنظم",
        shelby_text="نص شلبي المنظم",
        role_id=role_id,
        session_name=session_name,
        title=title,
    )
    assert f"🏷️ <b>{html.escape(title)}</b>" in res_both
    assert "<b>📝 المسودات الأولية (فلاش + شلبي)</b>" in res_both
    assert "⚡ <b>[مسودة فلاش]:</b>" in res_both
    assert "<pre>نص فلاش المنظم</pre>" in res_both
    assert "🦙 <b>[مسودة شلبي]:</b>" in res_both
    assert "<pre>نص شلبي المنظم</pre>" in res_both
    assert "⏳ <b>جاري السبك الثلاثي لإنتاج النسخة الذهبية المعتمدة...</b>" in res_both
    # التأكد التام من عدم وجود <i> داخل <pre>
    assert not re.search(r"<pre>(?:(?!</pre>).)*<i>", res_both, re.DOTALL)


# =====================================================================
# اختبار 2: الميزانية الذكية والقص بـ _safe_cut وحماية الكيانات
# =====================================================================

def test_render_live_html_budget_and_safe_cut():
    """
    التحقق من:
    (أ) عدم كسر الإيموجي أو الكتل المركبة عند حدود القص.
    (ب) عدم كسر الكيانات مثل &amp; عند حدود القص.
    (ج) التحول التلقائي لأول بلوك + آخر بلوك مع سقف < 200 عند 5 كتل.
    (د) عدم وجود وسم <i> داخل أي <pre> نهائياً.
    (هـ) الطول الإجمالي ≤ live_cap دائماً.
    """
    live_cap = 1200

    # (أ) إيموجي وتشكيل عربي قرب حد البلوك
    complex_text = "تجربة نص معقد 👨‍👩‍👧‍👦 مع تشكيل كَلِمَاتٌ عَرَبِيَّةٌ " * 40
    rendered = render_live_html(
        blocks=[("فلاش", complex_text), ("شلبي", complex_text)],
        live_cap=live_cap,
    )
    assert _utf16_len(rendered) <= live_cap
    assert "[...يظهر كاملاً في السبك النهائي...]" in rendered
    # فحص سلامة UTF-16 وعدم وجود رموز معطوبة
    rendered.encode("utf-16-le").decode("utf-16-le")

    # (ب) كيانات HTML مثل &amp; و &lt; قرب حد البلوك
    entities_text = "نص تجريبي يحوي رموز & و < و > مكررة " * 60
    rendered_entities = render_live_html(
        blocks=[("فلاش", entities_text), ("شلبي", entities_text)],
        live_cap=live_cap,
    )
    assert _utf16_len(rendered_entities) <= live_cap
    # التأكد من عدم وجود كيان مبتور مثل &am أو &l داخل النص المهروب
    clean_no_pre = re.sub(r"</?pre>", "", rendered_entities)
    # فحص الكيانات السليمة
    assert not re.search(r"&(?:amp|lt|gt|quot|apos)[^;]", clean_no_pre)
    assert not re.search(r"&[a-zA-Z0-9]{1,3}\n", clean_no_pre)

    # (ج) 5 بلوكات بميزانية ضيقة تؤدي إلى per_block_cap < 200:
    # يجب أن يعرض أول بلوك وآخر بلوك فقط مع سطر الملاحظة بينهما
    five_blocks = [
        ("اقتراح 1", "محتوى اقتراح 1 " * 30),
        ("اقتراح 2", "محتوى اقتراح 2 " * 30),
        ("اقتراح 3", "محتوى اقتراح 3 " * 30),
        ("اقتراح 4", "محتوى اقتراح 4 " * 30),
        ("سباك نهائي", "محتوى سباك نهائي " * 30),
    ]
    rendered_five = render_live_html(blocks=five_blocks, live_cap=1200)
    assert _utf16_len(rendered_five) <= 1200
    # البلوك الأول والأخير يجب أن يكونا موجودين
    assert "اقتراح 1" in rendered_five
    assert "سباك نهائي" in rendered_five
    # سطر الملاحظة لـ 3 مسودات بينهما
    assert "<i>...و 3 مسودات بينهما...</i>" in rendered_five
    # الكتل الوسيطة يجب ألا تكون معروضة ككتل <pre>
    assert "اقتراح 2" not in rendered_five
    assert "اقتراح 3" not in rendered_five
    assert "اقتراح 4" not in rendered_five

    # (د) فحص حاسم: صفر <i> داخل أي <pre> نهائياً في كافة النتائج
    for res_text in (rendered, rendered_entities, rendered_five):
        pres = re.findall(r"<pre>(.*?)</pre>", res_text, re.DOTALL)
        for p in pres:
            assert "<i>" not in p, f"وجد وسم <i> داخل <pre>: {p[:100]}"
            assert "</i>" not in p


# =====================================================================
# اختبار 3: كتم الإشعارات ووصول disable_notification
# =====================================================================

def test_silent_followups_and_per_output_mode(monkeypatch):
    """
    التحقق من:
    - send_output_message يرسل مع disable_notification=True إلى الفويس (anchor).
    - finalize يرسل القطع 1..N مع disable_notification=silent_followups.
    - FakeTelegram يسجل disable_notification=True في sent[].
    - edit_telegram_message يتجاهل disable_notification صراحة دون أخطاء.
    """
    fake = FakeTelegram()
    tb = telegram_bot
    fake.install(tb, monkeypatch)

    sender = tb.make_sender("fake_token", chat_id=123)

    # 1. اختبار send_output_message
    mid = send_output_message(sender, label="مسودة فلاش", text="نص المسودة السريعة", anchor=999, silent=True)
    assert mid == 1
    assert len(fake.sent) == 1
    call_payload = fake.sent[0]
    assert call_payload["reply_to_message_id"] == 999
    assert call_payload.get("disable_notification") is True
    assert "⚡ <b>[مسودة فلاش]:</b>" in call_payload["text"]

    # مسودة بدون كتم الإشعار
    mid_loud = send_output_message(sender, label="مخرج عادي", text="نص عادي", anchor=999, silent=False)
    assert mid_loud == 1
    assert len(fake.sent) == 2
    assert not fake.sent[1].get("disable_notification")

    # 2. اختبار finalize مع قطع متعددة (silent_followups)
    # نسجل طول fake.sent قبل finalize
    before_len = len(fake.sent)
    long_golden = "سطر طويل جداً للسبك النهائي المعتمد\n" * 300
    ctrl = LiveMessageController(sender, anchor=999)
    # لا توجد رسالة حية سابقة -> يتم الإرسال من الصفر
    sent_mids = ctrl.finalize(long_golden, is_golden=True)
    assert len(sent_mids) > 1

    # الرسائل الجديدة الناتجة عن finalize
    new_sent = fake.sent[before_len:]
    # يجب أن تكون الرسائل الأولى (chunk 0) بإشعار عادي
    # والرسائل الأخيرة التابعة للقطع المتتابعة (chunks 1..N) تحمل disable_notification=True
    assert not new_sent[0].get("disable_notification")
    followup_msgs = [p for p in new_sent if p.get("disable_notification")]
    assert len(followup_msgs) >= 1
    for p in followup_msgs:
        assert p.get("disable_notification") is True

    # 3. اختبار edit_telegram_message يتجاهل disable_notification
    ok = tb.edit_telegram_message(
        "fake_token",
        chat_id=123,
        message_id=555,
        text="تعديل اختباري",
        disable_notification=True,
    )
    assert ok is True


# =====================================================================
# اختبار 4: تصليح ثغرات T4 (DRAFTS_DIR والنوم خارج القفل)
# =====================================================================

def test_t4_fixes_lock_and_drafts_dir(tmp_path, monkeypatch):
    """
    التحقق من:
    - drafts.write و drafts.prune_old يستجيبان لـ Config.DRAFTS_DIR.
    - LiveMessageController.finalize ينتظر خارج القفل.
    - _try_edit_text لا ينام نهائياً عند RateLimitError تحت القفل.
    """
    import drafts
    import config

    custom_drafts = tmp_path / "custom_drafts_dir"
    monkeypatch.setattr(config.Config, "DRAFTS_DIR", str(custom_drafts))
    if hasattr(drafts, "Config"):
        monkeypatch.setattr(drafts.Config, "DRAFTS_DIR", str(custom_drafts))

    # اختبار drafts.write يحترم DRAFTS_DIR
    target = drafts.write(
        entry_id="test_entry_t5",
        final_text="النسخة الذهبية",
        drafts=[("فلاش", "مسودة فلاش")],
    )
    assert target is not None
    assert str(custom_drafts) in target
    assert os.path.exists(target)

    # اختبار drafts.prune_old يحترم DRAFTS_DIR
    pruned = drafts.prune_old(keep_days=1)
    assert pruned == 0

    # اختبار النوم خارج القفل في finalize:
    # إذا كان _rate_limited_until نشطاً، يجب أن ينام قبل أخذ _writer_lock
    lock_log = []

    class TrackingLock:
        def __init__(self, real_lock):
            self.real = real_lock
        def __enter__(self):
            lock_log.append("lock_acquired")
            return self.real.__enter__()
        def __exit__(self, *args):
            return self.real.__exit__(*args)
        def acquire(self, *a, **k):
            lock_log.append("lock_acquired")
            return self.real.acquire(*a, **k)
        def release(self, *a, **k):
            return self.real.release(*a, **k)

    sender = delivery.Sender(
        send_text=lambda *a, **kw: 101,
        edit_text=lambda *a, **kw: True,
    )
    ctrl = LiveMessageController(sender, anchor=999)
    ctrl.live_mid = 100
    ctrl._rate_limited_until = time.monotonic() + 0.2
    ctrl._writer_lock = TrackingLock(ctrl._writer_lock)

    orig_sleep = time.sleep

    def tracking_sleep(secs):
        lock_log.append(f"slept_{secs:.2f}")
        orig_sleep(0.001)

    monkeypatch.setattr(time, "sleep", tracking_sleep)

    ctrl.finalize("نص قصير")
    # يجب أن يظهر slept قبل lock_acquired
    assert any("slept" in x for x in lock_log)
    assert lock_log.index([x for x in lock_log if "slept" in x][0]) < lock_log.index("lock_acquired")

    # اختبار _try_edit_text لا ينام عند RateLimitError تحت القفل
    sleep_calls = []
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

    def edit_raising_429(*a, **kw):
        raise RateLimitError(retry_after=4.5)

    sender_429 = delivery.Sender(
        send_text=lambda *a, **kw: 202,
        edit_text=edit_raising_429,
    )
    ctrl2 = LiveMessageController(sender_429, anchor=999)
    ctrl2.live_mid = 200
    # finalize يجب ألا تنام نهائياً داخل _try_edit_text
    sent = ctrl2.finalize("نص قصير")
    assert sent == [202]  # لجأ للـ send_text فوراً
    assert len(sleep_calls) == 0  # صفر نوم تحت القفل
    assert ctrl2._rate_limited_until > time.monotonic() + 4.0


# =====================================================================
# اختبار 5: قيد الاستيراد المعماري الصارم لـ pipeline_config
# =====================================================================

def test_pipeline_config_import_restriction():
    """
    التحقق الصارم من أن pipeline_config لا يُستورد أثناء التشغيل إلا في delivery.py
    (من بين كافة ملفات Python التشغيلية في جذر المشروع - DEC-P2-04/08/09).
    """
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exempt_modules = {
        "delivery.py",       # مصرح له بالاستيراد (DEC-P2-04/08)
        "transcriber.py",    # مصرح له بالاستيراد لقراءة إعدادات المتكلمين (DEC-P2-01 T9)
        "pipeline_config.py",# هو الملف نفسه
        "pipeline_defaults.py",
        "conftest.py",
    }

    violating_files = []
    for fn in os.listdir(root_dir):
        if not fn.endswith(".py"):
            continue
        if fn in exempt_modules or fn.startswith("test_"):
            continue

        file_path = os.path.join(root_dir, fn)
        with open(file_path, "r", encoding="utf-8") as f:
            try:
                tree = ast.parse(f.read(), filename=fn)
            except Exception:
                continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pipeline_config":
                        violating_files.append(fn)
            elif isinstance(node, ast.ImportFrom):
                if node.module == "pipeline_config":
                    violating_files.append(fn)

    assert not violating_files, f"ملفات تشغيلية غير مصرح لها تستورد pipeline_config: {violating_files}"
