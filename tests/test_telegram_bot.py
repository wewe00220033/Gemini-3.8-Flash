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


def _capture_documents(tb, sent=None):
    captured = []

    def fake_post(url, json=None, timeout=None, data=None, files=None):
        if "sendDocument" in url:
            captured.append({"data": data, "files": files})
        elif sent is not None:
            sent.append({"url": url, "payload": json})
        return FakeResp(200, payload={"ok": True})
    import pytest as _p
    mp = _p.MonkeyPatch(); mp.setattr(tb.requests, "post", fake_post)
    return captured, mp


def test_backup_public_never_contains_keys_json(bot, fresh_env, tmp_path):
    """13 S4: /backup يرسل ZIP بالبيانات العامة فقط — keys.json لا يدخل أبداً حتى لو كان داخل DATA_DIR."""
    import zipfile, io
    tb, sent = bot
    tb.key_manager.add_key(VALID_A, "A")
    data_dir = tmp_path
    (data_dir / "transcripts").mkdir()
    (data_dir / "transcripts" / "ALL.md").write_text("# archive\n", encoding="utf-8")
    (data_dir / "transcripts" / "keys.json").write_text("{\"leak\": 1}", encoding="utf-8")  # تسريب مفتعل
    import backup as bk
    import pytest as _p
    mp0 = _p.MonkeyPatch(); mp0.setattr(bk.Config, "DATA_DIR", str(data_dir))
    captured, mp = _capture_documents(tb)
    try:
        tb.handle_text("T", "1", {"message_id": 9}, "/backup")
    finally:
        mp.undo(); mp0.undo()
    assert len(captured) == 1
    name, blob, mime = captured[0]["files"]["document"]
    assert name.startswith("backup_") and name.endswith(".zip") and mime == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert "transcripts/ALL.md" in names
    assert not any(n.endswith("keys.json") for n in names)
    assert VALID_A.encode() not in blob


def test_backup_secrets_requires_confirm_then_sends_keys(bot, fresh_env):
    tb, sent = bot
    import backup as bk
    import pytest as _p
    mp0 = _p.MonkeyPatch(); mp0.setattr(bk.Config, "KEYS_JSON_FILE", tb.Config.KEYS_JSON_FILE)
    tb.key_manager.add_key(VALID_A, "A")
    captured, mp = _capture_documents(tb, sent)
    try:
        tb.handle_text("T", "1", {"message_id": 9}, "/backup_secrets")
        assert captured == []
        assert any("confirm" in (m["payload"] or {}).get("text", "") for m in sent)
        tb.handle_text("T", "1", {"message_id": 10}, "/backup_secrets confirm")
    finally:
        mp.undo(); mp0.undo()
    assert len(captured) == 1
    import zipfile, io
    name, blob, _ = captured[0]["files"]["document"]
    assert name.startswith("secrets_")
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert z.namelist() == ["keys.json"]
    assert VALID_A.encode() in z.read("keys.json")


def test_help_mentions_both_backup_commands(bot):
    tb, sent = bot
    t = tb._help_text()
    assert "/backup " in t or "/backup —" in t
    assert "/backup_secrets" in t


# ======================================================================
# حادثة 01 — الشفافية: تذييل ⏱، رسالة التقدّم، /reset، /status بحالة الموديلات
# ======================================================================
def _msg(text="", mid=7):
    return {"message_id": mid, "chat": {"id": 1}, "text": text}


def test_footer_includes_latency_and_skipped(bot):
    tb, sent = bot
    info = {"elapsed": 7.9, "skipped": ["3.6-flash: 503 (يفتح بعد 2 د)"]}
    assert tb.send_transcript("T", "1", "نص", "gemini-3.5-transcribe", "P1", reply_to_id=5, info=info)
    body = sent[-1]["payload"]["text"]
    assert "⏱ 7.9 ث" in body and "تم تخطّي" in body and "3.6-flash" in body
    assert sent[-1]["payload"]["parse_mode"] == "HTML"


def test_footer_without_info_still_works(bot):
    tb, sent = bot
    assert tb.send_transcript("T", "1", "نص", "gemini-3.6-flash", "P1")
    assert "⏱" not in sent[-1]["payload"]["text"]


def test_reset_command_opens_circuits(bot):
    tb, sent = bot
    tb.model_circuit.pause("gemini-3.6-flash", 100, "503")
    tb.handle_text("T", "1", _msg("/reset"), "/reset")
    assert "تم فتح 1 قاطع" in sent[-1]["payload"]["text"]
    assert not tb.model_circuit.is_open("gemini-3.6-flash")


def test_status_shows_circuit_state_and_recent_attempts(bot):
    tb, sent = bot
    tb.key_manager.add_key(VALID_A, "A")
    tb.model_circuit.pause("gemini-3.6-flash", 100, "timeout")
    tb.model_circuit.record_attempt("gemini-3.6-flash", "A", "MODEL_SLOW", 21.3)
    tb.handle_text("T", "1", _msg("/status"), "/status")
    txt = sent[-1]["payload"]["text"]
    assert "⛔ 3.6-flash" in txt and "timeout" in txt and "🟢 3.5-transcribe" in txt
    assert "MODEL_SLOW" in txt and "21.3" in txt


def test_help_mentions_reset_and_generated_models(bot):
    tb, _ = bot
    h = tb._help_text()
    assert "/reset" in h and "3.6 Flash ➔ 3.5 Transcribe" in h and "3.8 Flash ➔" not in h


def test_progress_keeper_sends_and_deletes_progress_message(bot, monkeypatch):
    tb, sent = bot
    monkeypatch.setattr(tb.Config, "TYPING_REFRESH_SECONDS", 1)
    monkeypatch.setattr(tb.Config, "PROGRESS_AFTER_SECONDS", 0)

    def fake_post(url, json=None, timeout=None):
        sent.append({"url": url, "payload": json})
        if "sendMessage" in url:
            return FakeResp(200, payload={"ok": True, "result": {"message_id": 99}})
        return FakeResp(200, payload={"ok": True})
    monkeypatch.setattr(tb.requests, "post", fake_post)

    import time as _t
    with tb.ProgressKeeper("T", "1", reply_to_id=5) as pk:
        _t.sleep(1.6)
        assert pk.progress_msg_id == 99
    urls = [x["url"].rsplit("/", 1)[-1] for x in sent]
    assert urls.count("sendChatAction") >= 2
    assert "sendMessage" in urls and urls[-1] == "deleteMessage"
    assert sent[-1]["payload"] == {"chat_id": "1", "message_id": 99}
    assert "لسه شغّال" in [x for x in sent if x["url"].endswith("sendMessage")][0]["payload"]["text"]


def test_progress_keeper_no_message_when_fast(bot, monkeypatch):
    tb, sent = bot
    monkeypatch.setattr(tb.Config, "TYPING_REFRESH_SECONDS", 1)
    monkeypatch.setattr(tb.Config, "PROGRESS_AFTER_SECONDS", 60)
    with tb.ProgressKeeper("T", "1") as pk:
        pass
    assert pk.progress_msg_id is None
    assert not any(x["url"].endswith("deleteMessage") for x in sent)


def test_handle_audio_uses_ex_and_footer(bot, monkeypatch):
    tb, sent = bot
    monkeypatch.setattr(tb, "_download_file", lambda t, f: b"abc")
    monkeypatch.setattr(tb, "transcribe_audio_bytes_ex",
                        lambda b, m: ("النص", "gemini-3.6-flash", "A", {"elapsed": 6.4, "skipped": []}))
    tb.handle_audio("T", "1", _msg(), "fid", "audio/ogg", "voice")
    body = sent[-1]["payload"]["text"]
    assert "النص" in body and "⏱ 6.4 ث" in body and tb.STATE["processed"] >= 1
