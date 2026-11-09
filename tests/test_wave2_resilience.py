# -*- coding: utf-8 -*-
"""
اختبارات تكامل وصمود Wave 2 (B-02 حتى B-07):
- B-02: وضع merged مع مسودة منفصلة / خطأ طويل / فشل edit_text وon_polished.
- B-03: فخ draft_separated عند draft_mid=None + تنسيق LOCAL_POLISH_TIMEOUT ديناميكياً.
- B-04: _try_edit(None -> False) + حذف معاينة _send_long عند فشل send_document وإرسال النص كاملاً.
- B-05: process_job + _make_delivery_tracker (سباق سريع/بطيء، dedup > 2000، rescue_pending).
- B-06: إنقاذ الخام بالكامل وتسجيل sent في polished_only عند فشل جيميني وشلبي معاً.
- B-07: addenda للأرشيف والجلسات تحت التزامن وتبديل الجلسة مع نصوص > 64KB.
"""

import importlib
import threading
import time

import pytest

import archive
import config
import delivery
import jobs
import observability
import sessions
import settings
import text_assist


pytestmark = pytest.mark.usefixtures("fresh_env")


class _ResilienceSender:
    def __init__(self, *, edit_return=True, with_doc=True, doc_result=True,
                 doc_raises=None, fail_send_at=None, has_delete=True):
        self.sent = []          # [(mid, text, reply_to, parse_mode)]
        self.edits = []         # [(mid, text, parse_mode)]
        self.docs = []          # [(fn, data, mime, caption, reply_to)]
        self.deleted = []       # [mid]
        self.edit_return = edit_return
        self.with_doc = with_doc
        self.doc_result = doc_result
        self.doc_raises = doc_raises
        self.fail_send_at = set(fail_send_at or ())
        self.has_delete = has_delete
        self._send_calls = 0

    def send_text(self, text, reply_to, parse_mode=None, reply_markup=None):
        self._send_calls += 1
        if self._send_calls in self.fail_send_at:
            return None
        mid = len(self.sent) + 101
        self.sent.append((mid, text, reply_to, parse_mode))
        return mid

    def edit_text(self, mid, text, parse_mode="HTML", reply_markup=None):
        self.edits.append((mid, text, parse_mode))
        if callable(self.edit_return):
            return self.edit_return(mid, text, parse_mode)
        return self.edit_return

    def send_document(self, fn, data, mime, caption, reply_to):
        self.docs.append((fn, data, mime, caption, reply_to))
        if self.doc_raises is not None:
            raise self.doc_raises
        return self.doc_result

    def delete_text(self, mid):
        self.deleted.append(mid)
        return True

    def as_sender(self):
        return delivery.Sender(
            send_text=self.send_text,
            send_document=self.send_document if self.with_doc else None,
            edit_text=self.edit_text,
            delete_text=self.delete_text if self.has_delete else None,
        )


def _setup_mode(monkeypatch, cmp_mode, timeout=180.0):
    settings.set_compare_mode(cmp_mode)
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_TIMEOUT", timeout)
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_TIMEOUT", timeout)


def test_b02_merged_draft_separated_and_edit_fail_closes_waiting_and_fires_on_polished(monkeypatch):
    _setup_mode(monkeypatch, "merged")
    huge_gemini = "ج" * 4200
    long_err = "خطأ_طويل_" * 500
    callbacks = []
    fs = _ResilienceSender(edit_return=False, with_doc=True, doc_result=900)

    res = delivery.deliver(
        raw_text="نص اختبار B-02",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=10,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: (huge_gemini, None),
        local_polish_fn=lambda r, ro, gl, **kw: (None, long_err),
        on_polished=lambda txt, **kw: callbacks.append((txt, kw)),
    )
    t = res.get("compare_thread")
    assert t is not None
    t.join(timeout=5)

    # جيميني الطويل أُرسل كملف نظيف + رسالة انتظار مستقلة تم إغلاقها/حذفها عند فشل edit_text
    assert any(d[0] == "gemini_polished.md" for d in fs.docs)
    waiting_mids = [mid for mid, txt, _rt, _pm in fs.sent if "⏳" in txt]
    assert len(waiting_mids) == 1
    assert waiting_mids[0] in fs.deleted
    assert any("تعذر تشغيل شلبي" in txt for _mid, txt, _rt, _pm in fs.sent)
    surviving = [txt for mid, txt, _rt, _pm in fs.sent if mid not in fs.deleted]
    assert all("⏳" not in txt for txt in surviving)
    assert len(callbacks) == 1 and callbacks[0][0] == huge_gemini


def test_b02_merged_oversized_shelby_and_edit_failure_uses_replace_or_send(monkeypatch):
    _setup_mode(monkeypatch, "merged")
    fs = _ResilienceSender(edit_return=False, with_doc=True, doc_result=901)
    callbacks = []

    res = delivery.deliver(
        raw_text="نص خام متوسط",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=15,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: ("منظم جيميني قصير", None),
        local_polish_fn=lambda r, ro, gl, **kw: ("شلبي_كبير_" * 500, None),
        on_polished=lambda txt, **kw: callbacks.append(txt),
    )
    t = res.get("compare_thread")
    assert t is not None
    t.join(timeout=5)

    waiting_mids = [mid for mid, txt, _rt, _pm in fs.sent if "⏳" in txt]
    assert waiting_mids and waiting_mids[0] in fs.deleted
    assert any(d[0] == "merged_polished.md" for d in fs.docs)
    assert callbacks == ["منظم جيميني قصير"]


def test_b03_draft_separated_with_draft_mid_none_sends_full_draft_and_dynamic_timeout(monkeypatch):
    _setup_mode(monkeypatch, "synthesizer", timeout=8.5)
    huge_gemini = "ج" * 4200
    # _send_long بدون doc سيستدعي send_text أول مرة (الـ draft) -> نُفشل النداء رقم 2 فقط (1=raw، 2=clean_draft، 3=waiting)
    fs = _ResilienceSender(with_doc=False, fail_send_at={2})

    res = delivery.deliver(
        raw_text="نص اختبار B-03",
        model="gemini-3.6-flash",
        key_name="K1",
        info={},
        sender=fs.as_sender(),
        reply_to_id=20,
        archive_entry=False,
        polish_fn=lambda r, ro, gl, **kw: (huge_gemini, None),
        local_polish_fn=lambda r, ro, gl, **kw: (None, "تجاوز مهلة المزود المحلي (8.5ث)"),
    )
    t = res.get("compare_thread")
    assert t is not None
    t.join(timeout=5)

    # رسالة الانتظار الأولى يجب أن تعرض 8.5ث لا 180ث
    waiting_msgs = [txt for _m, txt, _r, _p in fs.sent if "جاري توليد النسخة الذهبية" in txt]
    assert waiting_msgs and "8.5ث" in waiting_msgs[0] and "180ث" not in waiting_msgs[0]

    # بما أن draft_mid فشل، يجب عدم إرسال كذبة "أعلاه"، بل إغلاق الانتظار وإرسال كامل مسودة جيميني للمستخدم!
    assert len(fs.edits) == 1
    assert "أعلاه" not in fs.edits[0][1]
    assert "اكتملت المعالجة" in fs.edits[0][1]
    all_delivered = [txt for _m, txt, _r, _p in fs.sent] + [txt for _m, txt, _p in fs.edits]
    assert all("أعلاه" not in txt for txt in all_delivered)
    assert any(("ج" * 100) in txt and "8.5ث" in txt for _m, txt, _r, _p in fs.sent)


def test_b04_try_edit_none_is_false_and_send_long_doc_fail_deletes_preview_and_sends_full(monkeypatch):
    # 1) _try_edit مع edit_text ترجع None -> False
    ns = _ResilienceSender(edit_return=None)
    assert delivery._try_edit(ns.as_sender(), 5, "hi", "HTML", "test") is False

    # 2) _send_long: فشل send_document بـ HTTP 500 أو False يحذف المعاينة المبتورة ويرسل full html عبر send_text
    class _Http500:
        status_code = 500

    for bad_res, bad_exc in [(_Http500(), None), (False, None), (None, RuntimeError("upload boom"))]:
        fs = _ResilienceSender(with_doc=True, doc_result=bad_res, doc_raises=bad_exc)
        long_plain = "محتوى_مستند_كامل " * 30
        full_html = "<pre>" + long_plain + "</pre>"
        mid = delivery._send_long(
            fs.as_sender(),
            full_html,
            long_plain,
            reply_to_id=77,
            filename_prefix="golden",
            caption="CAP",
            force_document=True,
        )
        assert len(fs.sent) == 2
        preview_mid = fs.sent[0][0]
        assert preview_mid in fs.deleted
        assert fs.sent[1][1] == full_html
        assert mid == fs.sent[1][0]


def _setup_bot(monkeypatch, cmp_mode="synthesizer"):
    import telegram_bot
    import voice_queue
    importlib.reload(telegram_bot)
    settings.set_compare_mode(cmp_mode)
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(delivery.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(telegram_bot.Config, "LOCAL_POLISH_CMD", "dummy_local")
    monkeypatch.setattr(telegram_bot, "make_sender", lambda t, c, reaction_msg_id=None: _ResilienceSender().as_sender())
    monkeypatch.setattr(telegram_bot, "_download_file", lambda t, fid: b"ogg-bytes")
    return telegram_bot, voice_queue


def test_b05_process_job_fast_and_slow_synthesizer_and_trace_consistency(monkeypatch):
    bot, vq = _setup_bot(monkeypatch, "synthesizer")

    # 1) سريع: السبك ينتهي قبل عودة finalize في process_job
    monkeypatch.setattr(bot, "transcribe_audio_bytes_ex",
                        lambda b, m, **kw: ("خام سريع", "gemini-3.6-flash", "K1", {"elapsed": 0.4, "skipped": []}))
    monkeypatch.setattr(text_assist, "polish_gemini", lambda r, ro, gl, **kw: ("مسودة فلاش", None))
    monkeypatch.setattr(text_assist, "polish_local", lambda r, ro, gl, **kw: ("مسودة شلبي مختلفة كلياً", None))
    monkeypatch.setattr(text_assist, "synthesize", lambda r, g, s, **kw: ("ذهبية سريعة", None))

    j_fast = vq.new_telegram_job("777", {"message_id": 10, "chat": {"id": 777},
                                         "voice": {"file_id": "FF1", "file_unique_id": "UF1"}},
                                 "FF1", "audio/ogg", "voice")
    j_fast.meta["token"] = "TOK"
    jobs.record(j_fast.job_id, "received", **j_fast.journal_fields())
    bot.process_job(j_fast)
    deadline = time.monotonic() + 3
    while delivery.active_background_threads() and time.monotonic() < deadline:
        time.sleep(0.02)

    evs_fast = [r for r in jobs.events_of(j_fast.job_id) if r["event"] == "sent"]
    assert len(evs_fast) == 1
    assert evs_fast[0]["polished_text"] == "ذهبية سريعة"
    assert evs_fast[0].get("synth_golden") is True
    tr_fast = observability.trace_text(j_fast.job_id)
    assert tr_fast and "🏆 سبك ذهبي" in tr_fast and "(مسودة)" not in tr_fast

    # 2) بطيء: الخيط الخلفي محجوز إلى ما بعد عودة process_job
    gate = threading.Event()
    monkeypatch.setattr(text_assist, "polish_local",
                        lambda r, ro, gl, **kw: gate.wait(5) and ("مسودة شلبي متأخرة ومختلفة تماماً", None))
    monkeypatch.setattr(text_assist, "synthesize", lambda r, g, s, **kw: ("ذهبية متأخرة", None))

    j_slow = vq.new_telegram_job("777", {"message_id": 11, "chat": {"id": 777},
                                         "voice": {"file_id": "FF2", "file_unique_id": "UF2"}},
                                 "FF2", "audio/ogg", "voice")
    j_slow.meta["token"] = "TOK"
    jobs.record(j_slow.job_id, "received", **j_slow.journal_fields())
    bot.process_job(j_slow)

    tr_mid = observability.trace_text(j_slow.job_id)
    assert tr_mid and "(مسودة)" in tr_mid

    gate.set()
    delivery.wait_background_threads(timeout=5)
    evs_slow = [r for r in jobs.events_of(j_slow.job_id) if r["event"] == "sent"]
    assert len(evs_slow) == 2
    assert evs_slow[0].get("draft") is True
    assert evs_slow[1].get("synth_golden") is True and evs_slow[1]["polished_text"] == "ذهبية متأخرة"
    assert jobs.find_recent_result("UF2")["polished_text"] == "ذهبية متأخرة"

    # 3) بطيء مع فشل شلبي: on_polished يُغلق المسودة بسجل sent نهائي يحمل مسودة جيميني
    gate2 = threading.Event()
    monkeypatch.setattr(text_assist, "polish_local",
                        lambda r, ro, gl, **kw: (None, "timeout") if gate2.wait(5) else (None, "x"))
    j_fb = vq.new_telegram_job("777", {"message_id": 12, "chat": {"id": 777},
                                       "voice": {"file_id": "FF3", "file_unique_id": "UF3"}},
                               "FF3", "audio/ogg", "voice")
    j_fb.meta["token"] = "TOK"
    jobs.record(j_fb.job_id, "received", **j_fb.journal_fields())
    bot.process_job(j_fb)
    gate2.set()
    delivery.wait_background_threads(timeout=5)
    evs_fb = [r for r in jobs.events_of(j_fb.job_id) if r["event"] == "sent"]
    assert len(evs_fb) == 2 and "draft" not in evs_fb[1]
    assert evs_fb[1]["polished_text"] == "مسودة فلاش"


def test_b05_dedup_over_2000_skips_shelby_and_synth(monkeypatch):
    bot, vq = _setup_bot(monkeypatch, "synthesizer")
    long_golden = "منظم_مكرر_طويل_" * 250  # 3750 حرف (> 2000)

    monkeypatch.setattr(bot, "transcribe_audio_bytes_ex",
                        lambda b, m, **kw: ("خام أصلي " * 10, "gemini-3.6-flash", "K1", {"elapsed": 0.2, "skipped": []}))
    monkeypatch.setattr(text_assist, "polish_gemini", lambda r, ro, gl, **kw: ("مسودة", None))
    monkeypatch.setattr(text_assist, "polish_local", lambda r, ro, gl, **kw: ("شلبي آخر تماماً", None))
    monkeypatch.setattr(text_assist, "synthesize", lambda r, g, s, **kw: (long_golden, None))

    j1 = vq.new_telegram_job("777", {"message_id": 1, "chat": {"id": 777},
                                     "voice": {"file_id": "D1", "file_unique_id": "U_LONG_DEDUP"}},
                             "D1", "audio/ogg", "voice")
    j1.meta["token"] = "TOK"
    jobs.record(j1.job_id, "received", **j1.journal_fields())
    bot.process_job(j1)
    delivery.wait_background_threads(timeout=5)

    # إعادة الإرسال يجب ألا تستدعي أياً من المحلي أو السبّاك أو فلاش
    monkeypatch.setattr(text_assist, "polish_local", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("local called")))
    monkeypatch.setattr(text_assist, "synthesize", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("synth called")))
    monkeypatch.setattr(text_assist, "polish_gemini", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("gemini called")))

    j2 = vq.new_telegram_job("777", {"message_id": 2, "chat": {"id": 777},
                                     "voice": {"file_id": "D2", "file_unique_id": "U_LONG_DEDUP"}},
                             "D2", "audio/ogg", "voice")
    j2.meta["token"] = "TOK"
    jobs.record(j2.job_id, "received", **j2.journal_fields())
    r2 = bot.process_job(j2)
    assert r2["dedup"] is True
    assert r2["polished"] == long_golden
    assert jobs.load_state()[j2.job_id]["polished_text"] == long_golden


def test_b05_and_b06_polished_only_rescue_pending_and_raw_fallback(monkeypatch):
    bot, vq = _setup_bot(monkeypatch, "synthesizer")
    settings.set_delivery_mode("polished_only")

    # 1) إنقاذ بطيء في polished_only: عندما يفشل إرسال رسالة الانتظار نفسه ولا ينتهي الخيط بعد -> ممنوع تسجيل failed زائفة
    fs_none_wait = _ResilienceSender(fail_send_at={1})
    monkeypatch.setattr(bot, "make_sender", lambda t, c, reaction_msg_id=None: fs_none_wait.as_sender())
    monkeypatch.setattr(bot, "transcribe_audio_bytes_ex",
                        lambda b, m, **kw: ("نص خام لن يضيع", "gemini-3.6-flash", "K1", {"elapsed": 0.3, "skipped": []}))
    monkeypatch.setattr(text_assist, "polish_gemini", lambda r, ro, gl, **kw: (None, "503"))

    gate = threading.Event()
    monkeypatch.setattr(text_assist, "polish_local",
                        lambda r, ro, gl, **kw: gate.wait(5) and ("إنقاذ شلبي بعد تأخير", None))

    j_pend = vq.new_telegram_job("777", {"message_id": 31, "chat": {"id": 777},
                                         "voice": {"file_id": "RP1", "file_unique_id": "URP1"}},
                                 "RP1", "audio/ogg", "voice")
    j_pend.meta["token"] = "TOK"
    jobs.record(j_pend.job_id, "received", **j_pend.journal_fields())
    bot.process_job(j_pend)

    assert jobs.load_state()[j_pend.job_id]["state"] == "transcribed"  # ليس failed كاذب
    gate.set()
    delivery.wait_background_threads(timeout=5)
    st_pend = jobs.load_state()[j_pend.job_id]
    assert st_pend["state"] == "sent" and st_pend["polished_text"] == "إنقاذ شلبي بعد تأخير"

    # 2) B-06: فشل جيميني وشلبي معاً في polished_only -> إرسال الخام بالكامل + "أرسلت الخام بدلاً منه" + sent (polished=False)
    for local_fn in [lambda *a, **kw: (None, "local boom"), lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash"))]:
        fs_raw = _ResilienceSender()
        monkeypatch.setattr(bot, "make_sender", lambda t, c, reaction_msg_id=None: fs_raw.as_sender())
        monkeypatch.setattr(text_assist, "polish_local", local_fn)
        j_b06 = vq.new_telegram_job("777", {"message_id": 32, "chat": {"id": 777},
                                            "voice": {"file_id": "B06_" + str(time.monotonic()), "file_unique_id": "UB06_" + str(time.monotonic())}},
                                    "B06", "audio/ogg", "voice")
        j_b06.meta["token"] = "TOK"
        jobs.record(j_b06.job_id, "received", **j_b06.journal_fields())
        bot.process_job(j_b06)
        delivery.wait_background_threads(timeout=5)

        all_sent = [txt for _m, txt, _r, _p in fs_raw.sent] + [txt for _m, txt, _p in fs_raw.edits]
        assert any("نص خام لن يضيع" in t for t in all_sent)
        assert any("أرسلت الخام بدلاً منه" in t for t in all_sent)
        assert all("الخام فوق" not in t for t in all_sent)
        st_b06 = jobs.load_state()[j_b06.job_id]
        assert st_b06["state"] == "sent" and st_b06["polished"] is False

    # 3) فشل تام في تليجرام أيضاً -> تسجيل failed بوضوح
    fs_dead = _ResilienceSender(fail_send_at={1, 2, 3, 4}, with_doc=False)
    monkeypatch.setattr(bot, "make_sender", lambda t, c, reaction_msg_id=None: fs_dead.as_sender())
    monkeypatch.setattr(text_assist, "polish_local", lambda *a, **kw: (None, "err"))
    j_dead = vq.new_telegram_job("777", {"message_id": 33, "chat": {"id": 777},
                                         "voice": {"file_id": "DEAD", "file_unique_id": "UDEAD"}},
                                 "DEAD", "audio/ogg", "voice")
    j_dead.meta["token"] = "TOK"
    jobs.record(j_dead.job_id, "received", **j_dead.journal_fields())
    bot.process_job(j_dead)
    delivery.wait_background_threads(timeout=5)
    assert jobs.load_state()[j_dead.job_id]["state"] == "failed"


def test_b07_concurrent_archive_and_session_addenda_with_large_raw_and_session_switch():
    sa = sessions.start("جلسة أولى")
    sid_a = sa["id"]
    big_raw_1 = "خام_أول_كبير_" + ("أ" * 75_000)
    raw_2 = "خام_ثانٍ_بعد_السباق"

    assert archive.append(big_raw_1, model="gemini-3.6-flash", session_name=sa["name"], entry_id="e1")
    assert sessions.append_entry(big_raw_1, model="gemini-3.6-flash", session_id=sid_a, entry_id="e1")

    # يصل فويس ثانٍ ويُنظّم سريعاً، ثم يبدّل المالك الجلسة إلى جلسة جديدة قبل انتهاء سبك الفويس الأول
    assert archive.append(raw_2, model="gemini-3.6-flash", session_name=sa["name"], entry_id="e2")
    assert sessions.append_entry(raw_2, model="gemini-3.6-flash", session_id=sid_a, entry_id="e2")
    assert archive.append_polished("منظم الفويس الثاني", entry_id="e2")
    assert sessions.append_polished("منظم الفويس الثاني", session_id=sid_a, entry_id="e2")

    sessions.end()
    sb = sessions.start("جلسة ثانية")
    assert sb["id"] != sid_a

    # لقطة append-only للملفين قبل إلحاق addenda الفويس الأول
    with open(archive.all_path(), "rb") as f:
        prefix_all = f.read()
    with open(sessions.session_file(sid_a), "rb") as f:
        prefix_sess = f.read()

    # الآن يكتمل عنوان ومنظم الفويس الأول المتأخر باستخدام entry_id و session_id الصريحين
    assert archive.append_title("عنوان الفويس الأول", entry_id="e1")
    assert archive.append_polished("منظم ذهبي للفويس الأول", entry_id="e1")
    assert sessions.append_title("عنوان الفويس الأول", session_id=sid_a, entry_id="e1")
    assert sessions.append_polished("منظم ذهبي للفويس الأول", session_id=sid_a, entry_id="e1")

    # ضمان append-only 100%: كل البايتات السابقة بقيت في بداية الملف كما هي
    with open(archive.all_path(), "rb") as f:
        assert f.read().startswith(prefix_all)
    with open(sessions.session_file(sid_a), "rb") as f:
        assert f.read().startswith(prefix_sess)

    arch_entries = archive.last_entries(5)
    assert len(arch_entries) == 2
    assert arch_entries[0]["raw"] == big_raw_1
    assert arch_entries[0]["title"] == "عنوان الفويس الأول"
    assert arch_entries[0]["polished"] == "منظم ذهبي للفويس الأول"
    assert arch_entries[1]["raw"] == raw_2
    assert arch_entries[1]["polished"] == "منظم الفويس الثاني"

    sess_entries = sessions.entries(sid_a)
    assert len(sess_entries) == 2
    assert sess_entries[0]["raw"] == big_raw_1
    assert sess_entries[0]["title"] == "عنوان الفويس الأول"
    assert sess_entries[0]["polished"] == "منظم ذهبي للفويس الأول"
    assert sess_entries[1]["raw"] == raw_2
    assert sess_entries[1]["polished"] == "منظم الفويس الثاني"
