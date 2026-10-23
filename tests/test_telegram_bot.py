# -*- coding: utf-8 -*-
"""اختبارات بوت تليجرام (P28, 2.6, 2.14) بـ mock لـ requests."""
import importlib
import html
import pytest

VALID_A = "AIzaSy" + "A" * 33


class FakeResp:
    def __init__(self, status, text="", payload=None):
        self.status_code = status
        self.text = text
        self._payload = payload or {}
    def json(self):
        return self._payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


@pytest.fixture
def bot(fresh_env, monkeypatch):
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append({"url": url, "payload": json})
        # محاكاة رفض تليجرام للـ HTML المكسور
        if "sendMessage" in url and json.get("parse_mode") == "HTML" and "<broken>" in json.get("text", ""):
            return FakeResp(400, "Bad Request: can't parse entities")
        return FakeResp(200, payload={"ok": True})

    monkeypatch.setattr(telegram_bot.requests, "post", fake_post)
    return telegram_bot, sent


# ---------- التقسيم ----------

def test_chunk_text_respects_limit(bot):
    tb, _ = bot
    text = "كلمة " * 3000
    chunks = tb._chunk_text(text, 4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(c + " " for c in chunks).split() == text.split()


def test_chunk_text_short_is_single(bot):
    tb, _ = bot
    assert tb._chunk_text("قصير", 4000) == ["قصير"]


# ---------- الإرسال الآمن ----------

def test_long_transcript_is_split_into_multiple_messages(bot):
    """P28: نص > 4096 لا يُرفض — يُقسَّم."""
    tb, sent = bot
    text = "سطر طويل جداً " * 800          # ~11,000 حرف
    assert tb.send_transcript("T", "1", text, "gemini-3.8-flash", "A", reply_to_id=5)
    msgs = [s for s in sent if "sendMessage" in s["url"]]
    assert len(msgs) >= 3
    assert all(len(m["payload"]["text"]) <= 4096 for m in msgs)
    assert msgs[0]["payload"].get("reply_to_message_id") == 5
    assert "reply_to_message_id" not in msgs[1]["payload"]


def test_transcript_with_markdown_chars_is_escaped(bot):
    """P28: نص فيه ` * _ < > لا يكسر التنسيق."""
    tb, sent = bot
    text = "استخدم `pip install` و *args و snake_case و <tag>"
    tb.send_transcript("T", "1", text, "m", "k")
    payload = sent[-1]["payload"]
    assert payload["parse_mode"] == "HTML"
    assert "&lt;tag&gt;" in payload["text"]
    assert html.escape(text) in payload["text"]


def test_send_falls_back_to_plain_text_when_html_rejected(bot):
    tb, sent = bot
    ok = tb.send_telegram_message("T", "1", "<b>x</b> <broken>", parse_mode="HTML")
    assert ok
    assert len(sent) == 2
    assert "parse_mode" not in sent[1]["payload"]


def test_send_returns_false_on_network_error(bot, monkeypatch):
    tb, _ = bot
    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(tb.requests, "post", boom)
    assert tb.send_telegram_message("T", "1", "x") is False


# ---------- كشف الصوت (2.6) ----------

def test_is_audio_message_variants(bot):
    tb, _ = bot
    assert tb._is_audio_message({"voice": {"file_id": "v", "mime_type": "audio/ogg"}})[0] == "v"
    assert tb._is_audio_message({"audio": {"file_id": "a"}})[0] == "a"
    assert tb._is_audio_message({"document": {"file_id": "d", "mime_type": "audio/mpeg"}})[0] == "d"
    assert tb._is_audio_message({"document": {"file_id": "d2", "file_name": "rec.M4A"}})[0] == "d2"
    assert tb._is_audio_message({"document": {"file_id": "p", "mime_type": "application/pdf", "file_name": "x.pdf"}}) is None
    assert tb._is_audio_message({"text": "hi"}) is None


# ---------- الأوامر (2.14) ----------

def _last_text(sent):
    return [s for s in sent if "sendMessage" in s["url"]][-1]["payload"]["text"]


def test_key_in_chat_is_added(bot):
    tb, sent = bot
    tb.handle_text("T", "1", {"message_id": 1}, f"مشروع 7: {VALID_A}")
    assert tb.key_manager.data["keys"][0]["name"] == "مشروع 7"
    assert "تم بنجاح" in _last_text(sent)


def test_remove_and_rename_commands(bot):
    tb, sent = bot
    tb.key_manager.add_key(VALID_A, "Alpha")
    tb.handle_text("T", "1", {"message_id": 2}, "/rename Alpha Beta")
    assert tb.key_manager.data["keys"][0]["name"] == "Beta"
    tb.handle_text("T", "1", {"message_id": 3}, "/remove Beta")
    assert tb.key_manager.data["keys"] == []
    assert "حذف" in _last_text(sent)


def test_remove_without_args_shows_usage(bot):
    tb, sent = bot
    tb.handle_text("T", "1", {"message_id": 4}, "/remove")
    assert "الاستخدام" in _last_text(sent)


def test_status_and_keys_commands(bot):
    tb, sent = bot
    tb.key_manager.add_key(VALID_A, "A")
    tb.handle_text("T", "1", {"message_id": 5}, "/status")
    assert "تصفير الكوتا" in _last_text(sent)
    tb.handle_text("T", "1", {"message_id": 6}, "/keys@MyBot")
    assert "المتاح أولاً" in _last_text(sent)


def test_help_lists_new_commands(bot):
    tb, sent = bot
    tb.handle_text("T", "1", {"message_id": 7}, "/help")
    t = _last_text(sent)
    assert "/remove" in t and "/rename" in t and "Lite" in t
