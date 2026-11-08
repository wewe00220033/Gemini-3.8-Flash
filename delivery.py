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
import threading
import time

from config import Config
import archive
import settings
import prompts
import glossary as glossary_mod
import sessions
import logkit


class Sender:
    """واجهة الإرسال القابلة للحقن: send_text(text, reply_to, parse_mode) -> message_id|None ؛ send_document(...) ؛ send_action(...) ؛ set_reaction(...) ؛ edit_text(...)"""

    def __init__(self, send_text, send_document=None, send_action=None, set_reaction=None, edit_text=None):
        self.send_text = send_text
        self.send_document = send_document
        self.send_action = send_action
        self.set_reaction = set_reaction
        self.edit_text = edit_text


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


def merged_compare_message_html(gemini_text, shelby_text=None, role_id=None, session_name=None, shelby_error=None):
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

    if shelby_status == "waiting":
        blocks.append("⏳ <i>جاري توليد النسخة الذهبية المسبوكة مع شلبي (مهلة 180ث)...</i>")
    elif shelby_status == "timeout":
        blocks.append("⚠️ <i>تم اعتماد مسودة جيميني (تجاوز شلبي المهلة المحددة 180ث)</i>")
    elif shelby_status == "error":
        err_msg = html.escape(str(error_detail or "خطأ غير معروف"))
        blocks.append(f"⚠️ <i>تم اعتماد مسودة جيميني (تعذر شلبي: {err_msg})</i>")

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
                                  session_name=session_name, polished=polished, extra="🌐 ويب")
    except Exception as e:
        print(f"⚠️ الأرشفة (ويب): {e}")
        archived = False
    if session_name:
        sessions.append_entry(raw, model=model, elapsed=info.get("elapsed"), key_name=key_name, extra="🌐 ويب")
        if polished:
            sessions.append_polished(polished)
    return raw, archived, polished, {"role": role_id, "session": session_name, "polish_error": polish_error, "source": "web"}


def _safe_session_name():
    try:
        return sessions.current_name()
    except Exception as e:  # pragma: no cover
        print(f"⚠️ الجلسات: {e}")
        return None


def _voice_title(raw, title_fn):
    """عنوان ذكي للفويس (08 §3) — نصي فقط، بعد إرسال الخام لا قبله. أي فشل → None."""
    try:
        fn = title_fn if title_fn is not None else sessions.voice_title
        return fn(raw)
    except Exception as e:  # pragma: no cover
        print(f"⚠️ التسمية: {e}")
        return None


def deliver(raw_text, model, key_name, info, sender, reply_to_id=None, polish_fn=None, glossary=None,
            session_name=None, footer_html="", archive_entry=True, title_fn=None, local_polish_fn=None):
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
    result = {"raw": raw, "polished": None, "raw_msg_id": None, "polished_msg_id": None, "mode": mode,
              "archived": False, "polish_error": None, "role": role_id, "session": session_name, "title": None,
              "session_written": False, "title_suggestion": None, "summary_updated": False}

    # 2) أرشفة قبل أي إرسال (outbox) — archive_entry=False للمكرر (dedup): سبق أرشفته
    if archive_entry:
        result["archived"] = archive.append(raw, model=model, elapsed=(info or {}).get("elapsed"), key_name=key_name,
                                            session_name=session_name)
        if session_name:
            result["session_written"] = sessions.append_entry(raw, model=model, elapsed=(info or {}).get("elapsed"),
                                                              key_name=key_name)

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
            archive.append_title(t)
            if session_name:
                sessions.append_title(t)
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
        is_compare = (cmp_mode in ("synthesizer", "merged", "split")) and has_local

        if polish_fn is None:
            import text_assist
            polish_fn = text_assist.polish_gemini if is_compare else text_assist.polish
        # L2 (10 §2): ملخص الجلسة التراكمي → طبقة B فقط (لا يُمرَّر لطبقة A إطلاقاً)
        l2 = sessions.summary() if session_name else None
        t0_flow = time.time()
        t0_gemini = t0_flow
        try:
            if l2 and _accepts_context_summary(polish_fn):
                polished, reason = polish_fn(raw, role_id, gl, context_summary=l2)
            else:
                polished, reason = polish_fn(raw, role_id, gl)
        except Exception as e:  # pragma: no cover - دفاع: التنظيم لا يكسر الخام أبداً
            polished, reason = None, f"{type(e).__name__}: {e}"
        gemini_elapsed = time.time() - t0_gemini
        anchor = result["raw_msg_id"] or reply_to_id
        if not polished and is_compare:
            # 🛡️ صمام الأمان الفولاذي: إذا تعذر جيميني السحابي (503 / 429 / مهلة)،
            # لا نستسلم ولا نوقف شلبي أبداً! نستدعي شلبي المحلي فوراً كمنقذ وبديل في نفس الرسالة.
            rescue_msg = f"⏳ <i>تعذر منظم جيميني السحابي ({html.escape(str(reason or '503'))}) — جاري تنظيم النص عبر شلبي محلياً...</i>"
            mid = sender.send_text(rescue_msg, anchor, "HTML")
            result["polished_msg_id"] = mid

            def _bg_shelby_rescue():
                try:
                    s_fn = local_polish_fn
                    if s_fn is None:
                        import text_assist
                        s_fn = text_assist.polish_local
                    s_kwargs = {"context_summary": l2} if _accepts_context_summary(s_fn) else {}
                    s_text, s_err = s_fn(raw, role_id, gl, **s_kwargs)
                    if s_text:
                        result["polished"] = s_text
                        if getattr(sender, "set_reaction", None):
                            try:
                                sender.set_reaction(Config.REACTION_DONE)
                            except Exception:
                                pass
                        if archive_entry:
                            archive.append_polished(s_text)
                            if session_name:
                                sessions.append_polished(s_text)
                        s_html = polished_message_html(
                            s_text, role_id, session_name,
                            provider_tag="🦙 <b>[شلبي]</b>", title=result["title"]
                        )
                        edited = False
                        if mid is not None and len(s_html) <= Config.TELEGRAM_MAX_MSG - 50 and getattr(sender, "edit_text", None):
                            try:
                                res = sender.edit_text(mid, s_html, "HTML")
                                edited = (res is not False)
                            except Exception:
                                edited = False
                        if not edited:
                            _send_long(sender, s_html, s_text, mid or anchor, filename_prefix="shelby_polished",
                                       caption="🦙 نسخة شلبي المنظمة (بديل لتعذر جيميني السحابي)",
                                       head_html=_entry_head_html(result["title"], session_name))
                    else:
                        err_html = f"⚠️ تعذر التنظيم عبر جيميني وشلبي ({html.escape(str(s_err or reason or ''))}) — الخام فوق."
                        if mid is not None and getattr(sender, "edit_text", None):
                            try:
                                res = sender.edit_text(mid, err_html, "HTML")
                                if res is False:
                                    sender.send_text(err_html, anchor, "HTML")
                            except Exception:
                                sender.send_text(err_html, anchor, "HTML")
                        else:
                            sender.send_text(err_html, anchor, "HTML")
                except Exception as e:
                    print(f"⚠️ Shelby rescue error: {e}")

            t = threading.Thread(target=_bg_shelby_rescue, daemon=True)
            t.start()
            result["compare_thread"] = t

        elif polished:
            result["polished"] = polished
            if getattr(sender, "set_reaction", None):
                try:
                    sender.set_reaction(Config.REACTION_DONE)
                except Exception:
                    pass
            if archive_entry:
                archive.append_polished(polished)
                if session_name:
                    sessions.append_polished(polished)

            if is_compare:
                if cmp_mode == "synthesizer":
                    init_html = synthesizer_interim_message_html(
                        polished, role_id=role_id, session_name=session_name,
                        title=result["title"], shelby_status="waiting"
                    )
                    mid = _send_long(sender, init_html, polished, anchor, filename_prefix="polished",
                                     caption="📝 النسخة المنظمة (طويلة → ملف)",
                                     head_html=_entry_head_html(result["title"], session_name))
                    result["polished_msg_id"] = mid

                    def _bg_synthesizer():
                        try:
                            t0_shelby = time.time()
                            s_fn = local_polish_fn
                            if s_fn is None:
                                import text_assist
                                s_fn = text_assist.polish_local
                            s_kwargs = {"context_summary": l2} if _accepts_context_summary(s_fn) else {}
                            s_text, s_err = s_fn(raw, role_id, gl, **s_kwargs)
                            shelby_elapsed = time.time() - t0_shelby

                            if s_text:
                                t0_synth = time.time()
                                import text_assist
                                synth_kwargs = {"context_summary": l2} if _accepts_context_summary(text_assist.synthesize) else {}
                                golden_text, synth_err = text_assist.synthesize(
                                    raw, polished, s_text, role_id=role_id, glossary=gl, **synth_kwargs
                                )
                                synth_elapsed = time.time() - t0_synth

                                if golden_text:
                                    result["polished"] = golden_text
                                    if archive_entry:
                                        try:
                                            archive.append_polished(f"[نسخة ذهبية مسبوكة]\n{golden_text}")
                                            if session_name:
                                                sessions.append_polished(golden_text)
                                        except Exception as e:
                                            print(f"⚠️ Synth archive update: {e}")

                                    wall_total = time.time() - t0_flow
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
                                    edited = False
                                    if mid is not None and len(full_html) <= Config.TELEGRAM_MAX_MSG - 50 and getattr(sender, "edit_text", None):
                                        try:
                                            res = sender.edit_text(mid, full_html, "HTML")
                                            edited = (res is not False)
                                        except Exception as e:
                                            print(f"⚠️ Edit text exception: {e}")
                                            edited = False
                                    if not edited:
                                        _send_long(
                                            sender, full_html, golden_text, mid or anchor,
                                            filename_prefix="golden_synthesized",
                                            caption="🏆 النسخة الذهبية المسبوكة",
                                            head_html=_entry_head_html(result["title"], session_name)
                                        )
                                else:
                                    fallback_html = merged_compare_message_html(
                                        polished, shelby_text=s_text, role_id=role_id, session_name=session_name
                                    )
                                    edited = False
                                    if mid is not None and len(fallback_html) <= Config.TELEGRAM_MAX_MSG - 50 and getattr(sender, "edit_text", None):
                                        try:
                                            res = sender.edit_text(mid, fallback_html, "HTML")
                                            edited = (res is not False)
                                        except Exception as e:
                                            print(f"⚠️ Fallback edit exception: {e}")
                                            edited = False
                                    if not edited:
                                        _send_long(
                                            sender, fallback_html, f"{polished}\n\n{s_text}", mid or anchor,
                                            filename_prefix="fallback_compare",
                                            caption="📝 المقارنة المدمجة (بديل لفشل السبك)",
                                            head_html=_entry_head_html(result["title"], session_name)
                                        )
                            else:
                                is_to = "مهلة" in str(s_err or "") or "timeout" in str(s_err or "").lower()
                                status = "timeout" if is_to else "error"
                                to_html = synthesizer_interim_message_html(
                                    polished, role_id=role_id, session_name=session_name,
                                    title=result["title"], shelby_status=status, error_detail=s_err
                                )
                                edited = False
                                if mid is not None and len(to_html) <= Config.TELEGRAM_MAX_MSG - 50 and getattr(sender, "edit_text", None):
                                    try:
                                        res = sender.edit_text(mid, to_html, "HTML")
                                        edited = (res is not False)
                                    except Exception:
                                        edited = False
                                if not edited and mid is None:
                                    sender.send_text(to_html, anchor, "HTML")
                        except Exception as e:
                            print(f"⚠️ Synthesizer bg error: {e}")

                    t = threading.Thread(target=_bg_synthesizer, daemon=True)
                    t.start()
                    result["compare_thread"] = t

                elif cmp_mode == "merged":
                    init_html = merged_compare_message_html(polished, shelby_text=None, role_id=role_id, session_name=session_name)
                    mid = _send_long(sender, init_html, polished, anchor, filename_prefix="polished",
                                     caption="📝 النسخة المنظمة (طويلة → ملف)",
                                     head_html=_entry_head_html(result["title"], session_name))
                    result["polished_msg_id"] = mid

                    def _bg_shelby():
                        try:
                            s_fn = local_polish_fn
                            if s_fn is None:
                                import text_assist
                                s_fn = text_assist.polish_local
                            s_kwargs = {"context_summary": l2} if _accepts_context_summary(s_fn) else {}
                            s_text, s_err = s_fn(raw, role_id, gl, **s_kwargs)
                            if s_text:
                                full_html = merged_compare_message_html(polished, shelby_text=s_text, role_id=role_id, session_name=session_name)
                                if len(full_html) <= Config.TELEGRAM_MAX_MSG - 50 and getattr(sender, "edit_text", None):
                                    sender.edit_text(mid, full_html, "HTML")
                                else:
                                    s_single = polished_message_html(s_text, role_id, session_name, provider_tag="🦙 <b>[شلبي]</b>", title=result["title"])
                                    _send_long(sender, s_single, s_text, mid or anchor, filename_prefix="shelby_polished", caption="🦙 نسخة شلبي المنظمة")
                            elif s_err and getattr(sender, "edit_text", None):
                                err_html = merged_compare_message_html(polished, shelby_text=None, role_id=role_id, session_name=session_name, shelby_error=s_err)
                                sender.edit_text(mid, err_html, "HTML")
                        except Exception as e:
                            print(f"⚠️ Dual polish bg error: {e}")

                    t = threading.Thread(target=_bg_shelby, daemon=True)
                    t.start()
                    result["compare_thread"] = t

                elif cmp_mode == "split":
                    gemini_html = polished_message_html(polished, role_id, session_name, provider_tag="🤖 <b>[جيميني]</b>", title=result["title"])
                    mid = _send_long(sender, gemini_html, polished, anchor, filename_prefix="gemini_polished",
                                     caption="🤖 نسخة جيميني المنظمة (طويلة → ملف)",
                                     head_html=_entry_head_html(result["title"], session_name))
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
                                s_html = polished_message_html(s_text, role_id, session_name, provider_tag="🦙 <b>[شلبي]</b>", title=result["title"])
                                _send_long(sender, s_html, s_text, mid or anchor, filename_prefix="shelby_polished", caption="🦙 نسخة شلبي المنظمة")
                            elif s_err:
                                sender.send_text(f"⚠️ تعذر تشغيل شلبي محلياً ({html.escape(str(s_err))})", mid or anchor, "HTML")
                        except Exception as e:
                            print(f"⚠️ Dual polish split bg error: {e}")

                    t = threading.Thread(target=_bg_shelby_split, daemon=True)
                    t.start()
                    result["compare_thread"] = t
            else:
                result["polished_msg_id"] = _send_long(sender, polished_message_html(polished, role_id, session_name, title=result["title"]),
                                                       polished, anchor, filename_prefix="polished",
                                                       caption="📝 النسخة المنظمة (طويلة → ملف)",
                                                       head_html=_entry_head_html(result["title"], session_name))
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
                                 result["raw_msg_id"], "HTML")
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


def _send_long(sender, html_text, plain_text, reply_to_id, filename_prefix, caption, head_html=""):
    """نص > POLISH_FILE_THRESHOLD → ملف .md + معاينة (تحمل الترويسة 🏷️/📂 إن وُجدت — 08 §3)؛ وإلا رسالة HTML عادية.
    يرجع message_id (أو None)."""
    if len(plain_text) > Config.POLISH_FILE_THRESHOLD and sender.send_document is not None:
        preview = plain_text[:500] + ("…" if len(plain_text) > 500 else "")
        head = (head_html + "\n") if head_html else ""
        mid = sender.send_text(f"{head}<pre>{html.escape(preview)}</pre>\n{caption}", reply_to_id, "HTML")
        try:
            sender.send_document(f"{filename_prefix}.md", plain_text.encode("utf-8"), "text/markdown", caption, mid or reply_to_id)
        except Exception as e:  # pragma: no cover
            print(f"⚠️ إرسال الملف فشل: {e}")
        return mid
    return sender.send_text(html_text, reply_to_id, "HTML")
