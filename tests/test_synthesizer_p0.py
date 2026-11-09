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

    def send_text(self, text, reply_to, parse_mode=None):
        mid = len(self.sent) + 1
        self.sent.append((mid, text, reply_to, parse_mode))
        return mid

    def edit_text(self, mid, text, parse_mode="HTML"):
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
        assert "اكتملت المعالجة" in edited_txt
        assert len(fs.docs) == 1
        assert fs.docs[0][0] == "golden_synthesized.md"
        assert fs.docs[0][1] == golden.encode("utf-8")


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
        assert any(d[0] == "golden_synthesized.md" and d[1] == golden.encode("utf-8") for d in fs.docs)
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
