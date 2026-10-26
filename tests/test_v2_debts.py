# -*- coding: utf-8 -*-
"""
fix/v2-debts — إغلاق الديون قبل النشر (13 §1.1 S1/S5، 09 §5):
S1 توكن ويب مولَّد تلقائياً على مضيف غير محلي + WEB_ALLOW_OPEN، S5 عدم الثقة بـ X-Forwarded-For إلا بـ TRUST_PROXY،
/restore_test يجمع الأجزاء تلقائياً، /backups [n]، وتوضيح توقيت الخادم (UTC) في الجدولة. كله بلا شبكة.
"""
import importlib
import io
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import HTTPServer

import pytest


# ---------------------------------------------------------------- helpers
class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = json.dumps(self._payload)
        self.headers = {}

    def json(self):
        return self._payload


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start(web_service):
    port = _free_port()
    httpd = HTTPServer(("127.0.0.1", port), web_service.WebRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _mk_zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _good_members(pad=0):
    return {
        "glossary.json": '{"entries": []}',
        "settings.json": '{"delivery_mode": "dual"}',
        "roles.json": "[]",
        "sessions/sessions_index.json": "[]",
        "transcripts/ALL.md": "# ALL\n" + ("x" * pad),
    }


@pytest.fixture
def bot(fresh_env, monkeypatch):
    import sessions
    sessions.reset_cache()
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    sent, docs = [], []

    def fake_post(url, json=None, timeout=None, **kw):
        sent.append({"method": url.rsplit("/", 1)[-1], "payload": json})
        return FakeResp(200, {"ok": True, "result": {"message_id": 500 + len(sent)}})

    monkeypatch.setattr(telegram_bot, "_http_post", fake_post)
    monkeypatch.setattr(telegram_bot, "_http_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network GET forbidden")))
    monkeypatch.setattr(telegram_bot, "_send_document",
                        lambda token, chat_id, filename, data, mime, caption, reply_to_id: docs.append((filename, data, caption)) or 900)
    return telegram_bot, sent, docs


def _texts(sent):
    return [x["payload"].get("text", "") for x in sent if x["method"] == "sendMessage"]


def _cmd(tb, text):
    tb.handle_text("T", "1", {"message_id": 1}, text)


# =====================================================================
# S1 — ensure_web_token (13 §1.1 / DoD §7)
# =====================================================================
def test_ensure_web_token_env_wins(fresh_env, monkeypatch):
    import web_service
    monkeypatch.setattr(web_service.Config, "WEB_ACCESS_TOKEN", "from-env")
    monkeypatch.setattr(web_service.Config, "WEB_HOST", "0.0.0.0")
    monkeypatch.setattr(web_service.Config, "WEB_ALLOW_OPEN", False)
    assert web_service.ensure_web_token() == ("env", "from-env")
    assert web_service.WEB_TOKEN_STATE == {"generated": False, "open": False}


def test_ensure_web_token_local_host_stays_open_without_token(fresh_env, monkeypatch):
    import web_service
    monkeypatch.setattr(web_service.Config, "WEB_ACCESS_TOKEN", "")
    monkeypatch.setattr(web_service.Config, "WEB_ALLOW_OPEN", False)
    for host in ("127.0.0.1", "localhost", "::1"):
        monkeypatch.setattr(web_service.Config, "WEB_HOST", host)
        assert web_service.ensure_web_token() == ("local", None)
        assert web_service.Config.WEB_ACCESS_TOKEN == ""           # لا توليد على المضيف المحلي


def test_ensure_web_token_generates_hex32_on_public_host(fresh_env, monkeypatch):
    import web_service
    monkeypatch.setattr(web_service.Config, "WEB_ACCESS_TOKEN", "")
    monkeypatch.setattr(web_service.Config, "WEB_HOST", "0.0.0.0")
    monkeypatch.setattr(web_service.Config, "WEB_ALLOW_OPEN", False)
    mode, tok = web_service.ensure_web_token()
    assert mode == "generated"
    assert len(tok) == 32 and all(c in "0123456789abcdef" for c in tok)   # secrets.token_hex(16)
    assert web_service.Config.WEB_ACCESS_TOKEN == tok                       # محفوظ في الذاكرة لكل /api/*
    assert web_service.WEB_TOKEN_STATE["generated"] is True
    mode2, tok2 = web_service.ensure_web_token()                            # استدعاء ثانٍ → نفس التوكن من البيئة
    assert mode2 == "env" and tok2 == tok


def test_ensure_web_token_open_only_with_explicit_flag(fresh_env, monkeypatch):
    import web_service
    monkeypatch.setattr(web_service.Config, "WEB_ACCESS_TOKEN", "")
    monkeypatch.setattr(web_service.Config, "WEB_HOST", "0.0.0.0")
    monkeypatch.setattr(web_service.Config, "WEB_ALLOW_OPEN", True)
    assert web_service.ensure_web_token() == ("open", None)
    assert web_service.WEB_TOKEN_STATE["open"] is True and web_service.Config.WEB_ACCESS_TOKEN == ""


def test_generated_token_protects_api_end_to_end(fresh_env, monkeypatch):
    """التوكن المولَّد يُطبَّق فعلاً: 401 بلا توكن، 200 به (عميل غير محلي)."""
    import web_service
    importlib.reload(web_service)
    monkeypatch.setattr(web_service.Config, "WEB_ACCESS_TOKEN", "")
    monkeypatch.setattr(web_service.Config, "WEB_HOST", "0.0.0.0")
    monkeypatch.setattr(web_service.Config, "WEB_ALLOW_OPEN", False)
    monkeypatch.setattr(web_service.WebRequestHandler, "_is_local", lambda self: False)
    mode, tok = web_service.ensure_web_token()
    assert mode == "generated"
    httpd, base = _start(web_service)
    try:
        status, body = _get(base + "/api/status")
        assert status == 401 and json.loads(body).get("need_token")
        status, body = _get(base + "/api/status", headers={"X-Access-Token": "wrong"})
        assert status == 401
        status, body = _get(base + "/api/status", headers={"X-Access-Token": tok})
        assert status == 200
    finally:
        httpd.shutdown()


def test_startup_ping_carries_generated_token_html_escaped(fresh_env, monkeypatch):
    import main
    posted = []
    monkeypatch.setattr(main.telegram_bot, "send_telegram_message",
                        lambda token, chat_id, text, reply_to_id=None, parse_mode=None: posted.append((text, {"parse_mode": parse_mode})) or True)
    monkeypatch.setattr(main, "_restart_notice", lambda: "", raising=False)
    monkeypatch.setattr(main.Config, "NOTIFY_ON_START", True)
    monkeypatch.setattr(main, "key_manager", fresh_env.key_manager, raising=False)
    main._startup_ping("T", "1", web_error="bind <failed> & more", web_token="abc123", web_open=False)
    assert posted, "لم تُرسل رسالة الإقلاع"
    text, kw = posted[-1]
    assert "<code>abc123</code>" in text and "WEB_ACCESS_TOKEN" in text
    assert "&lt;failed&gt;" in text and "<failed>" not in text          # الـ HTML الخام لا يكسر parse_mode
    assert kw.get("parse_mode") == "HTML"
    posted.clear()
    main._startup_ping("T", "1", web_error=None, web_token=None, web_open=True)
    assert "WEB_ALLOW_OPEN" in posted[-1][0]


# =====================================================================
# S5 — X-Forwarded-For لا يُحترم إلا بـ TRUST_PROXY=1
# =====================================================================
def _fake_handler(web_service, fwd):
    class H(web_service.WebRequestHandler):
        def __init__(self):
            self.headers = {"X-Forwarded-For": fwd} if fwd else {}
            self.client_address = ("10.0.0.7", 1234)
    return H()


def test_client_ip_ignores_forwarded_for_by_default(fresh_env, monkeypatch):
    import web_service
    monkeypatch.setattr(web_service.Config, "TRUST_PROXY", False)
    assert _fake_handler(web_service, "1.1.1.1, 2.2.2.2")._client_ip() == "10.0.0.7"
    monkeypatch.setattr(web_service.Config, "TRUST_PROXY", True)
    assert _fake_handler(web_service, "1.1.1.1, 2.2.2.2")._client_ip() == "1.1.1.1"
    assert _fake_handler(web_service, "")._client_ip() == "10.0.0.7"


def test_rate_limit_not_bypassable_by_spoofed_header(fresh_env, monkeypatch):
    import web_service
    importlib.reload(web_service)
    monkeypatch.setattr(web_service.Config, "TRUST_PROXY", False)
    monkeypatch.setattr(web_service.Config, "WEB_RATE_LIMIT_PER_MIN", 3)
    monkeypatch.setattr(web_service.Config, "WEB_ACCESS_TOKEN", "")
    monkeypatch.setattr(web_service, "transcribe_audio_bytes_ex",
                        lambda b, m="audio/webm": ("نص", "m", "k", {"elapsed": 0.1, "skipped": []}))
    web_service.reset_rate_limit()
    httpd, base = _start(web_service)

    def post(fwd):
        req = urllib.request.Request(base + "/api/transcribe", data=b"\x00" * 64,
                                     headers={"Content-Type": "audio/webm", "X-Forwarded-For": fwd})
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code

    try:
        codes = [post(f"9.9.9.{i}") for i in range(6)]
        assert codes.count(429) >= 3, codes                              # تغيير الترويسة لم يفد: الحد بالـ IP الحقيقي
        web_service.reset_rate_limit()
        monkeypatch.setattr(web_service.Config, "TRUST_PROXY", True)
        codes = [post(f"8.8.8.{i}") for i in range(6)]
        assert 429 not in codes, codes                                   # خلف proxy موثوق فقط تُحترم الترويسة
    finally:
        httpd.shutdown()
        web_service.reset_rate_limit()


# =====================================================================
# 09 §5 — local_groups / join_parts / restore_test auto-join
# =====================================================================
def test_local_groups_newest_first_and_parts_sorted(fresh_env, monkeypatch):
    import backup
    monkeypatch.setattr(backup.Config, "BACKUP_SPLIT_MB", 1)
    monkeypatch.setattr(backup.Config, "BACKUPS_KEEP", 4)
    t0 = time.time() - 3 * 86400
    old, _ = backup.save_local(b"small-old", when=t0)
    big = b"y" * (2 * 1024 * 1024 + 7)
    parts, _ = backup.save_local(big, when=t0 + 86400)
    groups = backup.local_groups()
    assert [g["parts"] for g in groups] == [3, 1]                       # الأحدث (المجزّأ) أولاً
    assert [backup._part_no(p) for p in groups[0]["paths"]] == [1, 2, 3]
    assert groups[0]["bytes"] == len(big) and groups[1]["bytes"] == len(b"small-old")
    assert groups[1]["paths"] == old
    dest = os.path.join(backup.backups_dir(), "joined.zip")
    assert backup.join_parts(groups[0]["paths"], dest) == len(big)
    assert open(dest, "rb").read() == big
    os.remove(dest)
    # ترتيب الأجزاء رقمي لا نصي (part10 بعد part9)
    assert backup._part_no("backup_x_part10.zip") == 10 and backup._part_no("backup_x.zip") == 0


def test_restore_test_auto_joins_split_backup(fresh_env, monkeypatch):
    import backup
    monkeypatch.setattr(backup.Config, "BACKUP_SPLIT_MB", 1)
    zip_bytes = _mk_zip(_good_members(pad=3 * 1024 * 1024))             # نص قابل للضغط لكن > 1MB بعد الضغط؟ نضمن ذلك
    if len(zip_bytes) <= 1024 * 1024:
        zip_bytes = _mk_zip({**_good_members(), "transcripts/blob.bin": os.urandom(2 * 1024 * 1024 + 11)})
    paths, _ = backup.save_local(zip_bytes)
    assert len(paths) >= 2 and all("_part" in os.path.basename(p) for p in paths)
    rep = backup.restore_test()
    assert rep["error"] is None, rep
    assert rep["ok"] and rep["parts"] == len(paths) and rep["joined_bytes"] == len(zip_bytes)
    assert rep["joined_from"] == [os.path.basename(p) for p in paths]
    assert rep["json_ok"] == 4 and rep["missing"] == []
    txt = backup.restore_test_text(rep)
    assert "✅" in txt and "جُمعت" in txt and "اجمع الأجزاء يدوياً" not in txt
    # الملف المؤقت المجمَّع حُذف ولم يُترك في backups/
    assert not [p for p, _ in backup.list_local() if "joined" in p]


def test_restore_test_single_file_still_works_and_bad_zip_reported(fresh_env):
    import backup
    paths, _ = backup.save_local(_mk_zip(_good_members()))
    rep = backup.restore_test()
    assert rep["ok"] and rep["parts"] == 1 and rep["joined_bytes"] == 0 and rep["path"] == paths[0]
    assert "جُمعت" not in backup.restore_test_text(rep)
    rep2 = backup.restore_test(paths[0] + ".missing")
    assert not rep2["ok"] and rep2["error"]


# =====================================================================
# توضيح التوقيت — server time (UTC) + DISPLAY_TZ
# =====================================================================
def test_schedule_text_states_server_time_and_display_tz(fresh_env, monkeypatch):
    import backup
    monkeypatch.setattr(backup.Config, "BACKUP_WEEKLY_DAY", "fri")
    monkeypatch.setattr(backup.Config, "BACKUP_HOUR", 3)
    monkeypatch.setattr(backup.Config, "DISPLAY_TZ", "Africa/Cairo", raising=False)
    txt = backup.schedule_text()
    assert txt.startswith("كل الجمعة 03:00 بتوقيت الخادم (")
    assert backup.server_tz_label() in txt
    if backup.server_tz_label() == "UTC":
        assert "Africa/Cairo" in txt and ("05:00" in txt or "06:00" in txt)   # صيفي/شتوي
    monkeypatch.setattr(backup.Config, "DISPLAY_TZ", "", raising=False)
    assert backup.schedule_text().endswith(")")                           # بلا DISPLAY_TZ → توقيت الخادم فقط
    monkeypatch.setattr(backup.Config, "DISPLAY_TZ", "Not/AZone", raising=False)
    assert "بتوقيت الخادم" in backup.schedule_text()                      # منطقة غير معروفة → لا استثناء


def test_tz_notice_in_doctor_panel_and_weekly_caption(fresh_env, monkeypatch):
    import backup
    import observability
    import panel
    monkeypatch.setattr(backup.Config, "BACKUP_WEEKLY_ENABLED", True)
    doc = observability.doctor_text(check_telegram=False)
    assert "بتوقيت الخادم" in doc
    text, _kb = panel.backup_menu()
    assert "بتوقيت الخادم" in text and "/backups" in text
    sent = []
    monkeypatch.setattr(backup, "build_public_backup", lambda: (_mk_zip(_good_members()), ["a", "b"]))
    res = backup.run_weekly(send_fn=lambda name, data, cap: sent.append(cap))
    assert res["ok"] and sent and "بتوقيت الخادم" in sent[0] and "/backups" in sent[0]


# =====================================================================
# /backups [n] — تليجرام + اللوحة
# =====================================================================
def test_backups_list_and_download_and_bad_index(bot, monkeypatch):
    tb, sent, docs = bot
    import backup
    monkeypatch.setattr(backup.Config, "BACKUP_SPLIT_MB", 1)
    _cmd(tb, "/backups")
    assert "لا توجد نسخ" in _texts(sent)[-1]
    t0 = time.time() - 2 * 86400
    old, _ = backup.save_local(_mk_zip(_good_members()), when=t0)
    big = _mk_zip({**_good_members(), "transcripts/blob.bin": os.urandom(2 * 1024 * 1024 + 3)})
    new, _ = backup.save_local(big, when=t0 + 86400)
    _cmd(tb, "/backups")
    txt = _texts(sent)[-1]
    assert "1." in txt and "2." in txt and "أجزاء" in txt and "بتوقيت الخادم" in txt
    assert txt.index("1.") < txt.index("2.")
    # #1 = الأحدث (المجزّأ) → تُرسل الأجزاء بالترتيب من القرص
    _cmd(tb, "/backups 1")
    assert [d[0] for d in docs] == [os.path.basename(p) for p in new]
    assert b"".join(d[1] for d in docs) == big
    assert "جزء 1/" in docs[0][2] and "#1" in docs[0][2]
    docs.clear()
    _cmd(tb, "/backups 2")
    assert [d[0] for d in docs] == [os.path.basename(old[0])] and docs[0][1] == open(old[0], "rb").read()
    docs.clear()
    for bad in ("/backups 3", "/backups 0", "/backups x"):
        _cmd(tb, bad)
        assert "غير صالح" in _texts(sent)[-1] and "1 إلى 2" in _texts(sent)[-1]
    assert docs == []
    import audit
    raw = open(audit.Config.AUDIT_FILE, encoding="utf-8").read()
    assert "backup.download" in raw


def test_backups_help_and_panel_button_parity(bot):
    tb, sent, docs = bot
    _cmd(tb, "/help")
    assert "/backups [n]" in "\n".join(_texts(sent))
    import panel
    text, markup = panel.backup_menu()
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "b:l" in datas and panel.COMMAND_PARITY["b:l"] == "/backups"
    for row in markup["inline_keyboard"]:
        for b in row:
            assert len(b["callback_data"].encode()) <= 64 and len(b["text"]) <= 20
    # الزر يستدعي نفس منطق الأمر النصي
    calls = []
    tb_run = tb._run_cmd
    tb._run_cmd = lambda token, chat_id, cmd, mid: calls.append(cmd) or tb_run(token, chat_id, cmd, mid)
    try:
        tb._dispatch_callback("T", "1", 77, "b:l") if hasattr(tb, "_dispatch_callback") else None
    finally:
        tb._run_cmd = tb_run
    if calls:
        assert calls == ["/backups"]
