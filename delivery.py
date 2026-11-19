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
import logging
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

logger = logging.getLogger("delivery")


MAX_FINAL_CHUNKS = 8


def _utf16_len(text):
    """حساب طول النص بوحدات UTF-16 Code Units لمطابقة معايير تليجرام الصارمة (4096)."""
    return len(str(text or "").encode("utf-16-le")) // 2


class Sender:
    """واجهة الإرسال القابلة للحقن: send_text(text, reply_to, parse_mode, reply_markup=None) -> message_id|None ؛ send_document(...) ؛ send_action(...) ؛ set_reaction(...) ؛ edit_text(...) ؛ delete_text(...)"""

    def __init__(self, send_text, send_document=None, send_action=None, set_reaction=None, edit_text=None, delete_text=None):
        def _send(text, reply_to=None, parse_mode=None, reply_markup=None, **kwargs):
            if send_text is None:
                return None
            try:
                return send_text(text, reply_to, parse_mode, reply_markup=reply_markup, **kwargs)
            except TypeError:
                try:
                    return send_text(text, reply_to, parse_mode, reply_markup)
                except TypeError:
                    return send_text(text, reply_to, parse_mode)

        self.send_text = _send
        self.send_document = send_document
        self.send_action = send_action
        self.set_reaction = set_reaction

        if edit_text is not None:
            def _edit(message_id, text, parse_mode="HTML", reply_markup=None, **kwargs):
                try:
                    return edit_text(message_id, text, parse_mode, reply_markup=reply_markup, **kwargs)
                except TypeError:
                    try:
                        return edit_text(message_id, text, parse_mode, reply_markup)
                    except TypeError:
                        return edit_text(message_id, text, parse_mode)
            self.edit_text = _edit
        else:
            self.edit_text = None

        self.delete_text = delete_text


class RateLimitError(Exception):
    """يُرفع عند استقبال HTTP 429 من تيليجرام مع زمن الانتظار الموصى به (T4 — DEC-P2-09)."""

    def __init__(self, retry_after=5.0, message=None):
        self.retry_after = max(0.5, float(retry_after or 5.0))
        super().__init__(message or f"Telegram rate limit: retry after {self.retry_after:.1f}s")


class LiveMessageController:
    """
    منسق الرسالة الحية الفردية (Single-Writer) لمسار السبك (Synthesizer).
    - يضمن رسالة تليجرام حية واحدة تُعدل في مكانها عبر قفل تسلسلي وحيد (_writer_lock).
    - يمنع أي تحديثات وسيطة بعد الاعتماد النهائي (finalized=True).
    - يدير الشبكة الوسيطة بدون إعادة محاولة عمياء لـ 429 (يلتقط RateLimitError ويكبح التحديثات الوسيطة).
    - يفرض فاصلاً زمنياً (1.5-2.0ث) بين طلبات editMessageText لمنع 429 Too Many Requests.
    - يقسم المخرجات النهائية الطويلة (> 4000 UTF-16) وفق إعدادات display وحفظ المسودات آلياً.
    """
    def __init__(self, sender, anchor, title=None, session_name=None, role_id=None, min_edit_interval=1.5,
                 chat_id=None, user_id=None, entry_id=None):
        self.sender = sender
        self.anchor = anchor
        self.title = title
        self.session_name = session_name
        self.role_id = role_id
        self.min_edit_interval = min_edit_interval
        self.chat_id = chat_id
        self.user_id = user_id
        self.entry_id = entry_id
        self._writer_lock = threading.Lock()
        self.live_mid = None
        self.finalized = False
        self._last_edit_time = 0.0
        self._rate_limited_until = 0.0

    def start_live(self, initial_html):
        """إرسال الرسالة الحية الأولى في الشات وحجز live_mid."""
        with self._writer_lock:
            if self.live_mid is not None or self.finalized:
                return self.live_mid
            try:
                self.live_mid = self.sender.send_text(initial_html, self.anchor, "HTML")
            except Exception as e:
                logger.warning("⚠️ فشل فتح الرسالة الحية: %s", e)
                self.live_mid = None
            self._last_edit_time = time.monotonic()
            return self.live_mid

    def update_interim(self, html_text, force=False):
        """تحديث الرسالة الحية في مكانها بنبضات الحالة الوسيطة (فلاش جاهز / شلبي قيد العمل)."""
        with self._writer_lock:
            if self.finalized or self.live_mid is None:
                return False
            now = time.monotonic()
            if now < self._rate_limited_until:
                return False
            if not force and (now - self._last_edit_time) < self.min_edit_interval:
                return False
            if getattr(self.sender, "edit_text", None) is None:
                return False
            try:
                ok = self.sender.edit_text(self.live_mid, html_text, parse_mode="HTML", raise_429=True)
                if ok:
                    self._last_edit_time = time.monotonic()
                return bool(ok)
            except RateLimitError as e:
                self._rate_limited_until = time.monotonic() + e.retry_after
                logger.warning("⚠️ كبح التحديث الوسيط للحية بسبب 429 تليجرام (انتظار %.1fث)", e.retry_after)
                return False
            except Exception as e:
                msg = str(e).lower()
                if "429" in msg or "retry after" in msg:
                    m = re.search(r"retry(?:\s+after)?\s+(\d+(?:\.\d+)?)", msg)
                    delay = float(m.group(1)) if m else 5.0
                    self._rate_limited_until = time.monotonic() + delay
                    logger.warning("⚠️ كبح التحديث الوسيط للحية بعد استثناء 429 (انتظار %.1fث)", delay)
                else:
                    logger.debug("⚠️ فشل التحديث الوسيط للحية: %s", e)
                return False

    def finalize(self, golden_text, is_golden=True, telemetry=None, reply_markup=None,
                 gemini_text=None, shelby_text=None, synth_provider=None, drafts=None):
        """
        اعتماد وتسليم النتيجة النهائية:
        1. يقفل الحية نهائياً (finalized = True).
        2. لو <= 4000 UTF-16: يستبدل الرسالة الحية في مكانها (دون زر .md).
        3. لو > 4000 UTF-16: يجزئ كتل <pre> متوازنة؛ القطعة 0 تحل محل الحية،
           والقطع 1..N تُرسل كردود متتابعة تحتها، مع وضع الزر على القطعة الأخيرة فقط إذا كان show_md_button مفعلاً.
        4. يحفظ المسودة كاملة على القرص تلقائياً بعد التسليم.
        يرجع قائمة بمعرفات الرسائل المرسلة: [mid_0, mid_1, ...].
        """
        # قراءة إعدادات العرض خارج القفل لتفادي أي بطء في قراءة الإعدادات
        try:
            import pipeline_config
            display_cfg = pipeline_config.get().get("display", {})
        except Exception:
            display_cfg = {}
        show_md_btn = bool(display_cfg.get("show_md_button", True))
        max_chunks = int(display_cfg.get("max_chunks_per_output", MAX_FINAL_CHUNKS))
        silent_followups = bool(display_cfg.get("silent_followups", True))

        # لو كان مؤقت التراجع نشطاً: انتظار لمرة واحدة بحد أقصى 10 ثوانٍ خارج القفل (DEC-P2-04/09)
        now = time.monotonic()
        if self._rate_limited_until > now:
            rem = self._rate_limited_until - now
            if rem > 0:
                time.sleep(min(rem, 10.0))

        with self._writer_lock:
            self.finalized = True
            sent_mids = []

            if is_golden:
                full_html = synthesizer_final_message_html(
                    golden_text,
                    gemini_text=gemini_text,
                    shelby_text=shelby_text,
                    role_id=self.role_id,
                    session_name=self.session_name,
                    title=self.title,
                    telemetry=telemetry,
                    synth_provider=synth_provider,
                )
            else:
                full_html = golden_text

            parsed_utf16 = _utf16_len(_html_to_plain(full_html))
            raw_utf16 = _utf16_len(full_html)
            limit = 4000

            # ربط كاش .md التفاعلي وزر التصدير الأزرق (المرحلة 4 - Contract 8)
            # يظهر فقط للنتيجة المسبوكة الذهبية (> 4000 UTF-16) على القطعة الأخيرة وعند show_md_btn=True
            if is_golden and show_md_btn and (parsed_utf16 > limit or raw_utf16 > limit) and reply_markup is None:
                try:
                    import md_cache
                    md_text = golden_text
                    if gemini_text and shelby_text:
                        md_text = (
                            f"{golden_text}\n\n---\n"
                            f"## 📋 ملحق المسودات الأولية\n\n"
                            f"### ⚡ مسودة فلاش (جيميني)\n{gemini_text}\n\n"
                            f"### 🦙 مسودة شلبي (محلي)\n{shelby_text}\n"
                        )
                    token = md_cache.store(
                        md_text,
                        chat_id=self.chat_id,
                        user_id=self.user_id,
                        entry_id=self.entry_id,
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "📄 تحويل لملف .md",
                                    "callback_data": f"md:ask:{token}",
                                    "style": "primary",
                                }
                            ]
                        ]
                    }
                except Exception as e:
                    logger.warning("⚠️ md_cache store failed: %s", e)

            import telegram_bot
            html_chunks_fn = getattr(telegram_bot, "_html_chunks", None)

            can_edit = self.live_mid is not None and getattr(self.sender, "edit_text", None) is not None
            can_delete = self.live_mid is not None and getattr(self.sender, "delete_text", None) is not None

            def _try_edit_text(mid, text_body, parse_mode="HTML", markup=None):
                try:
                    return self.sender.edit_text(mid, text_body, parse_mode=parse_mode, reply_markup=markup, raise_429=True)
                except RateLimitError as rle:
                    self._rate_limited_until = time.monotonic() + rle.retry_after
                    logger.warning("⚠️ تعذر تعديل الحية بسبب 429 تليجرام (تراجع %.1fث واللجوء للإرسال الجديد)", rle.retry_after)
                    return False
                except Exception:
                    return False

            if (parsed_utf16 <= limit and raw_utf16 <= limit) or html_chunks_fn is None:
                if can_edit:
                    ok = _try_edit_text(self.live_mid, full_html, parse_mode="HTML")
                    if not ok:
                        plain_full = _html_to_plain(full_html)
                        if _utf16_len(plain_full) <= limit:
                            ok = _try_edit_text(self.live_mid, plain_full, parse_mode=None)
                    if ok:
                        sent_mids.append(self.live_mid)

                if not sent_mids:
                    mid = self.sender.send_text(full_html, self.anchor, "HTML")
                    if mid:
                        sent_mids.append(mid)
                        if can_delete:
                            try:
                                self.sender.delete_text(self.live_mid)
                            except Exception:
                                pass

            else:
                raw_chunks = list(html_chunks_fn(full_html, limit))
                total_chunks = len(raw_chunks)
                if max_chunks > 0 and total_chunks > max_chunks:
                    raw_chunks = raw_chunks[:max_chunks]
                    notice = f"\n\n<i>[...تم إرسال أول {max_chunks} أجزاء فقط لاستيفاء الحد الأقصى للشات. يمكنك طلب الملف كاملاً...]</i>"
                    raw_chunks[-1] = raw_chunks[-1] + notice
                    total_chunks = max_chunks

                chunk_0 = raw_chunks[0]
                first_ok = False
                first_markup = reply_markup if total_chunks == 1 else None
                if can_edit:
                    first_ok = _try_edit_text(self.live_mid, chunk_0, parse_mode="HTML", markup=first_markup)
                    if not first_ok:
                        plain_0 = _html_to_plain(chunk_0)
                        first_ok = _try_edit_text(self.live_mid, plain_0, parse_mode=None, markup=first_markup)
                    if first_ok:
                        sent_mids.append(self.live_mid)

                if not first_ok:
                    mid_0 = self.sender.send_text(chunk_0, self.anchor, "HTML", reply_markup=first_markup)
                    if mid_0:
                        sent_mids.append(mid_0)
                        if can_delete:
                            try:
                                self.sender.delete_text(self.live_mid)
                            except Exception:
                                pass
                    parent_anchor = mid_0 or self.anchor
                else:
                    parent_anchor = self.live_mid

                for i in range(1, total_chunks):
                    chunk_i = raw_chunks[i]
                    is_last = (i == total_chunks - 1)
                    markup = reply_markup if is_last else None
                    sub_mid = self.sender.send_text(
                        chunk_i,
                        parent_anchor,
                        "HTML",
                        reply_markup=markup,
                        disable_notification=silent_followups,
                    )
                    if sub_mid:
                        sent_mids.append(sub_mid)
                        parent_anchor = sub_mid

            # حفظ المسودات التلقائي على القرص بعد نجاح الإرسال والتسليم
            try:
                import drafts as drafts_mod
                effective_drafts = drafts
                if effective_drafts is None:
                    effective_drafts = []
                    if gemini_text:
                        effective_drafts.append(("فلاش", gemini_text))
                    if shelby_text:
                        effective_drafts.append(("شلبي", shelby_text))
                drafts_mod.write(
                    entry_id=self.entry_id,
                    final_text=golden_text if is_golden else None,
                    drafts=effective_drafts,
                    meta={"title": self.title, "session_name": self.session_name, "role_id": self.role_id}
                )
            except Exception as e:
                logger.warning("⚠️ فشل حفظ المسودة التلقائية: %s", e)

            return sent_mids


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


_pipeline_cancel_event = threading.Event()


def request_pipeline_cancel():
    """يُفعل حدث الإلغاء لجميع مراحل الـ Pipeline الجارية أثناء الإغلاق الرشيق (T6b)."""
    _pipeline_cancel_event.set()


def reset_pipeline_cancel():
    """للاختبارات: تصفير حدث الإلغاء للبدء من جديد."""
    _pipeline_cancel_event.clear()


def wait_background_threads(timeout=15.0):
    """ينتظر اكتمال خيوط التنظيم/السبك الخلفية حتى timeout ثانية (يرجع عدد المتبقي)."""
    request_pipeline_cancel()
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
    role_name = role.get("name") or role_id or "عام"
    head = f"<b>📝 النسخة المنظمة</b> · دور: {html.escape(str(role_name))}"
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


def send_output_message(sender, label, text, anchor, silent=True):
    """
    إرسال مسودة مخرج منفصلة كرد مباشر على الفويس (نمط per_output - DEC-P2-04/09).
    تُرسل داخل كتلة <pre> واحدة مع كتم الإشعار (silent=True) لتفادي إزعاج المستخدم.
    """
    if sender is None or text is None:
        return None
    header = f"⚡ <b>[{html.escape(str(label))}]:</b>" if label else ""
    body = f"<pre>{html.escape(str(text))}</pre>"
    msg = f"{header}\n{body}" if header else body
    try:
        return sender.send_text(msg, anchor, "HTML", disable_notification=silent)
    except Exception as e:
        logger.warning("⚠️ فشل إرسال مسودة per_output (%s): %s", label, e)
        return None


def _format_label(lbl):
    """تنسيق شارة المسودة التمهيدية في الرسالة الحية بنمط موحد (DEC-P2-04/09)."""
    if not lbl:
        return None
    s = str(lbl).strip()
    if not s:
        return None
    if "<b" in s or s.endswith(":"):
        return s
    if any(ord(c) >= 0x2600 for c in s):
        return f"<b>[{html.escape(s)}]:</b>"
    return f"⚡ <b>[{html.escape(s)}]:</b>"


def render_live_html(blocks, title=None, role_id=None, session_name=None, footer_status=None, live_cap=3400, header_prefix=None):
    """
    توليد نص HTML للرسالة الحية التفاعلية مع دعم كتل متعددة ديناميكياً (T5 — DEC-P2-04/09).
    - blocks: قائمة عناصر بصيغة (label, text) أو dict(label=..., text=...).
    - title / role_id / session_name: ترويسة الرسالة.
    - footer_status: سطر الحالة في أسفل الرسالة.
    - live_cap: السقف الأقصى لطول الرسالة بوحدات UTF-16 (الافتراضي 3400).
    - header_prefix: ترويسة مخصصة اختيارية (مثل '📝 المسودات الأولية (فلاش + شلبي)').
    """
    role = prompts.get_role(role_id) or {}
    role_name = role.get("name") or role_id or "عام"
    head_parts = []
    if title:
        head_parts.append(f"🏷️ <b>{html.escape(str(title))}</b>")

    parsed_blocks = []
    for b in (blocks or []):
        if isinstance(b, dict):
            lbl = b.get("label")
            txt = str(b.get("text") or "")
        elif isinstance(b, (list, tuple)):
            lbl = b[0] if len(b) > 0 else None
            txt = str(b[1] if len(b) > 1 else "")
        else:
            lbl = None
            txt = str(b or "")
        parsed_blocks.append((lbl, txt))

    if header_prefix:
        prefix_txt = header_prefix
    elif len(parsed_blocks) > 1:
        prefix_txt = "📝 المسودات الأولية"
    else:
        prefix_txt = "📝 النسخة المنظمة"

    head_line = f"<b>{prefix_txt}</b> · دور: {html.escape(str(role_name))}"
    if session_name:
        head_line += f" · جلسة: «{html.escape(str(session_name))}»"
    head_parts.append(head_line)
    header_str = "\n".join(head_parts)

    footer_str = str(footer_status) if footer_status else ""

    # مسار سريع إذا كان الحجم الإجمالي لا يتجاوز سقف العرض الحي (صفر اقتطاع وصفر تغيير)
    full_pieces = [header_str]
    for lbl, txt in parsed_blocks:
        lbl_rendered = _format_label(lbl)
        if lbl_rendered:
            full_pieces.append(lbl_rendered)
        full_pieces.append(f"<pre>{html.escape(txt)}</pre>")
    if footer_str:
        full_pieces.append(footer_str)
    full_candidate = "\n".join(full_pieces)

    if live_cap is None or live_cap <= 0 or _utf16_len(full_candidate) <= live_cap:
        return full_candidate

    # حساب الميزانية والاقتطاع الذكي إذا تجاوز السقف (تصحيحات المراجع 1/2/4)
    import telegram_bot

    tail = "\n[...يظهر كاملاً في السبك النهائي...]"
    tail_len = _utf16_len(tail)
    n_total = len(parsed_blocks)

    fixed_overhead = _utf16_len(header_str) + 1 + (_utf16_len(footer_str) + 1 if footer_str else 0)

    # حساب ميزانية كل كتلة: overhead_per_block = 11 (<pre></pre>) + tail_len + label_len + newlines
    def _block_overhead(lbl):
        lbl_r = _format_label(lbl)
        return 11 + tail_len + (_utf16_len(lbl_r) + 1 if lbl_r else 0) + 1

    total_overheads = fixed_overhead + sum(_block_overhead(lbl) for lbl, _ in parsed_blocks)
    avail = live_cap - total_overheads
    per_block_cap = avail // max(1, n_total)

    shown_blocks = parsed_blocks
    skip_note = None

    # تصحيح 4: لو تجاوز عدد الكتل 2 وكانت حصة الكتلة < 200 UTF-16: نعرض أول بلوك وآخر بلوك فقط
    if n_total > 2 and per_block_cap < 200:
        shown_blocks = [parsed_blocks[0], parsed_blocks[-1]]
        skipped_count = n_total - 2
        skip_note = f"<i>...و {skipped_count} مسودات بينهما...</i>"
        note_overhead = _utf16_len(skip_note) + 1
        new_overheads = fixed_overhead + note_overhead + sum(_block_overhead(lbl) for lbl, _ in shown_blocks)
        avail = live_cap - new_overheads
        if avail < 200:
            notice = f"<i>[...{n_total} مسودات جاهزة — تظهر في النهائي...]</i>"
            pieces = [header_str, notice]
            if footer_str:
                pieces.append(footer_str)
            return "\n".join(pieces)
        per_block_cap = avail // 2

    rendered_blocks = []
    for lbl, raw_text in shown_blocks:
        lbl_rendered = _format_label(lbl)
        # تصحيح 1: الهروب أولاً html.escape ثم القص على النص المهرب ثم _safe_cut لحماية الكيانات
        escaped = html.escape(raw_text)
        if _utf16_len(escaped) > per_block_cap:
            target_budget = max(50, per_block_cap - tail_len)
            prefix_idx = telegram_bot._utf16_prefix_idx(escaped, target_budget)
            safe_idx = telegram_bot._safe_cut(escaped, prefix_idx)
            # تصحيح 2: ذيل الاقتطاع نص عادي بدون أي وسم <i> داخل <pre>
            cut_escaped = escaped[:safe_idx] + tail
        else:
            cut_escaped = escaped

        block_pieces = []
        if lbl_rendered:
            block_pieces.append(lbl_rendered)
        block_pieces.append(f"<pre>{cut_escaped}</pre>")
        rendered_blocks.append("\n".join(block_pieces))

    out_pieces = [header_str]
    if skip_note and len(rendered_blocks) == 2:
        out_pieces.append(rendered_blocks[0])
        # تصحيح 2: سطر الملاحظة <i> مستقل تماماً خارج أي <pre>
        out_pieces.append(skip_note)
        out_pieces.append(rendered_blocks[1])
    else:
        out_pieces.extend(rendered_blocks)

    if footer_str:
        out_pieces.append(footer_str)

    return "\n".join(out_pieces)


def synthesizer_interim_message_html(gemini_text, shelby_text=None, role_id=None, session_name=None, title=None, shelby_status="waiting", error_detail=None):
    """
    الرسالة الحية أثناء تشغيل السبك (غلاف توافقي يستدعي render_live_html - DEC-P2-04/09):
    - المرحلة 1 (انتظار شلبي): تعرض مسودة فلاش (جيميني) داخل <pre>، مع مؤشر انتظار شلبي.
    - المرحلة 2 (وصول شلبي وبدء السبك): تعرض مسودة فلاش ومسودة شلبي معاً داخل <pre> لكل منهما مع صمام أمان لاقتطاع النص إن تجاوز 3400 UTF-16 لتفادي خطأ تليجرام 400.
    """
    if shelby_text:
        footer = "⏳ <b>جاري السبك الثلاثي لإنتاج النسخة الذهبية المعتمدة...</b>"
        blocks = [
            ("⚡ <b>[مسودة فلاش]:</b>", gemini_text),
            ("🦙 <b>[مسودة شلبي]:</b>", shelby_text),
        ]
        return render_live_html(
            blocks=blocks,
            title=title,
            role_id=role_id,
            session_name=session_name,
            footer_status=footer,
            header_prefix="📝 المسودات الأولية (فلاش + شلبي)",
        )

    t_label = _local_timeout_label()
    footer = None
    if shelby_status == "waiting":
        footer = f"⏳ <i>جاري توليد النسخة الذهبية المسبوكة مع شلبي (مهلة {t_label})...</i>"
    elif shelby_status == "timeout":
        footer = f"⚠️ <i>تم اعتماد مسودة جيميني (تجاوز شلبي المهلة المحددة {t_label})</i>"
    elif shelby_status == "error":
        err_msg = html.escape(str(error_detail or "خطأ غير معروف"))
        footer = f"⚠️ <i>تم اعتماد مسودة جيميني (تعذر شلبي: {err_msg})</i>"
    elif shelby_status == "noop":
        footer = "ℹ️ <i>تم اعتماد مسودة جيميني (أعاد شلبي النص دون تنظيم فعلي)</i>"

    blocks = [(None, gemini_text)]
    return render_live_html(
        blocks=blocks,
        title=title,
        role_id=role_id,
        session_name=session_name,
        footer_status=footer,
        live_cap=0,  # المرحلة 1 لا تقتطع لأن _deliver_synthesizer يفصل المسودة الطويلة لملف .md
        header_prefix="📝 النسخة المنظمة",
    )


def _format_icon_label(label: str, provider: str = "") -> str:
    """تنسيق وسم النداء أو المزود بالأيقونة التعبيرية المناسبة (DEC-P2-08/10)."""
    lbl = str(label or provider or "").strip()
    if any(ord(c) >= 0x2600 for c in lbl):
        return lbl
    p_lower = str(provider or "").lower()
    if "فلاش" in lbl or "flash" in p_lower:
        return f"⚡ {lbl}"
    if "شلبي" in lbl or "shelby" in p_lower:
        return f"🦙 {lbl}"
    if "سبك" in lbl or "synth" in p_lower:
        return f"🏆 {lbl}"
    return f"🤖 {lbl}"


def synthesizer_final_message_html(golden_text, gemini_text=None, shelby_text=None,
                                   role_id=None, session_name=None, title=None, telemetry=None,
                                   include_drafts=False, synth_provider=None):
    """
    الرسالة الذهبية النهائية المسبوكة بعد نجاح الوكيل الثالث:
    تعرض النسخة الذهبية داخل <pre>، مع وسم يوضح المساهمين (فلاش + شلبي) والمزود الفعلي للسبك، وتذييل التوقيت الدقيق خارج <pre>.
    (افتراضياً لا تُعرض المسودات المطوية في الشات النهائي لإبقاء الرسالة نظيفة وموجزة، مع حفظها في كاش .md).
    """
    role = prompts.get_role(role_id) or {}
    role_name = role.get("name") or role_id or "عام"
    head_parts = []
    if title:
        head_parts.append(f"🏷️ <b>{html.escape(title)}</b>")

    provider = synth_provider
    if not provider and isinstance(telemetry, dict):
        provider = telemetry.get("synth_provider")

    provider_tag = ""
    if provider == "shelby":
        provider_tag = " [🦙 شلبي]"
    elif provider == "flash":
        provider_tag = " [⚡ فلاش (ارتداد)]"
    elif provider:
        provider_tag = f" [{html.escape(str(provider))}]"

    head_txt = f"<b>🏆 النسخة الذهبية المسبوكة (السبّاك){provider_tag}</b> · دور: {html.escape(str(role_name))}"
    if session_name:
        head_txt += f" · جلسة: «{html.escape(session_name)}»"
    head_parts.append(head_txt)

    body = golden_text

    blocks = [
        "\n".join(head_parts),
        f"<pre>{html.escape(body)}</pre>",
    ]

    # لو طُلب صراحة تضمين المسودات الأولية (افتراضياً تُحذف في الرد النهائي بناءً على رغبة المستخدم)
    if include_drafts:
        if gemini_text:
            blocks.append(f"<blockquote expandable>⚡ <b>[مسودة فلاش]:</b>\n{html.escape(gemini_text)}</blockquote>")

        if shelby_text:
            blocks.append(f"<blockquote expandable>🦙 <b>[مسودة شلبي]:</b>\n{html.escape(shelby_text)}</blockquote>")

    if telemetry and isinstance(telemetry, dict):
        stages_data = telemetry.get("stages")
        if stages_data and isinstance(stages_data, dict):
            stage_items = []
            for st_name, calls_dict in stages_data.items():
                if isinstance(calls_dict, dict):
                    for lbl, sec in calls_dict.items():
                        icon_lbl = _format_icon_label(lbl)
                        stage_items.append(f"{icon_lbl}: {sec:.1f}ث")
            t_tot = f"{telemetry.get('total', 0):.1f}ث"
            if stage_items:
                blocks.append(f"{' | '.join(stage_items)} | ⏱️ إجمالي: {t_tot}")
            else:
                t_syn = f"{telemetry.get('synth', 0):.1f}ث"
                blocks.append(f"🏆 السبك: {t_syn} | ⏱️ إجمالي: {t_tot}")
        else:
            t_gem = f"{telemetry.get('gemini', 0):.1f}ث"
            t_shl = f"{telemetry.get('shelby', 0):.1f}ث"
            t_syn = f"{telemetry.get('synth', 0):.1f}ث"
            t_tot = f"{telemetry.get('total', 0):.1f}ث"
            prov_label = ""
            if provider == "shelby":
                prov_label = " (🦙 شلبي)"
            elif provider == "flash":
                prov_label = " (⚡ فلاش)"
            blocks.append(f"⚡ فلاش: {t_gem} | 🤖 شلبي: {t_shl} | 🏆 السبك: {t_syn}{prov_label} | ⏱️ إجمالي: {t_tot}")

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
                         t0_flow, gemini_elapsed, res_lock, result, _bg_finalize,
                         chat_id=None, user_id=None, entry_id=None):
    """وضع السبك الثلاثي (Synthesizer): مسودة جيميني فورية ثم استبدالها بالنسخة الذهبية المسبوكة."""
    max_edit = Config.TELEGRAM_MAX_MSG - 50
    t_label = _local_timeout_label()
    init_html = synthesizer_interim_message_html(
        polished, role_id=role_id, session_name=session_name,
        title=result["title"], shelby_status="waiting"
    )
    controller = LiveMessageController(
        sender, anchor, title=result.get("title"), session_name=session_name, role_id=role_id,
        chat_id=chat_id, user_id=user_id, entry_id=entry_id
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
        controller.anchor = wait_anchor
        mid = controller.start_live(
            f"⏳ <i>جاري توليد النسخة الذهبية المسبوكة مع شلبي (مهلة {t_label})...</i>"
        )
        result["polished_msg_id"] = draft_mid or mid
    else:
        draft_sent = True
        wait_anchor = anchor
        mid = controller.start_live(init_html)
        result["polished_msg_id"] = mid

    use_short_above = (draft_separated and draft_sent)

    def _draft_fallback_view(status, short_html, err_detail=None):
        if use_short_above:
            return short_html, _html_to_plain(short_html)
        full = synthesizer_interim_message_html(
            polished, role_id=role_id, session_name=session_name,
            title=result["title"], shelby_status=status, error_detail=err_detail
        )
        return full, polished

    def _bg_run_pipeline():
        """مسار تشغيل الـ Pipeline عند وجود pipeline.json على القرص (T6c — DEC-P2-08/09).
        حلقة عامة تدور على كافة المراحل المفعلة (enabled_stages) دون أي أسماء ثابتة للمزودات أو المراحل."""
        import pipeline_config
        import pipeline_runner
        import text_assist
        import drafts
        import guards
        import logkit
        import memory

        cfg = pipeline_config.get()
        display = cfg.get("display") or {}
        providers = cfg.get("providers") or {}
        stages = cfg.get("stages") or []
        enabled_stages = [s for s in stages if s.get("enabled", True)]
        mem_cfg = cfg.get("memory") or {}
        mem_enabled = bool(mem_cfg.get("enabled", False))
        allowed_topics = mem_cfg.get("topics") or memory.DEFAULT_TOPICS

        max_parallel = int(display.get("max_parallel", 4))
        live_cap = int(display.get("live_cap_utf16", 3400))
        total_budget = pipeline_config.pipeline_deadline_sec(cfg)
        flow_deadline = t0_flow + total_budget
        min_exec = getattr(Config, "MIN_SHELBY_EXEC_SEC", 5.0)
        display_style = str(display.get("mode") or display.get("style") or "single_live").strip()
        show_stages_cfg = display.get("show_stages")
        owner_ctx = memory.get_owner_context(mem_cfg) if mem_enabled else None
        max_owner_chars = int(mem_cfg.get("inject_max_bytes", 25600) or 25600)
        discovered_topic = "notes"

        all_drafts_to_save = []
        precomp_lbl = str(result.get("polished_label") or "فلاش")
        if polished:
            all_drafts_to_save.append((precomp_lbl, polished))

        stage_outputs = {}          # stage_name -> list of CallResult
        telemetry_stages = {}       # stage_name -> {label/provider: elapsed_s}
        golden_call = None
        golden_text = None

        # آخر اسم مزود تم تنفيذه في polish المسبق للحقن الذكي في أول مرحلة (DEC-P2-08 تنبيه 2)
        # fallback: "flash_lite" كآخر قيمة افتراضية موثقة متوافقة مع polish() القديمة
        precomputed_prov = result.get("polished_provider") or "flash_lite"

        def _console_progress(event, call):
            """مستمع كونسول حي يطبع نبضات المحركات أولاً بأول على الشاشة (DEC-P2-10)."""
            icon_lbl = _format_icon_label(call.label or call.provider, call.provider)
            if event == "started":
                msg = f"⏳ [{icon_lbl}] بدأ..."
            elif event == "ok":
                msg = f"✅ [{icon_lbl}] {call.elapsed_s:.1f}ث"
            elif event == "failed":
                err_clean = logkit.redact(call.error or "خطأ غير محدد")
                msg = f"❌ [{icon_lbl}] {call.elapsed_s:.1f}ث — {err_clean}"
            elif event == "skipped":
                err_clean = logkit.redact(call.error or "نجح المزود ذو الأولوية")
                msg = f"⏭️ [{icon_lbl}] تخطٍّ: {err_clean}"
            else:
                msg = f"ℹ️ [{icon_lbl}] {event}"

            print(msg, flush=True)
            logger.info(msg)

        for stage_idx, stage in enumerate(enabled_stages):
            st_name = stage.get("name") or f"stage_{stage_idx}"
            telemetry_stages[st_name] = {}
            st_calls_to_run = []
            st_injected_calls = []

            # 1. فحص إمكانية حقن المسودة المسبقة في المرحلة الأولى لتفادي إعادة النداء
            for c in stage.get("calls") or []:
                if (stage_idx == 0 and c.get("provider") == precomputed_prov and
                        polished and not c.get("only_if_previous_failed")):
                    inj_lbl = c.get("label") or precomp_lbl
                    injected_res = pipeline_runner.CallResult(
                        call_id=str(c.get("id") or f"{c.get('provider')}_{c.get('repeat_index', 1)}"),
                        provider=c.get("provider"),
                        model=str(c.get("model") or "precomputed"),
                        status="ok",
                        text=polished,
                        label=inj_lbl,
                        elapsed_s=gemini_elapsed,
                    )
                    st_injected_calls.append(injected_res)
                    telemetry_stages[st_name][inj_lbl] = round(gemini_elapsed, 1)
                    inj_icon_lbl = _format_icon_label(inj_lbl, c.get("provider", ""))
                    msg_inj = f"✅ [{inj_icon_lbl}] {gemini_elapsed:.1f}ث (مسودة مسبقة)"
                    print(msg_inj, flush=True)
                    logger.info(msg_inj)
                else:
                    st_calls_to_run.append(c)

            # تصفية النداءات الثقيلة (طابور GPU / CLI محلي) لو الوقت المتبقي أقل من حد التنفيذ
            rem_time = max(1.0, flow_deadline - time.monotonic())
            if rem_time < min_exec:
                st_calls_to_run = [
                    c for c in st_calls_to_run
                    if not (providers.get(c.get("provider"), {}).get("queue") == "gpu" or
                            providers.get(c.get("provider"), {}).get("type") == "local_cli")
                ]

            # تجهيز مدخلات المرحلة السابقة
            collected_drafts = []
            for prev_st in stage.get("inputs") or []:
                if prev_st == "raw":
                    continue
                prev_calls = stage_outputs.get(prev_st) or []
                for pc in prev_calls:
                    if pc.status == "ok" and pc.text:
                        collected_drafts.append((pc.label or pc.provider, pc.text))

            # لو لم تُحدد inputs صراحة، نجمع من كافة المسودات السابقة الصالحة
            if not collected_drafts and stage_idx > 0 and not stage.get("inputs"):
                for s_calls in stage_outputs.values():
                    for pc in s_calls:
                        if pc.status == "ok" and pc.text:
                            collected_drafts.append((pc.label or pc.provider, pc.text))

            # حساب سياق الموضوع للمراحل اللاحقة (polishers/final) بعد اكتشافه في proposers (T8 تصحيح 3)
            topic_ctx = memory.get_topic_context(discovered_topic, mem_cfg) if (mem_enabled and stage_idx > 0) else None
            eff_summary = f"{topic_ctx}\n{l2}".strip() if (topic_ctx and l2) else (topic_ctx or l2)

            # تركيب البرومبت بحسب نوع المرحلة
            p_type = stage.get("prompt") or ("synthesis" if stage_idx == len(enabled_stages) - 1 else "polish")
            if p_type == "synthesis" or stage_idx == len(enabled_stages) - 1:
                p_sys, p_usr = prompts.compose_synthesis_prompt(
                    raw_text=raw,
                    drafts=collected_drafts if collected_drafts else None,
                    gemini_draft=polished if not collected_drafts else None,
                    role_id=role_id,
                )
                p_sys = text_assist.inject_system_context(
                    p_sys,
                    context_summary=eff_summary,
                    owner_context=owner_ctx,
                    mode="synthesis",
                    max_owner_chars=max_owner_chars,
                )
            elif p_type == "polish_multi" or (p_type == "polish" and collected_drafts):
                p_sys, p_usr = prompts.compose_polish_multi_prompt(
                    raw_text=raw,
                    drafts=collected_drafts,
                    role_id=role_id,
                )
                p_sys = text_assist.inject_system_context(
                    p_sys,
                    context_summary=eff_summary,
                    owner_context=owner_ctx,
                    mode="polish",
                    max_owner_chars=max_owner_chars,
                )
            else:
                p_sys = prompts.compose_prompt(
                    "polish",
                    role_id=role_id,
                    memory_enabled=mem_enabled,
                    topics=allowed_topics,
                )
                p_sys = text_assist.inject_system_context(
                    p_sys,
                    context_summary=eff_summary,
                    owner_context=owner_ctx,
                    mode="polish",
                    max_owner_chars=max_owner_chars,
                )
                p_usr = raw

            st_deadline = min(rem_time, pipeline_config.stage_deadline_sec(st_name, providers))
            st_cfg_to_run = dict(stage, calls=st_calls_to_run)

            stage_start_msg = f"🚀 مرحلة [{st_name}] ({len(st_calls_to_run)} نداء)..."
            print(stage_start_msg, flush=True)
            logger.info(stage_start_msg)

            st_res = pipeline_runner.run_stage(
                stage_name=st_name,
                stage_config=st_cfg_to_run,
                inputs={"raw": raw, "drafts": collected_drafts},
                providers=providers,
                deadline_s=st_deadline,
                system_prompt=p_sys,
                user_prompt=p_usr,
                cancel_event=_pipeline_cancel_event,
                max_concurrent=max_parallel,
                on_progress=_console_progress,
            )

            stage_end_msg = f"🏁 مرحلة [{st_name}]: {st_res.status} في {st_res.elapsed_s:.1f}ث"
            print(stage_end_msg, flush=True)
            logger.info(stage_end_msg)

            current_stage_calls = list(st_injected_calls) + list(st_res.calls)
            valid_stage_calls = []

            for call in current_stage_calls:
                c_lbl = call.label or call.provider
                if call.status == "ok" and call.elapsed_s > 0:
                    telemetry_stages[st_name][c_lbl] = round(call.elapsed_s, 1)

                if call.status == "ok" and call.text:
                    # فحص No-Op للمزودات
                    try:
                        is_noop = text_assist.is_shelby_noop(raw, call.text, role_id=role_id)
                    except TypeError:
                        is_noop = text_assist.is_shelby_noop(raw, call.text)
                    except Exception:
                        is_noop = False

                    if is_noop:
                        text_assist.note_shelby_noop()
                        logger.warning("⚠️ المزود %s أعاد النص دون تنظيم فعلي (No-Op)", c_lbl)
                        call = pipeline_runner.CallResult(
                            call_id=call.call_id,
                            provider=call.provider,
                            model=call.model,
                            status="failed",
                            text=None,
                            label=call.label,
                            elapsed_s=call.elapsed_s,
                            error="No-Op (أعاد النص دون تنظيم فعلي)",
                        )
                    else:
                        out_text = call.text.strip()
                        # تجريد وسم #topic قبل تطبيق القاموس وقبل المسودات والحراس (T8 تصحيح 1 و 2)
                        clean_out, found_top = memory.extract_and_strip_topic(out_text, allowed_topics)
                        if stage_idx == 0 and discovered_topic == "notes" and found_top != "notes":
                            discovered_topic = found_top
                        out_text = clean_out
                        if gl:
                            out_text, _ = gl.apply(out_text, target="polished")
                        call = pipeline_runner.CallResult(
                            call_id=call.call_id,
                            provider=call.provider,
                            model=call.model,
                            status="ok",
                            text=out_text,
                            label=call.label,
                            elapsed_s=call.elapsed_s,
                        )
                        valid_stage_calls.append(call)
                        all_drafts_to_save.append((c_lbl, out_text))

            stage_outputs[st_name] = valid_stage_calls

            # إرسال رسائل منفصلة صامتة لو وضع per_output مفعل
            if display_style == "per_output":
                for vc in valid_stage_calls:
                    if vc.text and sender and hasattr(sender, "send_message"):
                        try:
                            sender.send_message(
                                chat_id=anchor,
                                text=f"📝 <b>[{html.escape(vc.label or vc.provider)}]:</b>\n{html.escape(vc.text)}",
                                parse_mode="HTML",
                                disable_notification=True,
                            )
                        except Exception as e_send:
                            logger.warning("per_output send failed: %s", e_send)

            # تحديث العرض الحي بعد انتهاء المرحلة إذا كانت ضمن show_stages
            is_last_stage = (stage_idx == len(enabled_stages) - 1)
            should_show_interim = (
                not is_last_stage and
                display_style != "per_output" and
                not use_short_above and
                (show_stages_cfg is None or st_name in show_stages_cfg)
            )

            if should_show_interim:
                live_blocks = []
                for s_show_name, s_show_calls in stage_outputs.items():
                    if show_stages_cfg is None or s_show_name in show_stages_cfg:
                        for sc in s_show_calls:
                            if sc.text:
                                live_blocks.append((sc.label or sc.provider, sc.text))
                if live_blocks:
                    interim_html = render_live_html(
                        blocks=live_blocks,
                        title=result.get("title"),
                        role_id=role_id,
                        session_name=session_name,
                        footer_status="⏳ <i>جاري استكمال مراحل التنظيم والسبك...</i>",
                        live_cap=live_cap,
                        header_prefix="📝 النسخة المنظمة",
                    )
                    controller.update_interim(interim_html, force=False)
            elif not is_last_stage and use_short_above:
                controller.update_interim(
                    f"⏳ <b>تم إنجاز مرحلة {html.escape(st_name)}. جاري المتابعة...</b>",
                    force=False,
                )

            # إذا كانت هذه المرحلة الأخيرة، نحدد النسخة الذهبية
            if is_last_stage:
                for idx_c, vc in enumerate(valid_stage_calls):
                    if vc.status == "ok" and vc.text:
                        golden_call = vc
                        golden_text = vc.text
                        p_mapped = "flash" if vc.provider in ("flash_lite", "flash") else vc.provider
                        with text_assist._lock:
                            text_assist._state["synth_ok"] = text_assist._state.get("synth_ok", 0) + 1
                            if idx_c > 0 or any(c.status == "failed" for c in current_stage_calls):
                                text_assist._state["synth_fallback_calls"] = text_assist._state.get("synth_fallback_calls", 0) + 1
                            text_assist._state["synth_last_provider"] = p_mapped
                        break
                if not golden_text:
                    with text_assist._lock:
                        text_assist._state["synth_fail"] = text_assist._state.get("synth_fail", 0) + 1
                        text_assist._state["synth_last_error"] = str(st_res.error or "فشل السبك")
                        text_assist._state["synth_last_provider"] = "none"

        # فحص الحارس الحتمي للأرقام والكلمات اللاتينية (DEC-P2-08/09 تنبيه 1)
        if golden_text:
            # تجريد وسم #topic من النسخة الذهبية قبل الحارس والقاموس (T8 تصحيح 2)
            golden_text, top_g = memory.extract_and_strip_topic(golden_text, allowed_topics)
            if top_g != "notes" and discovered_topic == "notes":
                discovered_topic = top_g

            draft_texts = [txt for _, txt in all_drafts_to_save if txt]
            subset_warns = guards.numbers_latin_subset_check(
                golden_text=golden_text,
                raw_text=raw,
                drafts=draft_texts,
                glossary=gl,
            )
            for w in subset_warns:
                logger.warning('⚠️ guard: "%s" لم يرد في الخام أو المسودات', w)
                if mem_enabled:
                    memory.append_guard_warning(w, memory_cfg=mem_cfg)

            # فحص صدى سياق المالك في النسخة الذهبية (T7/T8 — DEC-P2-03/08)
            echo_ratio = float((cfg.get("memory") or {}).get("echo_ratio_strike") or getattr(Config, "CONTEXT_ECHO_RATIO", 0.60))
            if owner_ctx and guards.is_context_echo(golden_text, owner_ctx, ratio=echo_ratio):
                logger.warning(
                    "⚠️ echo_guard: النسخة الذهبية تحتوي على صدى لسياق المالك بنسبة >= %.2f",
                    echo_ratio,
                )

            # تطبيق القاموس النهائي للتأكيد
            if gl:
                golden_text, _ = gl.apply(golden_text, target="polished")

            # تسجيل الذاكرة السلبية: daily + context/<topic>.md + review/ (T8 — DEC-P2-03/04)
            if mem_enabled:
                v_entry_id = str(result.get("entry_id") or result.get("request_id") or "voice")
                memory.append_daily(v_entry_id, discovered_topic, golden_text, memory_cfg=mem_cfg)
                memory.update_topic_context(discovered_topic, golden_text, memory_cfg=mem_cfg)
                memory.maybe_write_weekly_review(glossary=gl, guard_warnings=subset_warns, memory_cfg=mem_cfg)

            all_drafts_to_save.append(("النسخة الذهبية", golden_text))
            synth_prov = golden_call.provider if golden_call else "pipeline"
            synth_el = golden_call.elapsed_s if golden_call else 0.0
            _bg_finalize(
                golden_text,
                f"[نسخة ذهبية مسبوكة]\n{golden_text}",
                session_text=golden_text,
                is_golden=True,
                synth_elapsed=synth_el,
            )
            wall_total = time.monotonic() - t0_flow
            telemetry = {
                "stages": telemetry_stages,
                "synth": synth_el,
                "total": wall_total,
                "synth_provider": synth_prov,
            }
            sent_mids = controller.finalize(
                golden_text,
                is_golden=True,
                telemetry=telemetry,
                synth_provider=synth_prov,
            )
            if sent_mids:
                with res_lock:
                    result["polished_msg_id"] = sent_mids[0]
                    result["golden_mids"] = sent_mids
        else:
            # مسار الإنقاذ بأفضل مسودة متوفرة (fallback)
            fallback_txt = None
            for scs in reversed(list(stage_outputs.values())):
                for sc in scs:
                    if sc.status == "ok" and sc.text:
                        fallback_txt = sc.text
                        break
                if fallback_txt:
                    break
            fallback_txt = fallback_txt or polished or raw
            _bg_finalize(fallback_txt, fallback_txt, session_text=fallback_txt, is_golden=False)
            controller.finalized = True

            noop_html, noop_plain = _draft_fallback_view(
                "noop",
                "ℹ️ <i>تم اعتماد المسودة البديلة (تعذر إكمال السبك النهائي)</i>",
            )
            sent_mid = _replace_or_send(
                sender, mid, wait_anchor, noop_html, noop_plain,
                filename_prefix="polished",
                caption="📝 النسخة المنظمة (اعتماد بديل)",
                head_html=_entry_head_html(result["title"], session_name),
            )
            if not use_short_above and sent_mid is not None:
                with res_lock:
                    result["polished_msg_id"] = sent_mid

        # حفظ كافة المسودات في كاش .md (T4)
        if all_drafts_to_save or golden_text:
            try:
                drafts.write(entry_id, final_text=golden_text, drafts=all_drafts_to_save)
            except Exception as e:
                logger.warning("drafts.write in pipeline delivery error: %s", e)

    def _bg_synthesizer():
        try:
            import pipeline_config
            if pipeline_config.has_disk_pipeline():
                return _bg_run_pipeline()
        except Exception as e:
            logger.warning("Pipeline check fallback to legacy: %s", e)

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
                    controller.finalized = True
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

                # --- المرحلة 2: تحديث الرسالة الحية بمسودة شلبي مع مسودة فلاش فوراً ---
                if not use_short_above:
                    interim_both_html = synthesizer_interim_message_html(
                        polished,
                        shelby_text=s_text,
                        role_id=role_id,
                        session_name=session_name,
                        title=result.get("title"),
                        shelby_status="synthesizing",
                    )
                    controller.update_interim(interim_both_html, force=False)
                else:
                    controller.update_interim(
                        "⏳ <b>تم استلام مسودة شلبي. جاري السبك الثلاثي...</b>",
                        force=False,
                    )

                t0_synth = time.monotonic()
                synth_kwargs = {"context_summary": l2} if _accepts_context_summary(text_assist.synthesize) else {}
                try:
                    synth_res = text_assist.synthesize(
                        raw, polished, s_text, role_id=role_id, glossary=gl, return_provider=True, **synth_kwargs
                    )
                except TypeError:
                    synth_res = text_assist.synthesize(
                        raw, polished, s_text, role_id=role_id, glossary=gl, **synth_kwargs
                    )

                if isinstance(synth_res, tuple) and len(synth_res) == 3:
                    golden_text, synth_err, synth_provider = synth_res
                elif isinstance(synth_res, tuple) and len(synth_res) == 2:
                    golden_text, synth_err = synth_res
                    synth_provider = getattr(text_assist, "get_last_synth_provider", lambda: "shelby")()
                else:
                    golden_text = synth_res
                    synth_provider = "shelby"

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
                        "synth_provider": synth_provider,
                    }
                    sent_mids = controller.finalize(
                        golden_text,
                        is_golden=True,
                        telemetry=telemetry,
                        gemini_text=polished,
                        shelby_text=s_text,
                        synth_provider=synth_provider,
                    )
                    if sent_mids:
                        with res_lock:
                            result["polished_msg_id"] = sent_mids[0]
                            result["golden_mids"] = sent_mids
                else:
                    _bg_finalize(polished, polished, session_text=polished, is_golden=False)
                    controller.finalized = True
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
                controller.finalized = True
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
            controller.finalized = True
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
        result["polished_provider"] = "flash_lite"
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
                            archive.append_polished(archive_text, entry_id=entry_id, is_golden=is_golden, role_id=role_id)
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
                archive.append_polished(polished, entry_id=entry_id, is_golden=False, role_id=role_id)
                if session_name:
                    sessions.append_polished(polished, session_id=session_id, entry_id=entry_id)

            if is_compare:
                if cmp_mode == "synthesizer":
                    result["compare_thread"] = _deliver_synthesizer(
                        raw, polished, role_id, gl, l2, session_name, sender, anchor, local_polish_fn,
                        t0_flow, gemini_elapsed, res_lock, result, _bg_finalize,
                        chat_id=(job_meta or {}).get("chat_id"),
                        user_id=(job_meta or {}).get("user_id") or (job_meta or {}).get("from_id"),
                        entry_id=entry_id,
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
