# -*- coding: utf-8 -*-
"""
المرحلة 6 — لوحة التحكم (11 §8 DoD):
 1. كل callback_data ≤ 64 بايت (اختبار يولّد كل الأزرار).   2. Buttons = Commands (parity على جدول الربط).
 3. FSM تنتهي بعد TTL؛ فويس أثناءها لا يكسرها.               4. /set يرفض الممنوع والقيم خارج المدى.
 5. answerCallbackQuery لكل callback حتى عند الخطأ.          + setMyCommands، editMessageText، التأكيد المزدوج، الترقيم.
كله بلا شبكة.
"""
import importlib
import json

import pytest

VALID_A = "AIzaSy" + "A" * 33
VALID_B = "AIzaSy" + "B" * 33


class FakeResp:
    def __init__(self, status, payload=None, text=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = text if text is not None else json.dumps(self._payload)
        self.headers = {}

    def json(self):
        return self._payload


@pytest.fixture
def bot(fresh_env, monkeypatch):
    import sessions
    sessions.reset_cache()
    import fsm
    fsm.clear()
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


def _calls(sent, method):
    return [x["payload"] for x in sent if x["method"] == method]


def _texts(sent):
    return [x["payload"].get("text", "") for x in sent if x["method"] == "sendMessage"]


def _cmd(tb, text):
    tb.handle_text("T", "1", {"message_id": 1}, text)


def _cb(tb, data, mid=77):
    return tb.handle_callback("T", {"id": "cq1", "data": data, "message": {"message_id": mid, "chat": {"id": 1}}})


def _buttons(markup):
    return [b for row in markup["inline_keyboard"] for b in row]


# =====================================================================
# DoD 1 — كل callback_data ≤ 64 بايت ASCII · DoD 2 — parity · 11 §6 نص الزر ≤ 20
# =====================================================================
def test_all_buttons_fit_64_bytes_and_have_command_parity(fresh_env):
    import panel
    import sessions
    import glossary
    sessions.reset_cache()
    fresh_env.key_manager.add_key(VALID_A, "Alpha-with-a-very-long-project-name")
    sessions.start("جلسة طويلة الاسم جداً جداً جداً")
    glossary.get_glossary().add("زوبعة", "ZubaaTool")
    total = 0
    for text, markup in panel.all_screens() + [panel.key_view(0), panel.key_view(0, True), panel.session_view(sessions.current()["id"]),
                                                panel.glossary_view("g_001") if glossary.get_glossary().find("g_001") else panel.glossary_list(1),
                                                panel.roles_list(confirm_delete="x")]:
        assert text and markup["inline_keyboard"]
        for b in _buttons(markup):
            total += 1
            d = b["callback_data"]
            assert len(d.encode("utf-8")) <= 64 and d.isascii(), d
            assert len(b["text"]) <= 20, b["text"]
            assert panel.parity_key(d) in panel.COMMAND_PARITY, d
    assert total > 60


def test_btn_rejects_invalid_callback_data(fresh_env):
    import panel
    with pytest.raises(ValueError):
        panel.btn("x", "a" * 65)
    with pytest.raises(ValueError):
        panel.btn("x", "ع:1")
    assert panel.btn("x" * 40, "p:home")["text"].endswith("…")


def test_pagination_five_per_page(fresh_env):
    import panel
    import glossary
    gl = glossary.get_glossary()
    base = len(gl.entries())
    for i in range(12):
        gl.add(f"كلمة{i}", f"Word{i}")
    n = base + 12
    pages = (n + 4) // 5
    text, markup = panel.glossary_list(1)
    assert f"صفحة 1/{pages}" in text and len([b for b in _buttons(markup) if b["callback_data"].startswith("g:v:")]) == 5
    text, markup = panel.glossary_list(99)        # تُقصّ إلى الأخيرة
    assert f"صفحة {pages}/{pages}" in text
    assert any(b["callback_data"] == f"g:l:{pages - 1}" for b in _buttons(markup))
    assert any(b["callback_data"] == "g:l:2" for b in _buttons(panel.glossary_list(1)[1]))


# =====================================================================
# DoD 5 — answerCallbackQuery دائماً · editMessageText بدل رسائل جديدة
# =====================================================================
def test_callback_always_answered_even_on_error(bot, monkeypatch):
    tb, sent, _ = bot
    _cb(tb, "p:home")
    assert len(_calls(sent, "answerCallbackQuery")) == 1 and len(_calls(sent, "editMessageText")) == 1
    assert _calls(sent, "editMessageText")[0]["message_id"] == 77 and "لوحة التحكم" in _calls(sent, "editMessageText")[0]["text"]
    note = _cb(tb, "zz:unknown")
    assert "غير معروف" in note and len(_calls(sent, "answerCallbackQuery")) == 2
    import panel
    monkeypatch.setattr(panel, "keys_list", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    note = _cb(tb, "k:l:1")
    assert "خطأ" in note and len(_calls(sent, "answerCallbackQuery")) == 3
    assert all(c["callback_query_id"] == "cq1" for c in _calls(sent, "answerCallbackQuery"))


def test_navigation_edits_same_message_not_new_ones(bot):
    tb, sent, _ = bot
    for d in ("k:l:1", "m:menu", "r:l", "s:l:1", "g:l:1", "c:menu", "L:menu", "b:menu", "S:l", "p:home"):
        _cb(tb, d, mid=42)
    edits = _calls(sent, "editMessageText")
    assert len(edits) == 10 and all(e["message_id"] == 42 for e in edits)
    assert not _calls(sent, "sendMessage")


def test_panel_command_sends_home_with_keyboard(bot):
    tb, sent, _ = bot
    _cmd(tb, "/panel")
    p = _calls(sent, "sendMessage")[-1]
    assert "reply_markup" in p and "لوحة التحكم" in p["text"]
    _cmd(tb, "/help")
    assert "/panel" in _texts(sent)[-1] and "/set" in _texts(sent)[-1] and "/cancel" in _texts(sent)[-1]


# =====================================================================
# Buttons = Commands — الأفعال الفعلية تمر بنفس منطق الأوامر + audit actor=telegram
# =====================================================================
def test_mode_header_role_context_buttons_apply(bot):
    tb, sent, _ = bot
    import settings
    import sessions
    import audit
    _cb(tb, "m:raw_only")
    assert settings.delivery_mode() == "raw_only"
    _cb(tb, "h:off")
    assert settings.header_target() == "off"
    _cb(tb, "r:s:doctor") if __import__("prompts").get_role("doctor") else None
    rid = next(iter(__import__("prompts").all_roles()))
    _cb(tb, f"r:s:{rid}")
    assert settings.role() == rid
    sessions.start("أ")
    _cb(tb, "c:simple")
    assert sessions.context_mode() == "simple"
    _cb(tb, "c:auto")
    assert sessions.context_mode() == "full"
    assert any("actor=telegram" in ln and "settings.delivery_mode" in ln for ln in audit.tail(20))
    edit = _calls(sent, "editMessageText")[0]
    assert "◉ خام" in json.dumps(edit["reply_markup"], ensure_ascii=False)


def test_key_delete_requires_double_confirm_in_same_message(bot):
    tb, sent, _ = bot
    km = tb.key_manager
    km.add_key(VALID_A, "Alpha")
    _cb(tb, "k:d:0", mid=9)
    e = _calls(sent, "editMessageText")[-1]
    assert e["message_id"] == 9 and "متأكد" in json.dumps(e["reply_markup"], ensure_ascii=False)
    assert len(km.data["keys"]) == 1                      # لم يُحذف بعد
    _cb(tb, "k:v:0", mid=9)                              # إلغاء → يرجع للعرض
    assert len(km.data["keys"]) == 1
    _cb(tb, "k:d:0", mid=9)
    note = _cb(tb, "k:D:0", mid=9)
    assert "حُذف" in note and len(km.data["keys"]) == 0
    assert any("تم حذف المفتاح" in t for t in _texts(sent))   # نفس رد الأمر النصي /remove


def test_glossary_confirm_and_delete_buttons(bot):
    tb, sent, _ = bot
    import glossary
    gl = glossary.get_glossary()
    e, _c = gl.add("زوبعة", "ZubaaTool", status="pending")
    gid = e["id"]
    _cb(tb, f"g:c:{gid}")
    assert gl.find(gid)["status"] == "confirmed"
    _cb(tb, f"g:d:{gid}")
    assert gl.find(gid) is not None
    _cb(tb, f"g:D:{gid}")
    assert gl.find(gid) is None


def test_sessions_buttons_end_resume_export(bot):
    tb, sent, docs = bot
    import sessions
    s = sessions.start("أ")
    sessions.append_entry("نص تجريبي")
    _cb(tb, "s:e")
    assert sessions.current() is None
    _cb(tb, f"s:r:{s['id']}")
    assert sessions.current()["id"] == s["id"]
    _cb(tb, f"s:x:{s['id']}")
    assert docs and docs[-1][0].startswith("session_")


def test_backup_buttons_and_secrets_double_confirm(bot, tmp_path):
    tb, sent, docs = bot
    import backup
    (tmp_path / "keys.json").write_text('{"keys": []}', encoding="utf-8")
    _cb(tb, "b:s")
    assert not docs and "keys.json" in _calls(sent, "editMessageText")[-1]["text"]   # تأكيد أولاً — لا إرسال
    _cb(tb, "b:S")
    assert docs and docs[-1][0].startswith("secrets_")
    _cb(tb, "b:n")
    assert backup.list_local() or any("لا توجد بيانات" in t for t in _texts(sent))
    _cb(tb, "b:t")
    assert any("تمرين الاستعادة" in t for t in _texts(sent))


def test_status_doctor_logs_buttons_run_commands(bot, monkeypatch):
    tb, sent, _ = bot
    _cb(tb, "p:st")
    assert any("المفاتيح" in t or "لا توجد مفاتيح" in t for t in _texts(sent))
    _cb(tb, "L:e")
    assert _texts(sent)
    _cb(tb, "L:a")
    assert any("التدقيق" in t for t in _texts(sent))
    note = _cb(tb, "L:r")
    assert "لا فويس" in note


# =====================================================================
# DoD 3 — FSM: TTL، /cancel، الفويس لا يكسرها، زر الإلغاء
# =====================================================================
def test_fsm_add_word_two_steps_then_cleared(bot):
    tb, sent, _ = bot
    import fsm
    import glossary
    _cb(tb, "g:a")
    assert fsm.current()["name"] == "await_word"
    last = _calls(sent, "sendMessage")[-1]
    assert "إلغاء" in json.dumps(last["reply_markup"], ensure_ascii=False) and "/cancel" in last["text"]
    _cmd(tb, "زوبعة")
    assert fsm.current()["name"] == "await_word_dst"
    _cmd(tb, "ZubaaTool")
    assert fsm.current() is None
    assert glossary.get_glossary().find("زوبعة")["replacement"] == "ZubaaTool"


def test_fsm_ttl_expires_with_single_notice(bot):
    tb, sent, _ = bot
    import fsm
    import clock
    fc = clock.FakeClock()
    clock.use(fc)
    try:
        _cb(tb, "s:n")
        assert fsm.active()
        fc.advance(fsm.Config.FSM_TTL_SECONDS + 1)
        assert not fsm.active()
        _cmd(tb, "اسم متأخر")
        assert any("انتهت مهلة" in t for t in _texts(sent))
        n = sum("انتهت مهلة" in t for t in _texts(sent))
        _cmd(tb, "نص آخر")
        assert sum("انتهت مهلة" in t for t in _texts(sent)) == n     # مرة واحدة فقط
        import sessions
        assert sessions.current() is None                            # لم تُنشأ جلسة بالاسم المتأخر
    finally:
        clock.reset()


def test_fsm_cancel_command_and_button(bot):
    tb, sent, _ = bot
    import fsm
    _cb(tb, "k:a")
    assert fsm.active()
    _cmd(tb, "/cancel")
    assert not fsm.active() and "أُلغيت" in _texts(sent)[-1]
    _cmd(tb, "/cancel")
    assert "لا عملية" in _texts(sent)[-1]
    _cb(tb, "r:n")
    assert fsm.active()
    note = _cb(tb, "x:cancel", mid=55)
    assert "أُلغي" in note and not fsm.active() and _calls(sent, "deleteMessage")[-1]["message_id"] == 55


def test_fsm_voice_during_state_does_not_break_it(bot, monkeypatch):
    tb, sent, _ = bot
    import fsm
    import voice_queue
    _cb(tb, "s:N")
    import sessions
    sessions.start("أ")
    assert fsm.current()["name"] == "await_session_name"
    monkeypatch.setattr(voice_queue.VoiceQueue, "submit", lambda self, job: job, raising=False)
    monkeypatch.setattr(tb, "QUEUE", tb.QUEUE or voice_queue.VoiceQueue(lambda j: None), raising=False)
    msg = {"message_id": 5, "chat": {"id": 1}, "date": 1, "voice": {"file_id": "f1", "file_unique_id": "u1", "duration": 3}}
    try:
        tb.enqueue_telegram_audio("T", "1", msg, "f1", "audio/ogg", "voice")
    except Exception:
        pass
    assert fsm.current()["name"] == "await_session_name"            # الفويس لم يمس الحالة
    _cmd(tb, "اسم جديد")
    assert sessions.current()["name"] == "اسم جديد" and not fsm.active()


def test_fsm_other_command_cancels_silently_and_runs(bot):
    tb, sent, _ = bot
    import fsm
    _cb(tb, "k:a")
    _cmd(tb, "/status")
    assert not fsm.active() and _texts(sent)


def test_fsm_key_paste_and_rename(bot):
    tb, sent, _ = bot
    import fsm
    km = tb.key_manager
    _cb(tb, "k:a")
    _cmd(tb, f"Proj X: {VALID_A}")
    assert not fsm.active() and km.data["keys"][0]["name"] == "Proj X"
    _cb(tb, "k:n:0")
    assert fsm.current()["name"] == "await_key_rename"
    _cmd(tb, "Renamed")
    assert km.data["keys"][0]["name"] == "Renamed" and not fsm.active()
    # /rename النصي مع اسم فيه مسافة (الأسماء الافتراضية «Project N»)
    km.add_key(VALID_B)                                   # → Project 2
    _cmd(tb, "/rename Project 2 Beta")
    assert km.data["keys"][1]["name"] == "Beta"
    _cmd(tb, '/rename "Beta" Gamma Delta')
    assert km.data["keys"][1]["name"] == "Gamma Delta"
    _cb(tb, "k:a")
    _cmd(tb, "كلام بلا مفتاح")
    assert any("لم أجد مفتاح" in t for t in _texts(sent)) and not fsm.active()


# =====================================================================
# DoD 4 — /set: القائمة البيضاء، الممنوع، المدى، التجاوز على Config، الاستمرارية
# =====================================================================
def test_set_rejects_forbidden_unknown_and_out_of_range(bot):
    tb, sent, _ = bot
    import panel
    for bad in ("/set token abc", "/set web_access_token x", "/set data_dir /tmp", "/set models a", "/set transcribe_workers 4", "/set gemini_api_key x"):
        _cmd(tb, bad)
        assert "ممنوع" in _texts(sent)[-1], bad
    _cmd(tb, "/set nothing 1")
    assert "غير معروف" in _texts(sent)[-1]
    _cmd(tb, "/set log_retention_days 0")
    assert "خارج المدى" in _texts(sent)[-1]
    _cmd(tb, "/set log_retention_days 366")
    assert "خارج المدى" in _texts(sent)[-1]
    _cmd(tb, "/set log_retention_days abc")
    assert "رقماً" in _texts(sent)[-1]
    _cmd(tb, "/set delivery_mode weird")
    assert "غير صالحة" in _texts(sent)[-1]
    assert panel.Config.LOG_RETENTION_DAYS == 90


def test_set_applies_persists_and_audits(bot):
    tb, sent, _ = bot
    import panel
    import settings
    import audit
    _cmd(tb, "/set log_retention_days 30")
    assert "90 → 30" in _texts(sent)[-1] and panel.Config.LOG_RETENTION_DAYS == 30
    _cmd(tb, "/set backup_weekly_day الجمعة")
    assert panel.Config.BACKUP_WEEKLY_DAY == "fri"
    _cmd(tb, "/set notify_on_start off")
    assert panel.Config.NOTIFY_ON_START is False
    _cmd(tb, "/set delivery_mode raw")
    assert settings.delivery_mode() == "raw_only"
    _cmd(tb, "/set panel_page_size 3")
    assert panel.page_size() == 3
    acts = [ln.split(" | ")[1] for ln in audit.tail(20)]
    assert "settings.log_retention_days" in acts and "settings.notify_on_start" in acts
    # الاستمرارية: reload من القرص ثم apply_persisted_overrides يعيد القيم على Config
    settings.reset_cache()
    panel.Config.LOG_RETENTION_DAYS = 90
    panel.Config.NOTIFY_ON_START = True
    assert settings.get("log_retention_days") == 30
    assert panel.apply_persisted_overrides() >= 3
    assert panel.Config.LOG_RETENTION_DAYS == 30 and panel.Config.NOTIFY_ON_START is False and panel.Config.PANEL_PAGE_SIZE == 3
    _cmd(tb, "/set")
    assert "المسموح" in _texts(sent)[-1] and "log_retention_days = 30" in _texts(sent)[-1]


def test_settings_panel_choice_and_int_fsm(bot):
    tb, sent, _ = bot
    import fsm
    import panel
    _cb(tb, "S:v:delivery_mode")
    e = _calls(sent, "editMessageText")[-1]
    assert "S:s:delivery_mode=raw_only" in json.dumps(e["reply_markup"])
    _cb(tb, "S:s:delivery_mode=polished_only")
    import settings
    assert settings.delivery_mode() == "polished_only"
    _cb(tb, "S:e:progress_after_seconds")
    assert fsm.current()["name"] == "await_set_value"
    _cmd(tb, "500")
    assert "خارج المدى" in _texts(sent)[-1] and panel.Config.PROGRESS_AFTER_SECONDS == 12 and not fsm.active()
    _cb(tb, "S:e:progress_after_seconds")
    _cmd(tb, "20")
    assert panel.Config.PROGRESS_AFTER_SECONDS == 20


# =====================================================================
# setMyCommands (11 §5) + allowed_updates (11 §1)
# =====================================================================
def test_register_commands_short_list(bot, monkeypatch):
    tb, sent, _ = bot
    assert len(tb.BOT_COMMANDS) <= 12 and tb.BOT_COMMANDS[0][0] == "panel"
    assert tb.register_commands("T") is True
    p = _calls(sent, "setMyCommands")[-1]
    assert len(p["commands"]) == len(tb.BOT_COMMANDS) and all(c["command"].isascii() and c["description"] for c in p["commands"])
    monkeypatch.setattr(tb.Config, "SET_MY_COMMANDS", False)
    assert tb.register_commands("T") is False
    monkeypatch.setattr(tb, "_http_post", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr(tb.Config, "SET_MY_COMMANDS", True)
    assert tb.register_commands("T") is False     # لا استثناء


def test_poller_requests_callback_query_updates(fresh_env, monkeypatch):
    import telegram_bot
    importlib.reload(telegram_bot)
    telegram_bot.key_manager = fresh_env.key_manager
    seen = {}

    def fake_get(url, params=None, timeout=None, **kw):
        seen["params"] = params
        raise KeyboardInterrupt
    monkeypatch.setattr(telegram_bot, "_http_get", fake_get)
    monkeypatch.setattr(fresh_env.key_manager, "get_telegram_config", lambda: ("T", "u", "1"))
    import threading
    try:
        telegram_bot.main(stop_event=threading.Event())
    except KeyboardInterrupt:
        pass
    assert "callback_query" in seen["params"]["allowed_updates"] and "message" in seen["params"]["allowed_updates"]


def test_config_defaults_phase6(fresh_env):
    from config import Config
    assert Config.FSM_TTL_SECONDS == 300 and Config.CALLBACK_DATA_MAX == 64 and Config.PANEL_BUTTON_MAX_CHARS == 20 and Config.PANEL_PAGE_SIZE == 5
