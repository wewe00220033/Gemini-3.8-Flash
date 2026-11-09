# -*- coding: utf-8 -*-
"""
اختبارات P0 الحرجة لمحرك السبك الثلاثي (Synthesizer) — تغطي BUG-01 حتى BUG-06
دون أي تسميم عالمي للوحدات ودون المساس بملفات الجذر.
"""

import pytest

import config
import archive
import delivery
import jobs
import observability
import sessions
import settings
import text_assist

pytestmark = pytest.mark.usefixtures("fresh_env")


class _FakeSender:
    def __init__(self, edit_ok=True, with_doc=True, edit_raises=False):
        self.sent = []
        self.edits = []
        self.docs = []
        self.edit_ok = edit_ok
        self.with_doc = with_doc
        self.edit_raises = edit_raises

    def send_text(self, text, reply_to, parse_mode=None, reply_markup=None):
        mid = len(self.sent) + 1
        self.sent.append((mid, text, reply_to, parse_mode))
        return mid

    def edit_text(self, mid, text, parse_mode="HTML", reply_markup=None):
        self.edits.append((mid, text, parse_mode))
        if self.edit_raises:
            raise RuntimeError("Telegram edit failure")
        return True if self.edit_ok else False

    def send_document(self, fn, data, mime, caption, reply_to):
        self.docs.append((fn, data, mime, caption, reply_to))
        return 900 + len(self.docs)

    def as_sender(self):
        return delivery.Sender(
            send_text=self.send_text,
            send_document=self.send_document if self.with_doc else None,
            edit_text=self.edit_text,
        )


def _setup_synth(monkeypatch):
    settings.set_compare_mode("synthesizer")
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")


def _deliver_and_join(**kwargs):
    res = delivery.deliver(**kwargs)
    t = res.get("compare_thread")
    assert t is not None
    t.join(timeout=5)
    return res


@pytest.mark.parametrize("scenario", ["timeout", "golden", "fallback_compare", "rescue"])
@pytest.mark.parametrize("edit_mode", ["false", "raises"])
def test_p0_01_edit_failure_never_leaves_waiting_bar_or_drops_output(monkeypatch, scenario, edit_mode):
    """BUG-01: فشل edit_text (False أو Exception) في أي مسار خلفي يُسقط الانتظار ويُرسل النتيجة النهائية فعلاً."""
    _setup_synth(monkeypatch)
    fs = _FakeSender(edit_ok=(edit_mode != "false"), edit_raises=(edit_mode == "raises"))

    if scenario == "timeout":
        p_fn = lambda r, ro, gl, **kw: ("مسودة جيميني المعتمدة", None)
        l_fn = lambda r, ro, gl, **kw: (None, "تجاوز مهلة المزود المحلي (180.0ث)")
        expected = "تم اعتماد مسودة جيميني"
    elif scenario == "golden":
        p_fn = lambda r, ro, gl, **kw: ("مسودة جيميني", None)
        l_fn = lambda r, ro, gl, **kw: ("مسودة شلبي", None)
        monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: ("النسخة الذهبية النهائية", None))
        expected = "النسخة الذهبية النهائية"
    elif scenario == "fallback_compare":
        p_fn = lambda r, ro, gl, **kw: ("مسودة جيميني مقارنة", None)
        l_fn = lambda r, ro, gl, **kw: ("مسودة شلبي مقارنة", None)
        monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: (None, "503"))
        expected = "مسودة شلبي مقارنة"
    else:  # rescue
        p_fn = lambda r, ro, gl, **kw: (None, "MODEL_DOWN (503)")
        l_fn = lambda r, ro, gl, **kw: ("إنقاذ شلبي المحلي", None)
        expected = "إنقاذ شلبي المحلي"

    _deliver_and_join(
        raw_text="نص خام لاختبار فشل التعديل",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=10,
        archive_entry=False,
        polish_fn=p_fn,
        local_polish_fn=l_fn,
    )

    # رسالة الانتظار (index 1) فشلت في التعديل -> يجب وجود رسالة متابعة تحمل الناتج النهائي ولا تحمل "جاري توليد"
    assert len(fs.sent) >= 3
    followups = [txt for _mid, txt, _rt, _pm in fs.sent[2:]]
    assert any(expected in t for t in followups)
    assert all("جاري توليد" not in t for t in followups)


@pytest.mark.parametrize("char_len,expect_doc", [
    (3500, False),
    (3800, True),    # مع <pre> والهيدر يتجاوز 3950 حرفاً (فجوة BUG-02 السابقة)
    (4500, True),
    (13000, True),
])
def test_p0_01_size_thresholds_edit_or_close_and_send_document(monkeypatch, char_len, expect_doc):
    """BUG-02: <= 3950 يعدّل في مكانه؛ > 3950 (سواء 3800 أو 4500 أو 13000) يغلق رسالة الانتظار ويرسل .md."""
    _setup_synth(monkeypatch)
    golden = "ن" * char_len
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: (golden, None))

    fs = _FakeSender()
    _deliver_and_join(
        raw_text="نص خام لاختبار الحدود",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=10,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني القصيرة", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي القصيرة", None),
    )

    assert len(fs.edits) == 1
    edited_mid, edited_txt, _pm = fs.edits[0]
    assert edited_mid == fs.sent[1][0]
    assert "جاري توليد" not in edited_txt

    if not expect_doc:
        assert golden in edited_txt
        assert len(fs.docs) == 0
    else:
        # Contract 7: zero automatic .md; delivered as text chunks in chat
        assert len(fs.docs) == 0
        assert len(fs.sent) >= 2


@pytest.mark.parametrize("golden_len,expect_doc", [
    (300, False),
    (4500, True),
])
def test_p0_01_large_initial_draft_isolates_waiting_notice(monkeypatch, golden_len, expect_doc):
    """المسودة الأولية (> 3950 حرف) تُرسل نظيفة أولاً، ثم رسالة انتظار مستقلة تتعدّل أو تُغلق بملف."""
    _setup_synth(monkeypatch)
    large_gemini = "ج" * 4500
    golden = "ذ" * golden_len
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: (golden, None))

    fs = _FakeSender()
    _deliver_and_join(
        raw_text="نص خام طويل",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=10,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: (large_gemini, None),
        local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي", None),
    )

    # رسالة الخام + معاينة ملف مسودة جيميني + رسالة الانتظار المستقلة
    assert fs.docs[0][0] == "gemini_draft.md"
    assert fs.docs[0][1] == large_gemini.encode("utf-8")
    wait_entries = [m for m in fs.sent if "جاري توليد النسخة الذهبية" in m[1]]
    assert len(wait_entries) == 1
    wait_mid = wait_entries[0][0]

    # التعديل استهدف رسالة الانتظار المستقلة وليس مسودة جيميني
    assert len(fs.edits) == 1
    assert fs.edits[0][0] == wait_mid
    assert "جاري توليد" not in fs.edits[0][1]
    if expect_doc:
        assert not any(d[0] == "golden_synthesized.md" for d in fs.docs)
        assert len(fs.sent) >= 2
    else:
        assert golden in fs.edits[0][1]


def test_p0_02_golden_persisted_in_archive_session_jobs_and_trace(monkeypatch):
    """BUG-04: حفظ النسخة الذهبية في ALL.md وsession وjobs.ndjson مع بقاء state == 'sent' وعمل dedup و/trace."""
    _setup_synth(monkeypatch)
    sessions.start("جلسة الاختبار")
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: ("النص الذهبي النهائي المحفوظ", None))

    job_id = "job-synth-001"
    jobs.record(job_id, "received", file_id="F_123", file_unique_id="U_GOLDEN_1", chat_id="77", message_id=9)
    jobs.record(job_id, "downloaded", bytes=2048)
    jobs.record(job_id, "transcribed", model="gemini-3.6-flash", key="K1", elapsed=2.5, text="كلام خام مهم")

    fs = _FakeSender()
    res = _deliver_and_join(
        raw_text="كلام خام مهم",
        model="gemini-3.6-flash",
        key_name="K1",
        info={"elapsed": 2.5},
        sender=fs.as_sender(),
        reply_to_id=9,
        archive_entry=True,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني الأولية", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي", None),
        job_id=job_id,
    )

    # محاكاة خط Worker الذي يكمل jobs.record بعد deliver تحت res["_lock"]
    with res["_lock"]:
        jobs.record(
            job_id, "sent", model="gemini-3.6-flash", key="K1", elapsed=2.5,
            text=res["raw"][:2000], polished=bool(res["polished"]), mode=res["mode"],
            polished_text=(res["polished"] or "")[:2000],
        )

    assert archive.last()["polished"] == "[نسخة ذهبية مسبوكة]\nالنص الذهبي النهائي المحفوظ"
    sess_last = sessions.last_entries(1)[-1]
    assert sess_last["polished"] == "النص الذهبي النهائي المحفوظ"

    st = jobs.load_state()[job_id]
    assert st["state"] == "sent"
    assert st["polished_text"] == "النص الذهبي النهائي المحفوظ"
    assert st.get("synth_golden") is True

    # Idempotency + Resume safety
    assert jobs.pending_jobs() == []
    cached = jobs.find_recent_result("U_GOLDEN_1")
    assert cached is not None and cached["polished_text"] == "النص الذهبي النهائي المحفوظ"

    # /trace clarity
    tr = observability.trace_text(job_id)
    assert tr is not None and "🏆 سبك ذهبي" in tr


def test_p0_02_fallback_writes_gemini_draft_to_archive_and_session(monkeypatch):
    """عند فشل شلبي أو السبّاك، تُحفظ مسودة جيميني في الأرشيف والجلسة دون ضياع (الكتابة المؤجلة)."""
    _setup_synth(monkeypatch)
    sessions.start("جلسة البديل")

    fs = _FakeSender()
    _deliver_and_join(
        raw_text="نص خام عند فشل شلبي",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=11,
        archive_entry=True,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني الوحيدة المتاحة", None),
        local_polish_fn=lambda r, ro, gl, **kw: (None, "تجاوز مهلة المزود المحلي (180.0ث)"),
    )

    assert archive.last()["polished"] == "مسودة جيميني الوحيدة المتاحة"
    assert sessions.last_entries(1)[-1]["polished"] == "مسودة جيميني الوحيدة المتاحة"


def test_p0_03_is_shelby_noop_unit_checks():
    """BUG-05: الفحص التوكيني يكشف إعادة النص كما هو دون إنذارات كاذبة عند وجود تنظيم حقيقي."""
    assert text_assist.is_shelby_noop("أهلا يا فندم   إزيك", "  أهلا يا فندم\nإزيك  ") is True
    assert text_assist.is_shelby_noop("أهلا يا فندم", "أهلاً يا فندم.") is False
    assert text_assist.is_shelby_noop("", "") is False
    assert text_assist.is_shelby_noop("نص", None) is False


def test_p0_03_shelby_noop_skips_third_llm_and_closes_cleanly(monkeypatch):
    """BUG-05: عند No-Op من شلبي يتم تخطي synthesize، زيادة local_noop، وإغلاق رسالة الانتظار بمسودة جيميني."""
    _setup_synth(monkeypatch)
    sessions.start("جلسة No-Op")
    synth_calls = []
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: synth_calls.append(1) or ("لن يُطلب", None))
    noop_before = text_assist.stats().get("local_noop", 0)

    fs = _FakeSender()
    res = _deliver_and_join(
        raw_text="يا جدعان احنا عايزين نخلص الشغل بدري",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=12,
        archive_entry=True,
        polish_fn=lambda r, ro, gl, **kw: ("نحن نريد إنهاء العمل مبكراً.", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("  يا جدعان   احنا عايزين\nنخلص الشغل بدري  ", None),
    )

    assert synth_calls == []
    assert text_assist.stats()["local_noop"] == noop_before + 1
    assert res["polished"] == "نحن نريد إنهاء العمل مبكراً."
    assert len(fs.edits) == 1
    assert "أعاد شلبي النص دون تنظيم فعلي" in fs.edits[0][1]
    assert "جاري توليد" not in fs.edits[0][1]
    assert archive.last()["polished"] == "نحن نريد إنهاء العمل مبكراً."


def test_p0_unexpected_exception_in_synthesizer_never_hangs_or_loses_archive(monkeypatch):
    """استثناء غير متوقع داخل خيط السبك يغلق رسالة الانتظار ويحفظ مسودة جيميني بدلاً من الصمت."""
    _setup_synth(monkeypatch)
    sessions.start("جلسة استثناء")

    def boom(*a, **kw):
        raise RuntimeError("boom in synthesize")

    monkeypatch.setattr(text_assist, "synthesize", boom)

    fs = _FakeSender()
    _deliver_and_join(
        raw_text="نص خام مع استثناء مفاجئ",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=14,
        archive_entry=True,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني المحمية", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي مختلفة", None),
    )

    assert len(fs.edits) == 1
    assert "RuntimeError" in fs.edits[0][1]
    assert "جاري توليد" not in fs.edits[0][1]
    assert archive.last()["polished"] == "مسودة جيميني المحمية"


def test_p0_merged_mode_edit_failure_falls_back_to_separate_message(monkeypatch):
    """في وضع merged، لو فشل edit_text لا يُبتلع شلبي بصمت بل يُرسل في رسالة مستقلة."""
    settings.set_compare_mode("merged")
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")

    fs = _FakeSender(edit_ok=False)
    _deliver_and_join(
        raw_text="نص خام للمدمج",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=15,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("نسخة شلبي المستقلة", None),
    )

    assert len(fs.sent) >= 3
    assert any("نسخة شلبي المستقلة" in m[1] for m in fs.sent[2:])


@pytest.mark.parametrize("bg_finishes_first", [True, False])
def test_p0_02_telegram_process_job_thread_safety(monkeypatch, bg_finishes_first):
    """اختبار تكامل حقيقي لمسار telegram_bot.process_job: لا سباق بين الخيط الرئيسي وخيط السبك."""
    import contextlib
    import time
    import telegram_bot
    import voice_queue

    _setup_synth(monkeypatch)
    fs = _FakeSender()
    captured_res = []
    orig_deliver = delivery.deliver

    def _wrapped_deliver(*args, **kwargs):
        res = orig_deliver(*args, **kwargs)
        captured_res.append(res)
        if bg_finishes_first and res.get("compare_thread"):
            res["compare_thread"].join(timeout=5)
        return res

    def _slow_or_fast_local(raw, role, gl, **kw):
        if not bg_finishes_first:
            time.sleep(0.08)
        return ("مسودة شلبي التكاملية", None)

    monkeypatch.setattr(delivery, "deliver", _wrapped_deliver)
    monkeypatch.setattr(telegram_bot, "ProgressKeeper", lambda *a, **kw: contextlib.nullcontext())
    monkeypatch.setattr(telegram_bot, "_runtime_token", lambda: "TEST_TOKEN")
    monkeypatch.setattr(telegram_bot, "make_sender", lambda *a, **kw: fs.as_sender())
    monkeypatch.setattr(telegram_bot, "_download_with_retry", lambda *a, **kw: b"ogg-bytes")
    monkeypatch.setattr(telegram_bot, "transcribe_audio_bytes_ex",
                        lambda *a, **kw: ("نص فويس متكامل", "gemini-3.6-flash", "K1", {"elapsed": 1.0}))
    monkeypatch.setattr(telegram_bot, "send_telegram_message", lambda *a, **kw: None)
    monkeypatch.setattr(text_assist, "polish_gemini", lambda raw, role=None, gl=None, **kw: ("مسودة فلاش التكاملية", None))
    monkeypatch.setattr(text_assist, "polish_local", _slow_or_fast_local)
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: ("النسخة الذهبية المتكاملة", None))

    jid = f"job-int-{int(bg_finishes_first)}"
    fuid = f"U_INT_{int(bg_finishes_first)}"
    jobs.record(jid, "received", file_id="F_INT", file_unique_id=fuid, chat_id="100", message_id=22)

    job = voice_queue.Job(
        source="telegram", file_id="F_INT", mime_type="audio/ogg",
        chat_id="100", message_id=22, file_unique_id=fuid, job_id=jid,
    )
    telegram_bot.process_job(job)

    if captured_res and captured_res[0].get("compare_thread"):
        captured_res[0]["compare_thread"].join(timeout=5)

    st = jobs.load_state()[jid]
    assert st["state"] == "sent"
    assert st["polished_text"] == "النسخة الذهبية المتكاملة"
    assert st.get("synth_golden") is True
    assert jobs.pending_jobs() == []
    assert jobs.find_recent_result(fuid)["polished_text"] == "النسخة الذهبية المتكاملة"


class _StatefulChatSender:
    """محاكي شات فعلي يتتبع الرسائل بالمعرف (إنشاء / تعديل / حذف) مثل تليجرام تماماً."""

    def __init__(self, policy="all_ok", with_delete=True, with_doc=True):
        self.messages = {}   # mid -> {"text": str, "parse_mode": str|None, "reply_to": int|None}
        self.docs = []
        self.edits = []
        self.deletes = []
        self._next_id = 1
        self.policy = policy
        self.with_delete = with_delete
        self.with_doc = with_doc

    def send_text(self, text, reply_to=None, parse_mode=None, reply_markup=None):
        mid = self._next_id
        self._next_id += 1
        self.messages[mid] = {"text": text, "parse_mode": parse_mode, "reply_to": reply_to}
        return mid

    def edit_text(self, mid, text, parse_mode="HTML", reply_markup=None):
        self.edits.append((mid, text, parse_mode))
        if self.policy == "html_only":
            if parse_mode == "HTML":
                return False
        elif self.policy == "full_only":
            if len(text) > 120:
                return False
        elif self.policy == "html_notice_rejected":
            if parse_mode == "HTML":
                return False
        elif self.policy == "all_edits_fail":
            return False
        elif self.policy == "all_edits_raise":
            raise RuntimeError("Telegram edit failure")

        if mid in self.messages:
            self.messages[mid]["text"] = text
            self.messages[mid]["parse_mode"] = parse_mode
        return True

    def delete_text(self, mid):
        self.deletes.append(mid)
        self.messages.pop(mid, None)
        return True

    def send_document(self, fn, data, mime, caption, reply_to):
        self.docs.append((fn, data, mime, caption, reply_to))
        return 900 + len(self.docs)

    def as_sender(self):
        return delivery.Sender(
            send_text=self.send_text,
            send_document=self.send_document if self.with_doc else None,
            edit_text=self.edit_text,
            delete_text=self.delete_text if self.with_delete else None,
        )


@pytest.mark.parametrize("is_oversized", [False, True])
@pytest.mark.parametrize("policy", [
    "html_only",
    "full_only",
    "html_notice_rejected",
    "all_edits_fail",
    "all_edits_raise",
])
def test_t06_failed_edit_must_close_original_message(monkeypatch, is_oversized, policy):
    """T-06: أي فشل لـ edit_text (small أو oversized) لا يترك رسالة ⏳ معلقة أبداً، ويوصل الذهبية كاملة."""
    _setup_synth(monkeypatch)
    golden = ("ذهبية-T06 " * 550) if is_oversized else "ذهبية-T06 قصيرة واضحة"
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: (golden, None))

    chat = _StatefulChatSender(policy=policy, with_delete=True, with_doc=True)
    _deliver_and_join(
        raw_text="نص خام لـ T-06",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=chat.as_sender(),
        reply_to_id=10,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة جيميني", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("مسودة شلبي", None),
    )

    remaining_texts = [m["text"] for m in chat.messages.values()]
    hanging = [t for t in remaining_texts if "⏳" in t or "جاري توليد" in t]
    assert hanging == [], f"Found hanging waiting message(s): {hanging}"

    assert not any(d[0] == "golden_synthesized.md" for d in chat.docs)
    assert any("ذهبية-T06" in t for t in remaining_texts)

    if not is_oversized and policy == "html_only":
        # تراجع من HTML إلى Plain في نفس الرسالة بدون رسالة إضافية
        assert len(chat.messages) == 2
    if policy in ("all_edits_fail", "all_edits_raise"):
        assert len(chat.deletes) == 1


def test_t06_rescue_failed_edit_closes_waiting_message(monkeypatch):
    """T-06 على مسار _bg_shelby_rescue: لو فشل edit_text لا تبقى رسالة 'جاري تنظيم النص عبر شلبي' معلقة."""
    _setup_synth(monkeypatch)
    chat = _StatefulChatSender(policy="all_edits_fail", with_delete=True, with_doc=True)
    _deliver_and_join(
        raw_text="نص خام للإنقاذ",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=chat.as_sender(),
        reply_to_id=10,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: (None, "503 unavailable"),
        local_polish_fn=lambda r, ro, gl, **kw: ("نتيجة شلبي المنقذة", None),
    )

    remaining_texts = [m["text"] for m in chat.messages.values()]
    assert [t for t in remaining_texts if "⏳" in t] == []
    assert any("نتيجة شلبي المنقذة" in t for t in remaining_texts)
    assert len(chat.deletes) == 1


def test_edit_telegram_message_retries_on_429(monkeypatch):
    """telegram_bot.edit_telegram_message يعيد المحاولة مرة واحدة عند HTTP 429 وفق retry_after."""
    import time
    import telegram_bot

    calls = []
    slept = []

    class Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}
            self.headers = {}

        def json(self):
            return self._payload

    def fake_post(token, method, payload, timeout=None):
        calls.append(payload)
        if len(calls) == 1:
            return Resp(429, {"parameters": {"retry_after": 0.5}})
        return Resp(200, {"ok": True})

    monkeypatch.setattr(telegram_bot, "_tg_post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))

    ok = telegram_bot.edit_telegram_message("TOK", "1", 99, "<b>تم</b>", parse_mode="HTML")
    assert ok is True
    assert len(calls) == 2
    assert slept == [0.5]


def test_wait_background_threads_and_on_polished_callback(monkeypatch):
    """تفعيل on_polished callback وانتظار خيوط الخلفية عبر delivery.wait_background_threads()."""
    import time

    _setup_synth(monkeypatch)
    events = []

    def _slow_local(raw, role, gl, **kw):
        time.sleep(0.12)
        return ("شلبي البطيء", None)

    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: ("ذهبية مع callback", None))

    fs = _FakeSender()
    res = delivery.deliver(
        raw_text="خام مع callback",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=5,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة أولى", None),
        local_polish_fn=_slow_local,
        on_polished=lambda txt, **kw: events.append((txt, kw.get("is_golden"))),
    )
    assert len(delivery.active_background_threads()) >= 1
    rem = delivery.wait_background_threads(timeout=3.0)
    assert rem == 0
    assert delivery.active_background_threads() == []
    assert res["polished"] == "ذهبية مع callback"
    assert events == [("ذهبية مع callback", True)]


def test_main_shutdown_waits_for_active_delivery_thread(monkeypatch):
    """Runtime.shutdown() لا يقتل خيط السبك النشط بل ينتظره لحفظ الذهبية في ALL.md وjobs.ndjson."""
    import time
    import main

    _setup_synth(monkeypatch)
    sessions.start("جلسة إغلاق")

    def _slow_local(raw, role, gl, **kw):
        time.sleep(0.12)
        return ("شلبي قبل الإغلاق", None)

    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: ("ذهبية محفوظة قبل الإغلاق", None))
    monkeypatch.setattr(main.telegram_bot, "stop_queue", lambda timeout=1: None)
    monkeypatch.setattr(main.telegram_bot, "queue_in_flight", lambda: 0)

    jid = "job-shutdown-01"
    jobs.record(jid, "received", file_id="F_SD", file_unique_id="U_SD", chat_id="1", message_id=7)

    fs = _FakeSender()
    delivery.deliver(
        raw_text="نص خام قبل SIGTERM",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=7,
        archive_entry=True,
        polish_fn=lambda r, ro, gl, **kw: ("مسودة فلاش", None),
        local_polish_fn=_slow_local,
        job_id=jid,
    )

    rt = main.Runtime("TOK", "1")
    left = rt.shutdown(reason="SIGTERM")
    assert left == 0
    assert delivery.active_background_threads() == []
    assert archive.last()["polished"] == "[نسخة ذهبية مسبوكة]\nذهبية محفوظة قبل الإغلاق"
    assert jobs.load_state()[jid].get("synth_golden") is True


def test_telegram_dedup_path_thread_safety_with_job_id(monkeypatch):
    """مسار dedup في telegram_bot.process_job يمرر job_id ويعمل بأمان مع res['_lock']."""
    import time
    import telegram_bot
    import voice_queue

    _setup_synth(monkeypatch)
    fs = _FakeSender()
    orig_deliver = delivery.deliver
    seen_job_ids = []

    def _spy_deliver(*args, **kwargs):
        seen_job_ids.append(kwargs.get("job_id"))
        res = orig_deliver(*args, **kwargs)
        if res.get("compare_thread"):
            res["compare_thread"].join(timeout=5)
        return res

    jobs.record("job-orig-1", "sent", file_id="F_DUP", file_unique_id="U_DUP_1",
                text="نص مفرغ سابقاً", model="gemini-3.6-flash", key="K1", elapsed=1.0)

    monkeypatch.setattr(delivery, "deliver", _spy_deliver)
    monkeypatch.setattr(telegram_bot, "_runtime_token", lambda: "TEST_TOKEN")
    monkeypatch.setattr(telegram_bot, "make_sender", lambda *a, **kw: fs.as_sender())
    monkeypatch.setattr(text_assist, "polish_gemini", lambda raw, role=None, gl=None, **kw: ("مسودة معاد تنظيمها", None))
    monkeypatch.setattr(text_assist, "polish_local", lambda *a, **kw: (time.sleep(0.05) or "شلبي المكرر", None))
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: ("ذهبية المكرر", None))

    job = voice_queue.Job(
        source="telegram", file_id="F_DUP", mime_type="audio/ogg",
        chat_id="100", message_id=55, file_unique_id="U_DUP_1", job_id="job-dedup-2",
    )
    out = telegram_bot.process_job(job)
    assert out["dedup"] is True
    assert seen_job_ids == ["job-dedup-2"]
    st = jobs.load_state()["job-dedup-2"]
    assert st["state"] == "sent"
    assert st["dedup_of"] == "job-orig-1"
    assert st["polished_text"] == "ذهبية المكرر"
