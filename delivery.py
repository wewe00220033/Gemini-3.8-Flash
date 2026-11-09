# -*- coding: utf-8 -*-
"""
خط الإنتاج بعد التفريغ (مواصفة 07 — المرحلة 3c): قاموس → أرشفة → الرسالتان (خام + منظم).

الترتيب (07 §2):
  1. الخام بعد قاموس scope=both/raw_only (أمانة النص).
  2. **أرشفة قبل الإرسال** (outbox): فشل تليجرام لا يضيع النص (`/last`).
  3. رسالة الخام فوراً (رد على الفويس) داخل <pre> + البانر حسب header_target + التذييل.
  4. المنظم عبر المساعد النصي (flash-lite) **من النص** لا من الصوت؛ يُرسل رداً على رسالة الخام ليُربطا.
     فشله = رسالة قصيرة "⚠️ تعذر التنظيم الآن — الخام فوق" ولا يُعاد التفريغ.
  5. نص طويل (> POLISH_FILE_THRESHOLD) → ملف .md + معاينة 500 حرف.

كل الإرسال عبر دوال قابلة للحقن (send_text/send_document) ليُختبر بلا شبكة.
"""

import html
import re
import threading
import time
import uuid

from config import Config
import archive
import settings
import prompts
import glossary as glossary_mod
import sessions
import logkit


class Sender:
    """واجهة الإرسال القابلة للحقن: send_text(text, reply_to, parse_mode) -> message_id|None ؛ send_document(...) ؛ send_action(...) ؛ set_reaction(...) ؛ edit_text(...) ؛ delete_text(...)"""

    def __init__(self, send_text, send_document=None, send_action=None, set_reaction=None, edit_text=None, delete_text=None):
        self.send_text = send_text
        self.send_document = send_document
        self.send_action = send_action
        self.set_reaction = set_reaction
        self.edit_text = edit_text
        self.delete_text = delete_text


_bg_threads_lock = threading.Lock()
_bg_threads = set()


def _track_bg_thread(t):
    with _bg_threads_lock:
        _bg_threads.add(t)


def _untrack_bg_thread(t=None):
    cur = t or threading.current_thread()
    with _bg_threads_lock:
        _bg_threads.discard(cur)


def _start_bg_thread(target):
    """يشغّل مهمة خلفية مع تسجيلها لضمان إمكانية انتظارها عند الإغلاق الرشيق."""
    def _runner():
        try:
            target()
        finally:
            _untrack_bg_thread()

    t = threading.Thread(target=_runner, daemon=True)
    _track_bg_thread(t)
    try:
        t.start()
    except Exception:
        _untrack_bg_thread(t)
        raise
    return t


def active_background_threads():
    """يرجع قائمة خيوط التنظيم/السبك الخلفية الحية حالياً."""
    with _bg_threads_lock:
        return [t for t in list(_bg_threads) if t.is_alive()]


def wait_background_threads(timeout=15.0):
    """ينتظر اكتمال خيوط التنظيم/السبك الخلفية حتى timeout ثانية (يرجع عدد المتبقي)."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        alive = active_background_threads()
        if not alive:
            return 0
        rem = deadline - time.monotonic()
        if rem <= 0:
            return len(alive)
        for t in alive:
            r = deadline - time.monotonic()
            if r <= 0:
                break
            t.join(timeout=r)


def prepare_texts(raw_text, glossary=None):
    """يرجع (raw_after_glossary, polished_base_for_glossary_fn). القاموس scope=both/raw_only على الخام فقط."""
    gl = glossary if glossary is not None else glossary_mod.get_glossary()
    raw, _n = gl.apply(raw_text, target="raw")
    return raw, gl


def raw_message_html(raw_text, footer_html="", title=None, session_name=None):
    """<pre>البانر(اختياري)+النص</pre> + التذييل — البانر داخل كتلة النسخ (06 §4 قرار المالك).
    المرحلة 4: سطر ترويسة اختياري فوق الكتلة «🏷️ العنوان · 📂 الجلسة» (خارج كتلة النسخ حتى لا يلوّث النص)."""
    body = raw_text
    if settings.header_for("raw"):
        body = archive.banner_text() + "\n" + raw_text
    head = _entry_head_html(title, session_name)
    out = (head + "\n" if head else "") + f"<pre>{html.escape(body)}</pre>"
    if footer_html:
        out += "\n" + footer_html
    return out


def _entry_head_html(title, session_name):
    parts = []
    if title:
        parts.append(f"🏷️ <b>{html.escape(title)}</b>")
    if session_name:
        parts.append(f"📂 {html.escape(session_name)}")
    return " · ".join(parts)


def polished_message_html(polished_text, role_id, session_name=None, provider_tag=None, title=None):
    role = prompts.get_role(role_id) or {}
    head = ""
    if title:
        head += f"🏷️ <b>{html.escape(title)}</b>\n"      # العنوان يُحسب بعد الخام — يظهر هنا
    head += f"<b>📝 النسخة المنظمة</b> · دور: {html.escape(role.get('name', role_id))}"
    if session_name:
        head += f" · جلسة: «{html.escape(session_name)}»"
    if provider_tag:
        head += f" · {provider_tag}"
    body = polished_text
    if settings.header_for("polished"):
        body = archive.banner_text() + "\n" + polished_text
    return head + "\n" + f"<pre>{html.escape(body)}</pre>"


def _local_timeout_label():
    """تنسيق مهلة المزود المحلي ديناميكياً من Config.LOCAL_POLISH_TIMEOUT (مثلاً 180ث أو 8.5ث)."""
    try:
        val = float(getattr(Config, "LOCAL_POLISH_TIMEOUT", 180.0) or 180.0)
    except (TypeError, ValueError):
        val = 180.0
    return f"{int(val)}ث" if val.is_integer() else f"{round(val, 1)}ث"


def merged_compare_message_html(gemini_text, shelby_text=None, role_id=None, session_name=None, shelby_error=None, status_line=None):
    role = prompts.get_role(role_id) or {}
    head = f"<b>📝 النسخة المنظمة</b> · دور: {html.escape(role.get('name', role_id))}"
    if session_name:
        head += f" · جلسة: «{html.escape(session_name)}»"

    body_gemini = gemini_text
    if settings.header_for("polished"):
        body_gemini = archive.banner_text() + "\n" + gemini_text

    blocks = [
        head,
        "🤖 <b>[جيميني]</b>:",
        f"<pre>{html.escape(body_gemini)}</pre>",
    ]

    if shelby_text:
        body_shelby = shelby_text
        if settings.header_for("polished"):
            body_shelby = archive.banner_text() + "\n" + shelby_text
        blocks.append("🦙 <b>[شلبي]</b>:")
        blocks.append(f"<pre>{html.escape(body_shelby)}</pre>")
    elif shelby_error:
        blocks.append(f"⚠️ <i>تعذر تشغيل شلبي محلياً ({html.escape(str(shelby_error))})</i>")
    elif status_line:
        blocks.append(status_line)
    else:
        blocks.append("⏳ <i>جاري تحضير شلبي محلياً...</i>")

    return "\n".join(blocks)


def synthesizer_interim_message_html(gemini_text, role_id=None, session_name=None, title=None, shelby_status="waiting", error_detail=None):
    """
    الرسالة الحية الأولى أثناء تشغيل السبك:
    تعرض مسودة جيميني الفورية كاملة داخل <pre>، مع شريط الحالة الحي بالأسفل خارج <pre>.
    """
    role = prompts.get_role(role_id) or {}
    head_parts = []
    if title:
        head_parts.append(f"🏷️ <b>{html.escape(title)}</b>")
    head_txt = f"<b>📝 النسخة المنظمة</b> · دور: {html.escape(role.get('name', role_id))}"
    if session_name:
        head_txt += f" · جلسة: «{html.escape(session_name)}»"
    head_parts.append(head_txt)

    body = gemini_text
    if settings.header_for("polished"):
        body = archive.banner_text() + "\n" + gemini_text

    blocks = [
        "\n".join(head_parts),
        f"<pre>{html.escape(body)}</pre>",
    ]

    t_label = _local_timeout_label()
    if shelby_status == "waiting":
        blocks.append(f"⏳ <i>جاري توليد النسخة الذهبية المسبوكة مع شلبي (مهلة {t_label})...</i>")
    elif shelby_status == "timeout":
        blocks.append(f"⚠️ <i>تم اعتماد مسودة جيميني (تجاوز شلبي المهلة المحددة {t_label})</i>")
    elif shelby_status == "error":
        err_msg = html.escape(str(error_detail or "خطأ غير معروف"))
        blocks.append(f"⚠️ <i>تم اعتماد مسودة جيميني (تعذر شلبي: {err_msg})</i>")
    elif shelby_status == "noop":
        blocks.append("ℹ️ <i>تم اعتماد مسودة جيميني (أعاد شلبي النص دون تنظيم فعلي)</i>")

    return "\n".join(blocks)


def synthesizer_final_message_html(golden_text, role_id=None, session_name=None, title=None, telemetry=None):
    """
    الرسالة الذهبية النهائية المسبوكة بعد نجاح الوكيل الثالث:
    تعرض النسخة الذهبية داخل <pre>، وتذييل التوقيت الدقيق خارج <pre>.
    """
    role = prompts.get_role(role_id) or {}
    head_parts = []
    if title:
        head_parts.append(f"🏷️ <b>{html.escape(title)}</b>")
    head_txt = f"<b>🏆 النسخة الذهبية المسبوكة</b> · دور: {html.escape(role.get('name', role_id))}"
    if session_name:
        head_txt += f" · جلسة: «{html.escape(session_name)}»"
    head_parts.append(head_txt)

    body = golden_text
    if settings.header_for("polished"):
        body = archive.banner_text() + "\n" + golden_text

    blocks = [
        "\n".join(head_parts),
        f"<pre>{html.escape(body)}</pre>",
    ]

    if telemetry and isinstance(telemetry, dict):
        t_gem = f"{telemetry.get('gemini', 0):.1f}ث"
        t_shl = f"{telemetry.get('shelby', 0):.1f}ث"
        t_syn = f"{telemetry.get('synth', 0):.1f}ث"
        t_tot = f"{telemetry.get('total', 0):.1f}ث"
        blocks.append(f"⚡ فلاش: {t_gem} | 🤖 شلبي: {t_shl} | 🏆 السبك: {t_syn} | ⏱️ إجمالي: {t_tot}")

    return "\n".join(blocks)


def prepare_for_web(raw_text, model=None, key_name=None, info=None, polish_fn=None):
    """
    مسار الويب (13 §3 Web Parity): **نفس الـ pipeline** — قاموس (scope الخام) → الدور الفعّال → تنظيم flash-lite
    (لو WEB_POLISH) → أرشفة في ALL.md بوسم «🌐 ويب» (+ المنظم) → كتابة في الجلسة النشطة. لا إرسال تليجرام.
    يرجع (raw, archived, polished, meta) — meta = {role, session, polish_error, source}.
    أي خطأ في القاموس/التنظيم/الأرشيف لا يُسقط التفريغ (نرجع الخام كما هو).
    """
    info = info or {}
    try:
        raw, gl = prepare_texts(raw_text)
    except Exception as e:
        print(f"⚠️ القاموس (ويب): {e}")
        raw, gl = raw_text, None
    role_id = settings.effective_role()
    session_name = _safe_session_name()
    session_id = _safe_session_id() if session_name else None
    entry_id = uuid.uuid4().hex[:10]
    polished, polish_error = None, None
    if Config.WEB_POLISH and (raw or "").strip():
        try:
            if polish_fn is None:
                import text_assist
                polish_fn = text_assist.polish
            kwargs = {"context_summary": sessions.summary()} if _accepts_context_summary(polish_fn) else {}
            polished, polish_error = polish_fn(raw, role_id, gl, **kwargs)
            if polish_error:
                polish_error = logkit.redact(polish_error)
        except Exception as e:
            polished, polish_error = None, logkit.redact(f"{type(e).__name__}: {e}")
            print(f"⚠️ التنظيم (ويب): {polish_error}")
    try:
        archived = archive.append(raw, model=model, elapsed=info.get("elapsed"), key_name=key_name,
                                  session_name=session_name, polished=polished, extra="🌐 ويب", entry_id=entry_id)
    except Exception as e:
        print(f"⚠️ الأرشفة (ويب): {e}")
        archived = False
    if session_name:
        sessions.append_entry(raw, model=model, elapsed=info.get("elapsed"), key_name=key_name,
                              extra="🌐 ويب", session_id=session_id, entry_id=entry_id)
        if polished:
            sessions.append_polished(polished, session_id=session_id, entry_id=entry_id)
    return raw, archived, polished, {"role": role_id, "session": session_name, "polish_error": polish_error, "source": "web"}


def _safe_session_name():
    try:
        return sessions.current_name()
    except Exception as e:  # pragma: no cover
        print(f"⚠️ الجلسات: {e}")
        return None


def _safe_session_id():
    try:
        return sessions.current_id()
    except Exception:  # pragma: no cover
        return None


def _voice_title(raw, title_fn):
    """عنوان ذكي للفويس (08 §3) — نصي فقط، بعد إرسال الخام لا قبله. أي فشل → None."""
    try:
        fn = title_fn if title_fn is not None else sessions.voice_title
        return fn(raw)
    except Exception as e:  # pragma: no cover
        print(f"⚠️ التسمية: {e}")
        return None


def _deliver_shelby_rescue(raw, model, role_id, gl, l2, reason, mode, footer_html, session_name, session_id,
                           entry_id, archive_entry, sender, reply_to_id, anchor, local_polish_fn,
                           job_id, job_meta, res_lock, result, _dwrite_lock, _dwrite_done, _bg_finalize):
    """إنقاذ شلبي المحلي عند تعذر جيميني السحابي في أوضاع المقارنة."""
    with res_lock:
        result["_rescue_pending"] = True
    red_reason = html.escape(logkit.redact(str(reason or "503"))[:300])
    rescue_msg = f"⏳ <i>تعذر منظم جيميني السحابي ({red_reason}) — جاري تنظيم النص عبر شلبي محلياً...</i>"
    mid = sender.send_text(rescue_msg, anchor, "HTML")
    with res_lock:
        result["polished_msg_id"] = mid

    def _record_terminal_fallback(raw_mid, err_txt):
        if not job_id:
            return
        import jobs
        if raw_mid is not None:
            extra_f = dict(job_meta or {})
            extra_f.pop("polished", None)
            extra_f.pop("polished_text", None)
            extra_f.pop("draft", None)
            if "text" in extra_f and extra_f["text"] is not None:
                extra_f["text"] = str(extra_f["text"])[:16000]
            jobs.record(job_id, "sent", polished=False, **extra_f)
        else:
            jobs.record(job_id, "failed", error=f"send failed (archived — /last): {err_txt}"[:200])
        with res_lock:
            result["_synth_recorded"] = True

    def _bg_shelby_rescue():
        try:
            s_fn = local_polish_fn
            if s_fn is None:
                import text_assist
                s_fn = text_assist.polish_local
            s_kwargs = {"context_summary": l2} if _accepts_context_summary(s_fn) else {}
            s_text, s_err = s_fn(raw, role_id, gl, **s_kwargs)
            if s_text:
                if getattr(sender, "set_reaction", None):
                    try:
                        sender.set_reaction(Config.REACTION_DONE)
                    except Exception:
                        pass
                s_html = polished_message_html(
                    s_text, role_id, session_name,
                    provider_tag="🦙 <b>[شلبي]</b>", title=result["title"]
                )
                sent_pol_mid = _replace_or_send(
                    sender, mid, anchor, s_html, s_text,
                    filename_prefix="shelby_polished",
                    caption="🦙 نسخة شلبي المنظمة (بديل لتعذر جيميني السحابي)",
                    head_html=_entry_head_html(result["title"], session_name),
                )
                if sent_pol_mid is not None:
                    with res_lock:
                        result["polished_msg_id"] = sent_pol_mid
                        result["_rescue_pending"] = False
                    _bg_finalize(s_text, s_text, session_text=s_text, is_golden=False, record_job=True)
                    return

                # فشل إرسال رسالة شلبي نفسه للمستخدم: نؤرشف المنظم وننقذ الخام لو وضع polished_only
                if archive_entry:
                    with _dwrite_lock:
                        if not _dwrite_done["v"]:
                            _dwrite_done["v"] = True
                            try:
                                archive.append_polished(s_text, entry_id=entry_id)
                                if session_name:
                                    sessions.append_polished(s_text, session_id=session_id, entry_id=entry_id)
                            except Exception as e:
                                print(f"⚠️ Archive/session rescue fallback update: {e}")
                with res_lock:
                    result["polished_msg_id"] = None
                    result["_rescue_pending"] = False
                    raw_mid = result.get("raw_msg_id")
                if mode == "polished_only" and raw_mid is None:
                    raw_mid = _send_long(
                        sender, raw_message_html(raw, footer_html, result["title"], session_name),
                        raw, reply_to_id, filename_prefix="transcript", caption="📄 التفريغ الخام",
                        head_html=_entry_head_html(result["title"], session_name),
                    )
                    with res_lock:
                        result["raw_msg_id"] = raw_mid
                _record_terminal_fallback(raw_mid, "telegram send failed after shelby")
                return

            err_txt = logkit.redact(str(s_err or reason or ""))[:500]
            if getattr(sender, "set_reaction", None):
                try:
                    sender.set_reaction(Config.REACTION_DONE)
                except Exception:
                    pass
            with res_lock:
                raw_mid = result.get("raw_msg_id")
            if mode == "polished_only" and raw_mid is None:
                raw_mid = _send_long(
                    sender, raw_message_html(raw, footer_html, result["title"], session_name),
                    raw, reply_to_id, filename_prefix="transcript", caption="📄 التفريغ الخام",
                    head_html=_entry_head_html(result["title"], session_name),
                )
                with res_lock:
                    result["raw_msg_id"] = raw_mid
                if raw_mid is not None:
                    err_html = f"⚠️ <i>تعذر التنظيم عبر جيميني وشلبي ({html.escape(err_txt)}) — أرسلت الخام بدلاً منه.</i>"
                else:
                    err_html = f"⚠️ <i>تعذر التنظيم عبر جيميني وشلبي ({html.escape(err_txt)}) — النص محفوظ بالأرشيف (/last).</i>"
                err_anchor = raw_mid or reply_to_id
            else:
                err_html = f"⚠️ <i>تعذر التنظيم عبر جيميني وشلبي ({html.escape(err_txt)}) — الخام فوق.</i>"
                err_anchor = anchor
            _replace_or_send(
                sender, mid, err_anchor, err_html, _html_to_plain(err_html),
                filename_prefix="polish_error",
                caption="⚠️ تعذر التنظيم",
                head_html=_entry_head_html(result["title"], session_name),
            )
            with res_lock:
                result["polished_msg_id"] = None
                result["polish_error"] = err_txt
                result["_rescue_pending"] = False
            if raw_mid is None or mode == "polished_only":
                _record_terminal_fallback(raw_mid, err_txt)
        except Exception as e:
            err_txt = logkit.redact(f"{type(e).__name__}: {e}")[:500]
            print(f"⚠️ Shelby rescue error: {err_txt}")
            if getattr(sender, "set_reaction", None):
                try:
                    sender.set_reaction(Config.REACTION_DONE)
                except Exception:
                    pass
            with res_lock:
                raw_mid = result.get("raw_msg_id")
            if mode == "polished_only" and raw_mid is None:
                raw_mid = _send_long(
                    sender, raw_message_html(raw, footer_html, result["title"], session_name),
                    raw, reply_to_id, filename_prefix="transcript", caption="📄 التفريغ الخام",
                    head_html=_entry_head_html(result["title"], session_name),
                )
                with res_lock:
                    result["raw_msg_id"] = raw_mid
                if raw_mid is not None:
                    err_html = f"⚠️ <i>تعذر التنظيم عبر جيميني وشلبي ({html.escape(err_txt)}) — أرسلت الخام بدلاً منه.</i>"
                else:
                    err_html = f"⚠️ <i>تعذر التنظيم عبر جيميني وشلبي ({html.escape(err_txt)}) — النص محفوظ بالأرشيف (/last).</i>"
                err_anchor = raw_mid or reply_to_id
            else:
                err_html = f"⚠️ <i>تعذر التنظيم عبر جيميني وشلبي ({html.escape(err_txt)}) — الخام فوق.</i>"
                err_anchor = anchor
            _replace_or_send(
                sender, mid, err_anchor, err_html, _html_to_plain(err_html),
                filename_prefix="polish_error",
                caption="⚠️ تعذر التنظيم",
                head_html=_entry_head_html(result["title"], session_name),
            )
            with res_lock:
                result["polished_msg_id"] = None
                result["polish_error"] = err_txt
                result["_rescue_pending"] = False
            if raw_mid is None or mode == "polished_only":
                _record_terminal_fallback(raw_mid, err_txt)

    return _start_bg_thread(_bg_shelby_rescue)


def _deliver_synthesizer(raw, polished, role_id, gl, l2, session_name, sender, anchor, local_polish_fn,
                         t0_flow, gemini_elapsed, res_lock, result, _bg_finalize):
    """وضع السبك الثلاثي (Synthesizer): مسودة جيميني فورية ثم استبدالها بالنسخة الذهبية المسبوكة."""
    max_edit = Config.TELEGRAM_MAX_MSG - 50
    t_label = _local_timeout_label()
    init_html = synthesizer_interim_message_html(
        polished, role_id=role_id, session_name=session_name,
        title=result["title"], shelby_status="waiting"
    )
    draft_separated = len(init_html) > max_edit
    if draft_separated:
        clean_draft_html = polished_message_html(
            polished, role_id, session_name, title=result["title"]
        )
        has_doc = getattr(sender, "send_document", None) is not None
        draft_mid = _send_long(
            sender, clean_draft_html, polished, anchor,
            filename_prefix="gemini_draft",
            caption="📝 مسودة جيميني المنظمة (طويلة → ملف)",
            head_html=_entry_head_html(result["title"], session_name),
            force_document=(len(clean_draft_html) > max_edit and has_doc),
        )
        draft_sent = (draft_mid is not None)
        wait_anchor = draft_mid or anchor
        mid = sender.send_text(
            f"⏳ <i>جاري توليد النسخة الذهبية المسبوكة مع شلبي (مهلة {t_label})...</i>",
            wait_anchor,
            "HTML",
        )
        result["polished_msg_id"] = draft_mid or mid
    else:
        draft_sent = True
        wait_anchor = anchor
        mid = _send_long(
            sender, init_html, polished, anchor,
            filename_prefix="polished",
            caption="📝 النسخة المنظمة (طويلة → ملف)",
            head_html=_entry_head_html(result["title"], session_name),
        )
        result["polished_msg_id"] = mid

    def _bg_synthesizer():
        use_short_above = (draft_separated and draft_sent)

        def _draft_fallback_view(status, short_html, err_detail=None):
            if use_short_above:
                return short_html, _html_to_plain(short_html)
            full = synthesizer_interim_message_html(
                polished, role_id=role_id, session_name=session_name,
                title=result["title"], shelby_status=status, error_detail=err_detail
            )
            return full, polished

        try:
            t0_shelby = time.monotonic()
            s_fn = local_polish_fn
            if s_fn is None:
                import text_assist
                s_fn = text_assist.polish_local
            s_kwargs = {"context_summary": l2} if _accepts_context_summary(s_fn) else {}
            s_text, s_err = s_fn(raw, role_id, gl, **s_kwargs)
            shelby_elapsed = time.monotonic() - t0_shelby

            if s_text:
                import text_assist
                try:
                    is_noop = text_assist.is_shelby_noop(raw, s_text, role_id=role_id)
                except TypeError:
                    is_noop = text_assist.is_shelby_noop(raw, s_text)
                if is_noop:
                    text_assist.note_shelby_noop()
                    print("⚠️ Shelby returned raw text unchanged (No-Op) — تخطي نداء السبك الثالث لتوفير الكوتة.")
                    _bg_finalize(polished, polished, session_text=polished, is_golden=False)
                    noop_html, noop_plain = _draft_fallback_view(
                        "noop",
                        "ℹ️ <i>تم اعتماد مسودة جيميني أعلاه (أعاد شلبي النص دون تنظيم فعلي)</i>",
                    )
                    sent_mid = _replace_or_send(
                        sender, mid, wait_anchor, noop_html, noop_plain,
                        filename_prefix="polished",
                        caption="📝 النسخة المنظمة (اعتماد جيميني)",
                        head_html=_entry_head_html(result["title"], session_name),
                    )
                    if not use_short_above and sent_mid is not None:
                        with res_lock:
                            result["polished_msg_id"] = sent_mid
                    return

                t0_synth = time.monotonic()
                synth_kwargs = {"context_summary": l2} if _accepts_context_summary(text_assist.synthesize) else {}
                golden_text, synth_err = text_assist.synthesize(
                    raw, polished, s_text, role_id=role_id, glossary=gl, **synth_kwargs
                )
                synth_elapsed = time.monotonic() - t0_synth

                if golden_text:
                    _bg_finalize(
                        golden_text,
                        f"[نسخة ذهبية مسبوكة]\n{golden_text}",
                        session_text=golden_text,
                        is_golden=True,
                        synth_elapsed=synth_elapsed,
                    )
                    wall_total = time.monotonic() - t0_flow
                    telemetry = {
                        "gemini": gemini_elapsed,
                        "shelby": shelby_elapsed,
                        "synth": synth_elapsed,
                        "total": wall_total,
                    }
                    full_html = synthesizer_final_message_html(
                        golden_text, role_id=role_id, session_name=session_name,
                        title=result["title"], telemetry=telemetry
                    )
                    sent_mid = _replace_or_send(
                        sender, mid, wait_anchor, full_html, golden_text,
                        filename_prefix="golden_synthesized",
                        caption="🏆 النسخة الذهبية المسبوكة",
                        head_html=_entry_head_html(result["title"], session_name),
                    )
                    if not use_short_above and sent_mid is not None:
                        with res_lock:
                            result["polished_msg_id"] = sent_mid
                else:
                    _bg_finalize(polished, polished, session_text=polished, is_golden=False)
                    fallback_html = merged_compare_message_html(
                        polished, shelby_text=s_text, role_id=role_id, session_name=session_name
                    )
                    sent_mid = _replace_or_send(
                        sender, mid, wait_anchor, fallback_html, f"{polished}\n\n{s_text}",
                        filename_prefix="fallback_compare",
                        caption="📝 المقارنة المدمجة (بديل لفشل السبك)",
                        head_html=_entry_head_html(result["title"], session_name),
                    )
                    if not use_short_above and sent_mid is not None:
                        with res_lock:
                            result["polished_msg_id"] = sent_mid
            else:
                _bg_finalize(polished, polished, session_text=polished, is_golden=False)
                err_s = str(s_err or "خطأ غير معروف")
                if "دون تنظيم فعلي" in err_s:
                    print("⚠️ Shelby returned raw text unchanged (No-Op) — تخطي نداء السبك الثالث لتوفير الكوتة.")
                    noop_html, noop_plain = _draft_fallback_view(
                        "noop",
                        "ℹ️ <i>تم اعتماد مسودة جيميني أعلاه (أعاد شلبي النص دون تنظيم فعلي)</i>",
                    )
                    sent_mid = _replace_or_send(
                        sender, mid, wait_anchor, noop_html, noop_plain,
                        filename_prefix="polished",
                        caption="📝 النسخة المنظمة (اعتماد جيميني)",
                        head_html=_entry_head_html(result["title"], session_name),
                    )
                    if not use_short_above and sent_mid is not None:
                        with res_lock:
                            result["polished_msg_id"] = sent_mid
                    return
                is_to = "مهلة" in err_s or "timeout" in err_s.lower()
                status = "timeout" if is_to else "error"
                err_msg = html.escape(logkit.redact(err_s)[:400])
                short_txt = (
                    f"⚠️ <i>تم اعتماد مسودة جيميني أعلاه (تجاوز شلبي المهلة المحددة {t_label})</i>"
                    if status == "timeout"
                    else f"⚠️ <i>تم اعتماد مسودة جيميني أعلاه (تعذر شلبي: {err_msg})</i>"
                )
                to_html, to_plain = _draft_fallback_view(status, short_txt, err_detail=err_msg)
                sent_mid = _replace_or_send(
                    sender, mid, wait_anchor, to_html, to_plain,
                    filename_prefix="polished",
                    caption="📝 النسخة المنظمة (اعتماد جيميني بعد تعذر شلبي)",
                    head_html=_entry_head_html(result["title"], session_name),
                )
                if not use_short_above and sent_mid is not None:
                    with res_lock:
                        result["polished_msg_id"] = sent_mid
        except Exception as e:
            err_txt = logkit.redact(f"{type(e).__name__}: {e}")[:400]
            print(f"⚠️ Synthesizer bg error: {err_txt}")
            _bg_finalize(polished, polished, session_text=polished, is_golden=False)
            err_html, err_plain = _draft_fallback_view(
                "error",
                f"⚠️ <i>تم اعتماد مسودة جيميني أعلاه (خطأ أثناء السبك: {html.escape(err_txt)})</i>",
                err_detail=err_txt,
            )
            sent_mid = _replace_or_send(
                sender, mid, wait_anchor, err_html, err_plain,
                filename_prefix="polished",
                caption="📝 النسخة المنظمة (اعتماد جيميني بعد خطأ السبك)",
                head_html=_entry_head_html(result["title"], session_name),
            )
            if not use_short_above and sent_mid is not None:
                with res_lock:
                    result["polished_msg_id"] = sent_mid

    return _start_bg_thread(_bg_synthesizer)


def _deliver_merged(raw, polished, role_id, gl, l2, session_name, sender, anchor, local_polish_fn,
                    res_lock, result, on_polished):
    """وضع المقارنة المدمجة (merged): جيميني + انتظار شلبي مع إغلاق رسالة الانتظار دائماً."""
    max_edit = Config.TELEGRAM_MAX_MSG - 50
    has_doc = getattr(sender, "send_document", None) is not None
    init_html = merged_compare_message_html(polished, shelby_text=None, role_id=role_id, session_name=session_name)
    draft_separated = len(init_html) > max_edit
    if draft_separated:
        clean_draft_html = polished_message_html(
            polished, role_id, session_name, provider_tag="🤖 <b>[جيميني]</b>", title=result["title"]
        )
        draft_mid = _send_long(
            sender, clean_draft_html, polished, anchor,
            filename_prefix="gemini_polished",
            caption="🤖 نسخة جيميني المنظمة (طويلة → ملف)",
            head_html=_entry_head_html(result["title"], session_name),
            force_document=(len(clean_draft_html) > max_edit and has_doc),
        )
        draft_sent = (draft_mid is not None)
        wait_anchor = draft_mid or anchor
        mid = sender.send_text("⏳ <i>جاري تحضير شلبي محلياً...</i>", wait_anchor, "HTML")
        result["polished_msg_id"] = draft_mid or mid
    else:
        draft_sent = True
        wait_anchor = anchor
        mid = _send_long(
            sender, init_html, polished, anchor,
            filename_prefix="polished",
            caption="📝 النسخة المنظمة (طويلة → ملف)",
            head_html=_entry_head_html(result["title"], session_name),
        )
        result["polished_msg_id"] = mid

    def _notify_merged():
        if on_polished is not None:
            try:
                on_polished(polished, is_golden=False, synth_elapsed=None)
            except TypeError:
                try:
                    on_polished(polished)
                except Exception as e:
                    print(f"⚠️ on_polished callback error: {e}")
            except Exception as e:
                print(f"⚠️ on_polished callback error: {e}")

    def _bg_shelby():
        try:
            s_fn = local_polish_fn
            if s_fn is None:
                import text_assist
                s_fn = text_assist.polish_local
            s_kwargs = {"context_summary": l2} if _accepts_context_summary(s_fn) else {}
            s_text, s_err = s_fn(raw, role_id, gl, **s_kwargs)
            if s_text:
                full_html = merged_compare_message_html(
                    polished, shelby_text=s_text, role_id=role_id, session_name=session_name
                )
                s_single = polished_message_html(
                    s_text, role_id, session_name, provider_tag="🦙 <b>[شلبي]</b>", title=result["title"]
                )
                if draft_separated:
                    if draft_sent:
                        _replace_or_send(
                            sender, mid, wait_anchor, s_single, s_text,
                            filename_prefix="shelby_polished",
                            caption="🦙 نسخة شلبي المنظمة",
                            head_html=_entry_head_html(result["title"], session_name),
                        )
                    else:
                        _replace_or_send(
                            sender, mid, anchor, full_html, f"{polished}\n\n{s_text}",
                            filename_prefix="merged_polished",
                            caption="📝 المقارنة المدمجة (جيميني + شلبي)",
                            head_html=_entry_head_html(result["title"], session_name),
                        )
                elif len(full_html) <= max_edit:
                    sent_mid = _replace_or_send(
                        sender, mid, anchor, full_html, f"{polished}\n\n{s_text}",
                        filename_prefix="merged_polished",
                        caption="📝 المقارنة المدمجة (جيميني + شلبي)",
                        head_html=_entry_head_html(result["title"], session_name),
                    )
                    if sent_mid is not None:
                        with res_lock:
                            result["polished_msg_id"] = sent_mid
                else:
                    closed_html = merged_compare_message_html(
                        polished, shelby_text=None, role_id=role_id, session_name=session_name,
                        status_line="✅ <i>نسخة شلبي أدناه (مستقلة — لتجاوز حد الرسالة).</i>",
                    )
                    closed_ok = mid is not None and (
                        _try_edit(sender, mid, closed_html, "HTML", "merged:close")
                        or _try_edit(sender, mid, _html_to_plain(closed_html), None, "merged:close-plain")
                    )
                    if closed_ok:
                        _send_long(
                            sender, s_single, s_text, mid or anchor,
                            filename_prefix="shelby_polished",
                            caption="🦙 نسخة شلبي المنظمة",
                            head_html=_entry_head_html(result["title"], session_name),
                            force_document=(len(s_single) > max_edit and has_doc),
                        )
                    else:
                        sent_mid = _replace_or_send(
                            sender, mid, anchor, full_html, f"{polished}\n\n{s_text}",
                            filename_prefix="merged_polished",
                            caption="📝 المقارنة المدمجة (جيميني + شلبي)",
                            head_html=_entry_head_html(result["title"], session_name),
                        )
                        if sent_mid is not None:
                            with res_lock:
                                result["polished_msg_id"] = sent_mid
            else:
                red_err = logkit.redact(str(s_err or "تعذر تشغيل شلبي"))[:400]
                if draft_separated and draft_sent:
                    short_err = f"⚠️ <i>تعذر تشغيل شلبي محلياً ({html.escape(red_err)})</i>"
                    _replace_or_send(
                        sender, mid, wait_anchor, short_err, _html_to_plain(short_err),
                        filename_prefix="shelby_error", caption="⚠️ تعذر شلبي",
                    )
                else:
                    err_html = merged_compare_message_html(
                        polished, shelby_text=None, role_id=role_id, session_name=session_name,
                        shelby_error=red_err,
                    )
                    _replace_or_send(
                        sender, mid, anchor, err_html, polished,
                        filename_prefix="merged_polished", caption="📝 نسخة جيميني (تعذر شلبي)",
                        head_html=_entry_head_html(result["title"], session_name),
                    )
        except Exception as e:
            err_txt = logkit.redact(f"{type(e).__name__}: {e}")[:400]
            print(f"⚠️ Dual polish bg error: {err_txt}")
            if draft_separated and draft_sent:
                short_err = f"⚠️ <i>تعذر تشغيل شلبي محلياً ({html.escape(err_txt)})</i>"
                _replace_or_send(
                    sender, mid, wait_anchor, short_err, _html_to_plain(short_err),
                    filename_prefix="shelby_error", caption="⚠️ تعذر شلبي",
                )
            else:
                err_html = merged_compare_message_html(
                    polished, shelby_text=None, role_id=role_id, session_name=session_name,
                    shelby_error=err_txt,
                )
                _replace_or_send(
                    sender, mid, anchor, err_html, polished,
                    filename_prefix="merged_polished", caption="📝 نسخة جيميني (تعذر شلبي)",
                    head_html=_entry_head_html(result["title"], session_name),
                )
        finally:
            _notify_merged()

    return _start_bg_thread(_bg_shelby)


def _deliver_split(raw, polished, role_id, gl, l2, session_name, sender, anchor, local_polish_fn,
                   result, on_polished):
    """وضع المقارنة المنفصلة (split): جيميني في رسالة وشلبي في رسالة رد مستقلة."""
    max_edit = Config.TELEGRAM_MAX_MSG - 50
    has_doc = getattr(sender, "send_document", None) is not None
    gemini_html = polished_message_html(
        polished, role_id, session_name, provider_tag="🤖 <b>[جيميني]</b>", title=result["title"]
    )
    mid = _send_long(
        sender, gemini_html, polished, anchor,
        filename_prefix="gemini_polished",
        caption="🤖 نسخة جيميني المنظمة (طويلة → ملف)",
        head_html=_entry_head_html(result["title"], session_name),
        force_document=(len(gemini_html) > max_edit and has_doc),
    )
    result["polished_msg_id"] = mid

    def _bg_shelby_split():
        try:
            s_fn = local_polish_fn
            if s_fn is None:
                import text_assist
                s_fn = text_assist.polish_local
            s_kwargs = {"context_summary": l2} if _accepts_context_summary(s_fn) else {}
            s_text, s_err = s_fn(raw, role_id, gl, **s_kwargs)
            if s_text:
                s_html = polished_message_html(
                    s_text, role_id, session_name, provider_tag="🦙 <b>[شلبي]</b>", title=result["title"]
                )
                _send_long(
                    sender, s_html, s_text, mid or anchor,
                    filename_prefix="shelby_polished",
                    caption="🦙 نسخة شلبي المنظمة",
                    head_html=_entry_head_html(result["title"], session_name),
                    force_document=(len(s_html) > max_edit and has_doc),
                )
            elif s_err:
                red_err = logkit.redact(str(s_err))[:400]
                sender.send_text(f"⚠️ تعذر تشغيل شلبي محلياً ({html.escape(red_err)})", mid or anchor, "HTML")
        except Exception as e:
            print(f"⚠️ Dual polish split bg error: {logkit.redact(f'{type(e).__name__}: {e}')}")
        finally:
            if on_polished is not None:
                try:
                    on_polished(polished, is_golden=False, synth_elapsed=None)
                except TypeError:
                    try:
                        on_polished(polished)
                    except Exception as e:
                        print(f"⚠️ on_polished callback error: {e}")
                except Exception as e:
                    print(f"⚠️ on_polished callback error: {e}")

    return _start_bg_thread(_bg_shelby_split)


def deliver(raw_text, model, key_name, info, sender, reply_to_id=None, polish_fn=None, glossary=None,
            session_name=None, footer_html="", archive_entry=True, title_fn=None, local_polish_fn=None,
            job_id=None, on_polished=None, disable_compare=False, job_meta=None):
    """
    ينفّذ خط الإنتاج كاملاً. يرجع قاموساً: {raw, polished, raw_msg_id, polished_msg_id, mode, archived, polish_error,
    role, session, title, session_written, title_suggestion}.
    polish_fn(raw_text, role_id, glossary) -> (text|None, reason) — افتراضياً text_assist.polish (يُحقن في الاختبارات).
    title_fn(raw) -> str|None — عنوان الفويس (افتراضياً sessions.voice_title عبر flash-lite؛ يُحقن في الاختبارات).
    session_name: لو None تُستخدم الجلسة النشطة تلقائياً (المرحلة 4 — كتابة مزدوجة ALL.md + session_<id>.md).
    """
    mode = settings.delivery_mode()
    role_id = settings.effective_role()
    raw, gl = prepare_texts(raw_text, glossary)
    if session_name is None:
        session_name = _safe_session_name()
    session_id = _safe_session_id() if session_name else None
    entry_id = uuid.uuid4().hex[:10] if archive_entry else None
    res_lock = threading.Lock()
    result = {"raw": raw, "polished": None, "raw_msg_id": None, "polished_msg_id": None, "mode": mode,
              "archived": False, "polish_error": None, "role": role_id, "session": session_name, "title": None,
              "session_written": False, "title_suggestion": None, "summary_updated": False,
              "_lock": res_lock, "_synth_recorded": False, "_rescue_pending": False}

    # 2) أرشفة قبل أي إرسال (outbox) — archive_entry=False للمكرر (dedup): سبق أرشفته
    if archive_entry:
        result["archived"] = archive.append(raw, model=model, elapsed=(info or {}).get("elapsed"), key_name=key_name,
                                            session_name=session_name, entry_id=entry_id)
        if session_name:
            result["session_written"] = sessions.append_entry(raw, model=model, elapsed=(info or {}).get("elapsed"),
                                                              key_name=key_name, session_id=session_id, entry_id=entry_id)

    # 2b) عنوان الفويس (08 §3) — نداء lite واحد قصير، يُسجَّل append-only في الملفين.
    #     ⚠️ يُؤجَّل إلى ما بعد إرسال الخام: كان النداء المتزامن هنا يؤخر ظهور أول رسالة
    #     للمستخدم بمقدار زمن النداء (2–5 ثوانٍ مقاسة). الوظيفة محفوظة في _attach_title() أدناه.
    _titles_on = bool(archive_entry and Config.SMART_TITLE_ENABLED)
    title_first = _titles_on and getattr(Config, "SMART_TITLE_BEFORE_RAW", False)
    title_deferred = _titles_on and not title_first

    def _compute_title():
        """يحسب العنوان ويسجّله في الأرشيف/الجلسة. يعيد العنوان أو None. لا يرفع استثناء أبداً."""
        try:
            t = _voice_title(raw, title_fn)
        except Exception as e:      # التسمية تحسين لا شرط — لا تُسقط التسليم أبداً
            print(f"⚠️ تعذر حساب عنوان الفويس: {type(e).__name__}: {e}")
            return None
        if not t:
            return None
        try:
            archive.append_title(t, entry_id=entry_id)
            if session_name:
                sessions.append_title(t, session_id=session_id, entry_id=entry_id)
        except Exception as e:
            print(f"⚠️ تعذر تسجيل عنوان الفويس: {type(e).__name__}: {e}")
        return t

    if title_first:
        result["title"] = _compute_title()   # وضع التوافق: العنوان قبل الخام (يكلّف نداء lite)

    def _attach_title():
        """يحسب العنوان بعد إرسال الخام ويُلحقه بالأرشيف/الجلسة والترويسة إن أمكن."""
        if not title_deferred:
            return
        result["title"] = _compute_title()

    # 3) الخام — يُرسل فوراً بلا انتظار أي نداء شبكي إضافي
    if mode in ("dual", "raw_only"):
        _raw_title = None if title_deferred else result["title"]
        result["raw_msg_id"] = _send_long(sender, raw_message_html(raw, footer_html, _raw_title, session_name),
                                          raw, reply_to_id,
                                          filename_prefix="transcript", caption="📄 التفريغ الخام (طويل → ملف)",
                                          head_html=_entry_head_html(_raw_title, session_name))
        if mode == "raw_only" and getattr(sender, "set_reaction", None):
            try:
                sender.set_reaction(Config.REACTION_DONE)
            except Exception:
                pass

    # 3a) العنوان صار بعد إرسال الخام — نداء lite واحد هنا، لا يعطّل وصول النص للمستخدم.
    _attach_title()

    # 3b) اقتراح اسم الجلسة (08 §2): أول فويس في جلسة باسمها المؤقت → اقتراح معلّق (قبول صامت عند الفويس التالي)
    if archive_entry and session_name:
        try:
            if sessions.needs_title_suggestion():
                # توفير: لو حصلنا على عنوان الفويس للتو نعيد استخدامه اقتراحاً للجلسة (نداء lite واحد لا اثنان)
                gen = (lambda sys_t, usr_t: (result["title"], None)) if result["title"] else None
                result["title_suggestion"] = sessions.propose_title(raw, generate_fn=gen)
        except Exception as e:  # pragma: no cover
            print(f"⚠️ اقتراح اسم الجلسة: {e}")

    # 4) المنظم
    if mode in ("dual", "polished_only"):
        if getattr(sender, "send_action", None):
            try:
                sender.send_action("typing")
            except Exception:
                pass
        if getattr(sender, "set_reaction", None):
            try:
                sender.set_reaction(Config.REACTION_POLISHING)
            except Exception:
                pass

        cmp_mode = settings.compare_mode()
        has_local = bool(getattr(Config, "LOCAL_POLISH_CMD", "") or local_polish_fn)
        is_compare = (not disable_compare) and (cmp_mode in ("synthesizer", "merged", "split")) and has_local

        if polish_fn is None:
            import text_assist
            polish_fn = text_assist.polish_gemini if is_compare else text_assist.polish
        # L2 (10 §2): ملخص الجلسة التراكمي → طبقة B فقط (لا يُمرَّر لطبقة A إطلاقاً)
        l2 = sessions.summary() if session_name else None
        t0_flow = time.monotonic()
        t0_gemini = t0_flow
        try:
            if l2 and _accepts_context_summary(polish_fn):
                polished, reason = polish_fn(raw, role_id, gl, context_summary=l2)
            else:
                polished, reason = polish_fn(raw, role_id, gl)
        except Exception as e:  # pragma: no cover - دفاع: التنظيم لا يكسر الخام أبداً
            polished, reason = None, f"{type(e).__name__}: {e}"
        gemini_elapsed = time.monotonic() - t0_gemini
        anchor = result["raw_msg_id"] or reply_to_id
        _dwrite_lock = threading.Lock()
        _dwrite_done = {"v": False}

        def _bg_finalize(approved_text, archive_text, session_text=None, is_golden=False, synth_elapsed=None, record_job=False):
            if approved_text is not None:
                with res_lock:
                    result["polished"] = approved_text
                    if job_id and (is_golden or record_job):
                        import jobs
                        extra_fields = dict(job_meta or {})
                        extra_fields.pop("polished", None)
                        extra_fields.pop("polished_text", None)
                        extra_fields.pop("draft", None)
                        if "text" in extra_fields and extra_fields["text"] is not None:
                            extra_fields["text"] = str(extra_fields["text"])[:16000]
                        if is_golden:
                            extra_fields["synth_golden"] = True
                            if synth_elapsed is not None:
                                extra_fields["synth_elapsed"] = round(float(synth_elapsed), 2)
                        jobs.record(
                            job_id,
                            "sent",
                            polished=True,
                            polished_text=str(approved_text)[:16000],
                            **extra_fields,
                        )
                        result["_synth_recorded"] = True
            if archive_entry and archive_text:
                with _dwrite_lock:
                    if not _dwrite_done["v"]:
                        _dwrite_done["v"] = True
                        try:
                            archive.append_polished(archive_text, entry_id=entry_id)
                            if session_name:
                                sessions.append_polished(
                                    session_text if session_text is not None else archive_text,
                                    session_id=session_id,
                                    entry_id=entry_id,
                                )
                        except Exception as e:
                            print(f"⚠️ Archive/session polished update: {e}")
            if on_polished is not None and approved_text is not None:
                try:
                    on_polished(approved_text, is_golden=is_golden, synth_elapsed=synth_elapsed)
                except TypeError:
                    try:
                        on_polished(approved_text)
                    except Exception as e:
                        print(f"⚠️ on_polished callback error: {e}")
                except Exception as e:
                    print(f"⚠️ on_polished callback error: {e}")

        if not polished and is_compare:
            result["compare_thread"] = _deliver_shelby_rescue(
                raw, model, role_id, gl, l2, reason, mode, footer_html, session_name, session_id,
                entry_id, archive_entry, sender, reply_to_id, anchor, local_polish_fn,
                job_id, job_meta, res_lock, result, _dwrite_lock, _dwrite_done, _bg_finalize,
            )
        elif polished:
            result["polished"] = polished
            if getattr(sender, "set_reaction", None):
                try:
                    sender.set_reaction(Config.REACTION_DONE)
                except Exception:
                    pass
            defer_archive = bool(is_compare and cmp_mode == "synthesizer")
            if archive_entry and not defer_archive:
                archive.append_polished(polished, entry_id=entry_id)
                if session_name:
                    sessions.append_polished(polished, session_id=session_id, entry_id=entry_id)

            if is_compare:
                if cmp_mode == "synthesizer":
                    result["compare_thread"] = _deliver_synthesizer(
                        raw, polished, role_id, gl, l2, session_name, sender, anchor, local_polish_fn,
                        t0_flow, gemini_elapsed, res_lock, result, _bg_finalize,
                    )
                elif cmp_mode == "merged":
                    result["compare_thread"] = _deliver_merged(
                        raw, polished, role_id, gl, l2, session_name, sender, anchor, local_polish_fn,
                        res_lock, result, on_polished,
                    )
                elif cmp_mode == "split":
                    result["compare_thread"] = _deliver_split(
                        raw, polished, role_id, gl, l2, session_name, sender, anchor, local_polish_fn,
                        result, on_polished,
                    )
            else:
                result["polished_msg_id"] = _send_long(
                    sender, polished_message_html(polished, role_id, session_name, title=result["title"]),
                    polished, anchor, filename_prefix="polished",
                    caption="📝 النسخة المنظمة (طويلة → ملف)",
                    head_html=_entry_head_html(result["title"], session_name),
                )
        else:
            redacted_reason = logkit.redact(reason) if reason else None
            result["polish_error"] = redacted_reason
            if getattr(sender, "set_reaction", None):
                try:
                    sender.set_reaction(Config.REACTION_DONE)
                except Exception:
                    pass
            if mode == "polished_only":
                # لا خام أُرسل: نرسل الخام كبديل حتى لا يضيع الفويس (07 §2.2 روح القرار: لا رفض)
                result["raw_msg_id"] = _send_long(sender, raw_message_html(raw, footer_html), raw, reply_to_id,
                                                  filename_prefix="transcript", caption="📄 التفريغ الخام")
                sender.send_text(f"⚠️ تعذر التنظيم الآن ({html.escape(str(redacted_reason or ''))}) — أرسلت الخام بدلاً منه.",
                                 result["raw_msg_id"] or reply_to_id, "HTML")
            else:
                sender.send_text(f"⚠️ تعذر التنظيم الآن ({html.escape(str(redacted_reason or ''))}) — الخام فوق.", anchor, "HTML")

    # 5) L2 (10 §2): كل SESSION_SUMMARY_EVERY فويسات → تحديث ملخص الجلسة (نداء lite نصي واحد) — للتنظيم القادم فقط
    if archive_entry and session_name:
        try:
            result["summary_updated"] = bool(sessions.maybe_update_summary())
        except Exception as e:  # pragma: no cover
            print(f"⚠️ ملخص الجلسة L2: {e}")
    return result


def _accepts_context_summary(fn):
    """هل تقبل دالة التنظيم وسيط context_summary؟ (polish_fn المحقونة في المسار المكرر/الاختبارات قد لا تقبله)."""
    try:
        import inspect
        params = inspect.signature(fn).parameters
        return "context_summary" in params or any(p.kind == p.VAR_KEYWORD for p in params.values())
    except (TypeError, ValueError):
        return False


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_plain(text):
    """تحويل HTML إلى نص عادي مقروء للاستخدام كبديل عند رفض parse_mode='HTML'."""
    return html.unescape(_HTML_TAG_RE.sub("", text or ""))


def _try_edit(sender, mid, text, parse_mode, label=""):
    if mid is None or getattr(sender, "edit_text", None) is None:
        return False
    try:
        res = sender.edit_text(mid, text, parse_mode)
        return bool(res)
    except Exception as e:
        print(f"⚠️ Edit text exception ({label}): {e}")
        return False


def _replace_or_send(sender, mid, anchor, full_html, plain_text, filename_prefix, caption,
                     head_html="", close_notice="✅ <i>اكتملت المعالجة — تم إرسال النتيجة في ملف أدناه.</i>",
                     max_edit_len=None):
    """
    محرك التسليم الموحد رباعي الطبقات (P0-01):
    - ضمن حد التعديل (3950): يعدّل الرسالة في مكانها (HTML ثم Plain بدون HTML ثم إغلاق قصير)،
      وإن فشلت كل محاولات التعديل يرسل النتيجة عبر _send_long ويحذف رسالة الانتظار المعلقة إن توفّر delete_text.
    - فوق حد التعديل: يغلق رسالة الانتظار بإشعار قصير (HTML ثم Plain) ثم يرسل النتيجة كملف .md (أو نص)،
      وإن فشل إغلاقها يرسل النتيجة أولاً ثم يحذف رسالة الانتظار المعلقة.
    """
    if max_edit_len is None:
        max_edit_len = Config.TELEGRAM_MAX_MSG - 50
    can_edit = mid is not None and getattr(sender, "edit_text", None) is not None
    can_delete = mid is not None and getattr(sender, "delete_text", None) is not None
    has_doc = getattr(sender, "send_document", None) is not None
    oversized = len(full_html) > max_edit_len

    if not oversized:
        if can_edit and _try_edit(sender, mid, full_html, "HTML", filename_prefix):
            return mid
        plain_full = _html_to_plain(full_html)
        if can_edit and len(plain_full) <= max_edit_len and _try_edit(sender, mid, plain_full, None, f"{filename_prefix}:plain"):
            return mid
        notice_html = "✅ <i>اكتملت المعالجة — تم إرسال النتيجة أدناه.</i>"
        notice_plain = _html_to_plain(notice_html)
    else:
        notice_html = close_notice if has_doc else "✅ <i>اكتملت المعالجة — تم إرسال النتيجة أدناه.</i>"
        notice_plain = _html_to_plain(notice_html)

    mid_closed = False
    if can_edit:
        mid_closed = (
            _try_edit(sender, mid, notice_html, "HTML", f"{filename_prefix}:close")
            or _try_edit(sender, mid, notice_plain, None, f"{filename_prefix}:close-plain")
        )

    should_delete_waiting = (mid is not None and can_delete and not mid_closed)
    target_reply = anchor if should_delete_waiting else (mid or anchor)

    sent_mid = _send_long(
        sender,
        full_html,
        plain_text,
        target_reply,
        filename_prefix=filename_prefix,
        caption=caption,
        head_html=head_html,
        force_document=(oversized and has_doc),
    )

    if should_delete_waiting and sent_mid is not None:
        try:
            sender.delete_text(mid)
        except Exception as e:
            print(f"⚠️ Delete waiting message exception ({filename_prefix}): {e}")

    return sent_mid


def _send_long(sender, html_text, plain_text, reply_to_id, filename_prefix, caption, head_html="", force_document=False):
    """نص > POLISH_FILE_THRESHOLD (أو force_document=True) → ملف .md + معاينة (تحمل الترويسة 🏷️/📂 إن وُجدت — 08 §3)؛
    عند تعذر رفع الملف يحذف المعاينة المبتورة ويتراجع لإرسال النص الكامل مقسّماً عبر send_text."""
    has_doc = getattr(sender, "send_document", None) is not None
    if (force_document or len(plain_text) > Config.POLISH_FILE_THRESHOLD) and has_doc:
        preview = plain_text[:500] + ("…" if len(plain_text) > 500 else "")
        head = (head_html + "\n") if head_html else ""
        mid = sender.send_text(f"{head}<pre>{html.escape(preview)}</pre>\n{caption}", reply_to_id, "HTML")
        doc_ok = False
        dres = None
        try:
            dres = sender.send_document(
                f"{filename_prefix}.md", plain_text.encode("utf-8"), "text/markdown", caption, mid or reply_to_id
            )
            if hasattr(dres, "status_code"):
                doc_ok = (dres.status_code == 200)
            else:
                doc_ok = (dres is not False)
        except Exception as e:  # pragma: no cover
            print(f"⚠️ إرسال الملف فشل: {e}")
            doc_ok = False
        if doc_ok:
            if mid is not None:
                return mid
            if isinstance(dres, int) and not isinstance(dres, bool):
                return dres
            if hasattr(dres, "json"):
                try:
                    return dres.json().get("result", {}).get("message_id")
                except Exception:
                    return None
            return None
        if mid is not None and getattr(sender, "delete_text", None) is not None:
            try:
                sender.delete_text(mid)
            except Exception:
                pass
        return sender.send_text(html_text, reply_to_id, "HTML")
    return sender.send_text(html_text, reply_to_id, "HTML")
