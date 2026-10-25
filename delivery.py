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

from config import Config
import archive
import settings
import prompts
import glossary as glossary_mod


class Sender:
    """واجهة الإرسال القابلة للحقن: send_text(text, reply_to, parse_mode) -> message_id|None ؛ send_document(...)"""

    def __init__(self, send_text, send_document=None):
        self.send_text = send_text
        self.send_document = send_document


def prepare_texts(raw_text, glossary=None):
    """يرجع (raw_after_glossary, polished_base_for_glossary_fn). القاموس scope=both/raw_only على الخام فقط."""
    gl = glossary if glossary is not None else glossary_mod.get_glossary()
    raw, _n = gl.apply(raw_text, target="raw")
    return raw, gl


def raw_message_html(raw_text, footer_html=""):
    """<pre>البانر(اختياري)+النص</pre> + التذييل — البانر داخل كتلة النسخ (06 §4 قرار المالك)."""
    body = raw_text
    if settings.header_for("raw"):
        body = archive.banner_text() + "\n" + raw_text
    out = f"<pre>{html.escape(body)}</pre>"
    if footer_html:
        out += "\n" + footer_html
    return out


def polished_message_html(polished_text, role_id, session_name=None):
    role = prompts.get_role(role_id) or {}
    head = f"<b>📝 النسخة المنظمة</b> · دور: {html.escape(role.get('name', role_id))}"
    if session_name:
        head += f" · جلسة: «{html.escape(session_name)}»"
    body = polished_text
    if settings.header_for("polished"):
        body = archive.banner_text() + "\n" + polished_text
    return head + "\n" + f"<pre>{html.escape(body)}</pre>"


def prepare_for_web(raw_text, model=None, key_name=None, info=None):
    """مسار الويب: القاموس (scope الخام) + أرشفة ALL.md. لا إرسال ولا تنظيم. يرجع (text, archived).
    أي خطأ في القاموس/الأرشيف لا يُسقط التفريغ (نرجع النص كما هو)."""
    info = info or {}
    try:
        raw, _gl = prepare_texts(raw_text)
    except Exception as e:
        print(f"⚠️ القاموس (ويب): {e}")
        raw = raw_text
    try:
        archived = archive.append(raw, model=model, elapsed=info.get("elapsed"), key_name=key_name,
                                  session_name=None)
    except Exception as e:
        print(f"⚠️ الأرشفة (ويب): {e}")
        archived = False
    return raw, archived


def deliver(raw_text, model, key_name, info, sender, reply_to_id=None, polish_fn=None, glossary=None,
            session_name=None, footer_html=""):
    """
    ينفّذ خط الإنتاج كاملاً. يرجع قاموساً: {raw, polished, raw_msg_id, polished_msg_id, mode, archived, polish_error}.
    polish_fn(raw_text, role_id, glossary) -> (text|None, reason) — افتراضياً text_assist.polish (يُحقن في الاختبارات).
    """
    mode = settings.delivery_mode()
    role_id = settings.role()
    raw, gl = prepare_texts(raw_text, glossary)
    result = {"raw": raw, "polished": None, "raw_msg_id": None, "polished_msg_id": None, "mode": mode,
              "archived": False, "polish_error": None, "role": role_id}

    # 2) أرشفة قبل أي إرسال (outbox)
    result["archived"] = archive.append(raw, model=model, elapsed=(info or {}).get("elapsed"), key_name=key_name,
                                        session_name=session_name)

    # 3) الخام
    if mode in ("dual", "raw_only"):
        result["raw_msg_id"] = _send_long(sender, raw_message_html(raw, footer_html), raw, reply_to_id,
                                          filename_prefix="transcript", caption="📄 التفريغ الخام (طويل → ملف)")

    # 4) المنظم
    if mode in ("dual", "polished_only"):
        if polish_fn is None:
            import text_assist
            polish_fn = text_assist.polish
        try:
            polished, reason = polish_fn(raw, role_id, gl)
        except Exception as e:  # pragma: no cover - دفاع: التنظيم لا يكسر الخام أبداً
            polished, reason = None, f"{type(e).__name__}: {e}"
        anchor = result["raw_msg_id"] or reply_to_id
        if polished:
            result["polished"] = polished
            archive.append_polished(polished)
            result["polished_msg_id"] = _send_long(sender, polished_message_html(polished, role_id, session_name),
                                                   polished, anchor, filename_prefix="polished",
                                                   caption="📝 النسخة المنظمة (طويلة → ملف)")
        else:
            result["polish_error"] = reason
            if mode == "polished_only":
                # لا خام أُرسل: نرسل الخام كبديل حتى لا يضيع الفويس (07 §2.2 روح القرار: لا رفض)
                result["raw_msg_id"] = _send_long(sender, raw_message_html(raw, footer_html), raw, reply_to_id,
                                                  filename_prefix="transcript", caption="📄 التفريغ الخام")
                sender.send_text(f"⚠️ تعذر التنظيم الآن ({html.escape(str(reason or ''))}) — أرسلت الخام بدلاً منه.",
                                 result["raw_msg_id"], "HTML")
            else:
                sender.send_text(f"⚠️ تعذر التنظيم الآن ({html.escape(str(reason or ''))}) — الخام فوق.", anchor, "HTML")
    return result


def _send_long(sender, html_text, plain_text, reply_to_id, filename_prefix, caption):
    """نص > POLISH_FILE_THRESHOLD → ملف .md + معاينة؛ وإلا رسالة HTML عادية. يرجع message_id (أو None)."""
    if len(plain_text) > Config.POLISH_FILE_THRESHOLD and sender.send_document is not None:
        preview = plain_text[:500] + ("…" if len(plain_text) > 500 else "")
        mid = sender.send_text(f"<pre>{html.escape(preview)}</pre>\n{caption}", reply_to_id, "HTML")
        try:
            sender.send_document(f"{filename_prefix}.md", plain_text.encode("utf-8"), "text/markdown", caption, mid or reply_to_id)
        except Exception as e:  # pragma: no cover
            print(f"⚠️ إرسال الملف فشل: {e}")
        return mid
    return sender.send_text(html_text, reply_to_id, "HTML")
