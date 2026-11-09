# -*- coding: utf-8 -*-
"""
حزمة اختبارات المرحلة الرابعة (M4) وفق خطة التنفيذ المعتمدة v2.12:
- نظام الكاش الذري المشفر لملفات .md (md_cache) وصلاحيات 0o700/0o600.
- التحقق من التوكن، منع Symlink، وحد الصلاحية TTL (24 ساعة).
- سقف حماية السعة (200 ملف أو 50MB) وفرز FIFO.
- تفويض المجموعات (user_id صاحب الطلب في chat_id < 0).
- استرجاع الأرشيف بالمعرف (archive.get_entry_by_id) ودعم synth_golden و role.
- قصر زر .md الأزرق على مخرجات السبّاك الذهبية الطويلة (> 4000 UTF-16) وعلى القطعة الأخيرة فقط.
- تدرج الأزرار التفاعلية (md:ask -> md:ok / md:no) ورفع الملف.
- أمر /md التفاعلي مع البارامترات والردود والأرشيف.
- تكامل COMMAND_PARITY ودعم button style.
"""

import json
import os
import time
import pytest

from config import Config
import md_cache
import archive
import panel
from delivery import LiveMessageController, Sender
import telegram_bot


pytestmark = pytest.mark.usefixtures("fresh_env")


class MockDeliverySender:
    def __init__(self, can_edit=True, can_delete=True):
        self.sent = []
        self.edits = []
        self.deletes = []
        self.docs = []
        self.can_edit = can_edit
        self.can_delete = can_delete
        self._next_id = 100

    def send_text(self, text, reply_to=None, parse_mode=None, reply_markup=None):
        mid = self._next_id
        self._next_id += 1
        self.sent.append({
            "mid": mid, "text": text, "reply_to": reply_to,
            "parse_mode": parse_mode, "reply_markup": reply_markup
        })
        return mid

    def edit_text(self, message_id, text, parse_mode="HTML", reply_markup=None):
        if not self.can_edit:
            return False
        self.edits.append({
            "mid": message_id, "text": text,
            "parse_mode": parse_mode, "reply_markup": reply_markup
        })
        return True

    def delete_text(self, message_id):
        if not self.can_delete:
            return False
        self.deletes.append(message_id)
        return True

    def send_document(self, fn, data, mime, caption, reply_to):
        self.docs.append({"filename": fn, "data": data, "caption": caption, "reply_to": reply_to})
        return 999

    def as_sender(self):
        return Sender(
            send_text=self.send_text,
            send_document=self.send_document,
            edit_text=self.edit_text if self.can_edit else None,
            delete_text=self.delete_text if self.can_delete else None,
        )


def test_md_cache_store_and_get(tmp_path, monkeypatch):
    """الكاش الذري: التخزين الذري واسترجاع البيانات ومطابقة التوكن والصلاحيات."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    token = md_cache.store("محتوى تجريبي للسبك الذهبي", chat_id=12345, user_id=67890, entry_id="ent_001")
    assert md_cache.TOKEN_RE.match(token)

    cdir = md_cache.get_cache_dir()
    fpath = os.path.join(cdir, f"{token}.json")
    assert os.path.exists(fpath)

    # التحقق من القراءة الناجحة مع مطابقة المعرفات
    item = md_cache.get(token, chat_id=12345, user_id=67890)
    assert item is not None
    assert item["text"] == "محتوى تجريبي للسبك الذهبي"
    assert item["entry_id"] == "ent_001"
    assert item["chat_id"] == "12345"
    assert item["user_id"] == "67890"

    # رفض استرجاع توكن غير صالح أو مسار اختراق
    assert md_cache.get("../../../etc/passwd") is None
    assert md_cache.get("invalid$token") is None
    assert md_cache.get("") is None


def test_md_cache_ttl_expiration(tmp_path, monkeypatch):
    """انتهاء الصلاحية: التحقق من حذف الملفات بعد انتهاء مهلة TTL (24س)."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    token = md_cache.store("نص سينتهي", chat_id=100)
    cdir = md_cache.get_cache_dir()
    fpath = os.path.join(cdir, f"{token}.json")

    # تعديل expires_at إلى الماضي يدوياً
    with open(fpath, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["expires_at"] = time.time() - 100
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(data, f)

    assert md_cache.get(token) is None
    assert not os.path.exists(fpath)


def test_md_cache_symlink_rejection(tmp_path, monkeypatch):
    """منع الروابط الرمزية: رفض قراءة أي ملف خارج مجلد الكاش أو رابط رمزي."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    cdir = md_cache.get_cache_dir()
    outside_file = tmp_path / "secret.json"
    outside_file.write_text('{"token": "ext_tok", "text": "secret"}', encoding="utf-8")

    link_path = os.path.join(cdir, "symlink_tok.json")
    try:
        os.symlink(str(outside_file), link_path)
        # لو نجح إنشاء الرابط، يجب أن ترفضه دالة get و prune
        assert md_cache.get("symlink_tok") is None
    except (OSError, NotImplementedError):
        # في بيئات Windows بدون امتيازات symlink، نتأكد من دالة _is_safe_path
        assert not md_cache._is_safe_path(str(outside_file), cdir)


def test_md_cache_group_delegation(tmp_path, monkeypatch):
    """تفويض المجموعات (A-18): منع أي عضو غير صاحب الطلب من استخراج الملف في المجموعات."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    group_chat_id = -100987654321
    owner_id = 555
    intruder_id = 777

    token = md_cache.store("نص للمجموعة", chat_id=group_chat_id, user_id=owner_id)

    # صاحب الطلب مسموح له
    assert md_cache.get(token, chat_id=group_chat_id, user_id=owner_id) is not None

    # عضو آخر في نفس المجموعة يُرفض تفويضه
    assert md_cache.get(token, chat_id=group_chat_id, user_id=intruder_id) is None

    # طلب من محادثة مختلفة يُرفض
    assert md_cache.get(token, chat_id=111, user_id=owner_id) is None


def test_md_cache_prune_capacity(tmp_path, monkeypatch):
    """سقف السعة وفرز FIFO: حذف الأقدم عند تجاوز 200 ملف أو 50MB."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(md_cache, "MAX_CACHE_FILES", 5)

    tokens = []
    for i in range(7):
        tok = md_cache.store(f"file_{i}", chat_id=i)
        tokens.append(tok)
        time.sleep(0.01)

    cdir = md_cache.get_cache_dir()
    files = [f for f in os.listdir(cdir) if f.endswith(".json")]
    assert len(files) <= 5
    # الأقدم (أول ملفين) تم حذفهما
    assert md_cache.get(tokens[0]) is None
    assert md_cache.get(tokens[1]) is None
    # الأحدث لا يزال موجوداً
    assert md_cache.get(tokens[-1]) is not None


def test_archive_get_entry_by_id_and_synth_golden(tmp_path, monkeypatch):
    """الأرشفة: دعم استرجاع المدخل بالمعرف وتخزين وسوم synth_golden و role."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    eid = "test_ent_123"
    archive.append("نص تفريغ خام", model="gemini-flash", elapsed=1.5, entry_id=eid)
    archive.append_polished("نص مسبوك ذهبي فاخر", entry_id=eid, is_golden=True, role_id="lawyer")

    ent = archive.get_entry_by_id(eid)
    assert ent is not None
    assert ent["raw"] == "نص تفريغ خام"
    assert "نص مسبوك ذهبي فاخر" in ent["polished"]
    assert ent["synth_golden"] is True
    assert ent["role"] == "lawyer"

    # استرجاع معرف غير موجود يرجع None
    assert archive.get_entry_by_id("non_existent_id") is None


def test_panel_command_parity_and_button_style():
    """لوحة التحكم وتكافؤ الأوامر: مطابقة مفاتيح md ودعم نمط الأزرار style."""
    assert panel.parity_key("md:ask:tok123") == "md:ask"
    assert panel.parity_key("md:ok:tok123") == "md:ok"
    assert panel.parity_key("md:no:tok123") == "md:no"
    assert panel.COMMAND_PARITY.get("md:ask") == "/md"
    assert panel.COMMAND_PARITY.get("md:ok") == "/md"
    assert panel.COMMAND_PARITY.get("md:no") == "/md"

    b1 = panel.btn("تحويل", "md:ask:123", style="primary")
    assert b1["text"] == "تحويل"
    assert b1["callback_data"] == "md:ask:123"
    assert b1["style"] == "primary"


def test_live_message_controller_md_button_only_on_chunk_last_when_exceeding_4000():
    """العقد 8: زر .md الأزرق يظهر فقط للمخرجات المسبوكة الطويلة (> 4000) وعلى القطعة الأخيرة فقط."""
    mock = MockDeliverySender()
    sender = mock.as_sender()
    ctrl = LiveMessageController(
        sender, anchor=50, title="فحص الأزرار", chat_id=123, user_id=456, entry_id="e1"
    )
    ctrl.start_live("<b>انتظار</b>")

    # 1. نص قصير مسبوك (<= 4000): لا يحصل على زر .md نهائياً
    short_golden = "نص مسبوك قصير وموجز"
    ctrl.finalize(short_golden, is_golden=True)
    assert len(mock.edits) == 1
    edit_item = mock.edits[0]
    # لا توجد reply_markup للأزرار
    assert edit_item.get("reply_markup") is None

    # 2. نص طويل مسبوك (> 4000 UTF-16): يتجزأ لقطع، والقطعة الأخيرة فقط تحمل الزر
    mock2 = MockDeliverySender()
    ctrl2 = LiveMessageController(
        mock2.as_sender(), anchor=60, title="فحص الطويل", chat_id=123, user_id=456, entry_id="e2"
    )
    ctrl2.start_live("<b>انتظار 2</b>")

    long_golden = "فقرة مسبوكة طويلة جداً للتحقق من التجزئة والزر الأزرق.\n" * 120  # تتجاوز 4000 حرف
    sent_mids = ctrl2.finalize(long_golden, is_golden=True)
    assert len(sent_mids) > 1

    # القطعة الأولى (تعديل الحية) ليس بها reply_markup
    assert mock2.edits[0].get("reply_markup") is None

    # القطع الوسيطة ليس بها أزرار، بينما القطعة الأخيرة تحمل زر md:ask
    assert len(mock2.sent) >= 2  # القطعة الحية الأولى + القطع التالية
    last_sent = mock2.sent[-1]
    assert last_sent.get("reply_markup") is not None
    kb = last_sent["reply_markup"]["inline_keyboard"]
    assert len(kb) == 1
    assert kb[0][0]["text"] == "📄 تحويل لملف .md"
    assert kb[0][0]["callback_data"].startswith("md:ask:")
    assert kb[0][0].get("style") == "primary"


def test_telegram_callback_md_flow(monkeypatch, tmp_path):
    """دورة حياة أزرار .md التفاعلية: ask -> تأكيد/إلغاء -> ok يرفع الملف ويزيل الأزرار."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    token_str = md_cache.store("محتوى الملف الذهبي النهائي", chat_id=999, user_id=111, entry_id="eid_999")

    edited_markups = []
    sent_docs = []

    def mock_edit_markup(tok, cid, mid, reply_markup=None):
        edited_markups.append({"cid": cid, "mid": mid, "markup": reply_markup})
        return True

    def mock_send_doc(tok, cid, fn, data, mime, caption, reply_to_id, **kwargs):
        sent_docs.append({"cid": cid, "fn": fn, "data": data, "caption": caption, "reply_to": reply_to_id})
        return 555

    monkeypatch.setattr(telegram_bot, "edit_telegram_reply_markup", mock_edit_markup)
    monkeypatch.setattr(telegram_bot, "_send_document", mock_send_doc)

    # 1. الضغط على md:ask
    cq_ask = {"from": {"id": 111}}
    note1 = telegram_bot._dispatch_callback("tok", 999, 101, f"md:ask:{token_str}", cq=cq_ask)
    assert "تأكيد" in note1
    assert len(edited_markups) == 1
    kb = edited_markups[0]["markup"]["inline_keyboard"]
    assert kb[0][0]["callback_data"] == f"md:ok:{token_str}"
    assert kb[0][1]["callback_data"] == f"md:no:{token_str}"

    # 2. الضغط على md:no (إلغاء)
    note2 = telegram_bot._dispatch_callback("tok", 999, 101, f"md:no:{token_str}", cq=cq_ask)
    assert "إلغاء" in note2
    assert len(edited_markups) == 2
    assert edited_markups[1]["markup"]["inline_keyboard"][0][0]["callback_data"] == f"md:ask:{token_str}"

    # 3. محاولة ضغط ok من مستخدم متطفل في مجموعة
    cq_intruder = {"from": {"id": 222}}
    note_int = telegram_bot._dispatch_callback("tok", -100999, 101, f"md:ok:{token_str}", cq=cq_intruder)
    assert "لا يمكن لغير صاحب الطلب" in note_int
    assert len(sent_docs) == 0

    # 4. تأكيد صاحب الطلب md:ok
    cq_ok = {"from": {"id": 111}}
    note3 = telegram_bot._dispatch_callback("tok", 999, 101, f"md:ok:{token_str}", cq=cq_ok)
    assert "تم رفع الملف بنجاح" in note3
    assert len(sent_docs) == 1
    doc = sent_docs[0]
    assert doc["fn"] == "golden_synthesized.md"
    assert doc["data"].decode("utf-8") == "محتوى الملف الذهبي النهائي"
    # مسح الأزرار بعد الرفع
    assert edited_markups[-1]["markup"] is None


def test_telegram_cmd_md(monkeypatch, tmp_path):
    """أمر /md في تليجرام: بالتوكن، بالمعرف، وبالرد، وبالأرشيف الأخير."""
    monkeypatch.setattr(Config, "DATA_DIR", str(tmp_path))
    token_str = md_cache.store("محتوى أمر md الصافي", chat_id=888, user_id=111, entry_id="ent_cmd")

    sent_docs = []
    sent_msgs = []

    def mock_send_doc(tok, cid, fn, data, mime, caption, reply_to_id, **kwargs):
        sent_docs.append({"cid": cid, "fn": fn, "data": data, "caption": caption, "reply_to": reply_to_id})
        return 777

    def mock_send_msg(tok, cid, text, reply_to_id=None, **kwargs):
        sent_msgs.append({"cid": cid, "text": text, "reply_to": reply_to_id})
        return 888

    monkeypatch.setattr(telegram_bot, "_send_document", mock_send_doc)
    monkeypatch.setattr(telegram_bot, "send_telegram_message", mock_send_msg)

    # 1. أمر /md مع توكن كاش صالح
    msg1 = {"message_id": 10, "from": {"id": 111}}
    telegram_bot.handle_text("tok", 888, msg1, f"/md {token_str}")
    assert len(sent_docs) == 1
    assert sent_docs[0]["data"].decode("utf-8") == "محتوى أمر md الصافي"

    # 2. أمر /md مع معرف أرشيف
    archive.append("خام أرشيف", model="gemini", elapsed=1.0, entry_id="arc_123")
    archive.append_polished("نص الأرشيف الذهبي", entry_id="arc_123", is_golden=True)
    msg2 = {"message_id": 11, "from": {"id": 111}}
    telegram_bot.handle_text("tok", 888, msg2, "/md arc_123")
    assert len(sent_docs) == 2
    assert "نص الأرشيف الذهبي" in sent_docs[1]["data"].decode("utf-8")

    # 3. أمر /md بالرد على رسالة
    msg3 = {"message_id": 12, "from": {"id": 111}, "reply_to_message": {"text": "رسالة تم الرد عليها بالمنظم"}}
    telegram_bot.handle_text("tok", 888, msg3, "/md")
    assert len(sent_docs) == 3
    assert sent_docs[2]["data"].decode("utf-8") == "رسالة تم الرد عليها بالمنظم"
