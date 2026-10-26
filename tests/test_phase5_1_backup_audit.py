# -*- coding: utf-8 -*-
"""
المرحلة 5.1 — إغلاق نواقص 09/12: قناة audit.log (09 §2، 11 §4)، النسخ الأسبوعي التلقائي + backups/ آخر 4 +
تقسيم > 50MB + /restore_test (09 §5)، وفحص getMe في /doctor (12 §3.1) — كله بلا شبكة.
"""
import importlib
import io
import json
import os
import time
import zipfile

import pytest

VALID_A = "AIzaSy" + "A" * 33
VALID_B = "AIzaSy" + "B" * 33


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = json.dumps(self._payload)
        self.headers = {}

    def json(self):
        return self._payload


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


def _seed_data(tmp_path):
    os.makedirs(tmp_path / "transcripts", exist_ok=True)
    (tmp_path / "transcripts" / "ALL.md").write_text("# ALL\n", encoding="utf-8")
    (tmp_path / "glossary.json").write_text('{"entries": []}', encoding="utf-8")
    (tmp_path / "settings.json").write_text('{"delivery_mode": "dual"}', encoding="utf-8")


# =====================================================================
# audit.log — 09 §2 / 11 §4
# =====================================================================
def test_audit_records_key_lifecycle_redacted(fresh_env):
    import audit
    km = fresh_env.key_manager
    km.add_key(VALID_A, "Alpha")
    km.rename_key("Alpha", "Beta")
    km.set_project_group("Beta", "proj1")
    km.remove_key("Beta")
    raw = open(audit.Config.AUDIT_FILE, encoding="utf-8").read()
    assert VALID_A not in raw                                   # المفتاح الكامل لا يصل إلى القرص أبداً
    acts = [ln.split(" | ")[1] for ln in raw.strip().splitlines()]
    assert acts == ["key.add", "key.rename", "key.project_group", "key.remove"]
    assert "actor=system" in raw and "old=Alpha | new=Beta" in raw and "group=proj1" in raw


def test_audit_records_settings_roles_glossary_session(fresh_env):
    import audit
    import settings
    import prompts
    import glossary
    import sessions
    sessions.reset_cache()
    settings.set_delivery_mode("raw")
    settings.set_delivery_mode("raw")                           # لا تغيير → لا سطر مكرر
    prompts.add_role("lawyer", "محامي", "", "نظّم قانونياً")
    prompts.remove_role("lawyer")
    gl = glossary.get_glossary()
    e, _ = gl.add("زوبعة", "ZubaaTool")
    gl.add("زوبعه", "ZubaaTool")                                # نمط إضافي لنفس المدخل
    gl.confirm(e["id"])
    gl.remove(e["id"])
    sessions.start("أ")
    sessions.set_role_override("doctor")
    acts = [ln.split(" | ")[1] for ln in audit.tail(50)]
    assert acts.count("settings.delivery_mode") == 1
    for a in ("role.add", "role.remove", "glossary.add", "glossary.add_pattern", "glossary.confirm", "glossary.remove", "session.role_override"):
        assert a in acts, a
    raw = open(audit.Config.AUDIT_FILE, encoding="utf-8").read()
    assert "old=dual | new=raw" in raw


def test_audit_actor_and_cap_and_tail(fresh_env, monkeypatch):
    import audit
    audit.set_actor("telegram")
    try:
        audit.record("x.y", a="1\nخط\nجديد", b=None, c="z" * 200)
    finally:
        audit.set_actor(None)
    ln = audit.tail(1)[0]
    assert "actor=telegram" in ln and "\n" not in ln and "b=" not in ln and "..." in ln
    # سقف الأمان: فوق AUDIT_MAX_BYTES يُبقي النصف الأخير فقط (القناة لا تُدوَّر)
    monkeypatch.setattr(audit, "AUDIT_MAX_BYTES", 2000)
    for i in range(200):
        audit.record("fill", i=i)
    assert audit.size_bytes() <= 2000 * 1.1
    lines = audit.tail(1000)
    assert lines[-1].endswith("i=199") and len(lines) < 200


def test_audit_command_and_telegram_actor(bot):
    tb, sent, _docs = bot
    _cmd(tb, "/audit")
    assert "فارغ" in _texts(sent)[-1]
    _cmd(tb, "/mode raw")
    _cmd(tb, "/audit 5")
    last = _texts(sent)[-1]
    assert "settings.delivery_mode" in last and "actor=telegram" in last and VALID_A not in last


def test_help_mentions_new_commands(bot):
    tb, sent, _docs = bot
    _cmd(tb, "/help")
    h = _texts(sent)[-1]
    assert "/restore_test" in h and "/audit" in h


# =====================================================================
# النسخ المحلية + الاحتفاظ بآخر 4 + التقسيم — 09 §5
# =====================================================================
def test_save_local_keeps_last_4_and_prunes_oldest(fresh_env, tmp_path):
    import backup
    t0 = time.time() - 10 * 86400
    paths = []
    for i in range(6):
        p, removed = backup.save_local(b"PK\x05\x06" + bytes(18), when=t0 + i * 86400)
        paths.extend(p)
    local = backup.list_local()
    assert len(local) == 4
    names = sorted(os.path.basename(p) for p, _ in local)
    assert os.path.basename(paths[0]) not in names and os.path.basename(paths[1]) not in names
    assert os.path.basename(paths[-1]) in names


def test_split_over_limit_and_part_groups_prune_together(fresh_env, monkeypatch):
    import backup
    monkeypatch.setattr(backup.Config, "BACKUP_SPLIT_MB", 1)
    big = b"x" * (2 * 1024 * 1024 + 5)
    assert len(backup.split_bytes(b"x" * 1024)) == 1
    parts = backup.split_bytes(big)
    assert len(parts) == 3 and b"".join(parts) == big
    monkeypatch.setattr(backup.Config, "BACKUPS_KEEP", 1)
    t0 = time.time() - 5 * 86400
    p1, _ = backup.save_local(big, when=t0)
    assert len(p1) == 3 and all("_part" in os.path.basename(p) for p in p1)
    p2, removed = backup.save_local(b"small", when=t0 + 86400)
    assert removed == 3 and [os.path.basename(p) for p, _ in backup.list_local()] == [os.path.basename(p2[0])]


# =====================================================================
# الجدولة الأسبوعية — 09 §5
# =====================================================================
def _at(wday, hour):
    """طابع زمني محلي ليوم أسبوع (0=الاثنين) وساعة معينة في الأسبوع الحالي."""
    now = time.time()
    lt = time.localtime(now)
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, 0, 0, 0, 0, -1))
    return base + (wday - lt.tm_wday) * 86400


def test_weekly_due_day_hour_and_once_per_week(fresh_env, monkeypatch):
    import backup
    monkeypatch.setattr(backup.Config, "BACKUP_WEEKLY_DAY", "fri")
    monkeypatch.setattr(backup.Config, "BACKUP_HOUR", 3)
    assert backup.weekday_index("الجمعة") == 4 and backup.weekday_index("3") == 3 and backup.weekday_index("junk") == 4
    assert not backup.weekly_due(_at(3, 3))        # الخميس
    assert not backup.weekly_due(_at(4, 2))        # الجمعة قبل الساعة
    assert backup.weekly_due(_at(4, 3))
    assert backup.weekly_due(_at(4, 15))           # catch-up في نفس اليوم
    backup.mark_done(_at(4, 3), [], ok=True)
    assert not backup.weekly_due(_at(4, 15))       # نُفِّذت هذا الأسبوع
    assert backup.weekly_due(_at(4, 3) + 7 * 86400)
    monkeypatch.setattr(backup.Config, "BACKUP_WEEKLY_ENABLED", False)
    assert not backup.weekly_due(_at(4, 3) + 7 * 86400)


def test_run_weekly_sends_saves_and_never_leaks_secrets(fresh_env, tmp_path):
    import backup
    import audit
    _seed_data(tmp_path)
    (tmp_path / "keys.json").write_text(json.dumps({"keys": [{"key": VALID_A}]}), encoding="utf-8")
    sent = []
    res = backup.run_weekly(send_fn=lambda n, d, c: sent.append((n, d, c)))
    assert res["ok"] and res["sent"] == 1 and len(res["paths"]) == 1 and os.path.exists(res["paths"][0])
    name, data, cap = sent[0]
    assert name.startswith("backup_") and "أسبوعية" in cap and "/restore_test" in cap
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert "keys.json" not in z.namelist() and "glossary.json" in z.namelist()
    st = backup.load_state()
    assert st["last_ok"] and st["last_week"] == backup._week_key(time.time())
    assert not backup.weekly_due()
    assert any("backup.weekly" in ln and "actor=scheduler" in ln for ln in audit.tail(5))


def test_run_weekly_failure_is_recorded_not_raised(fresh_env, tmp_path, monkeypatch):
    import backup
    _seed_data(tmp_path)
    monkeypatch.setattr(backup, "build_public_backup", lambda: (_ for _ in ()).throw(OSError("disk full")))
    res = backup.run_weekly(send_fn=lambda *a: None)
    assert not res["ok"] and "disk full" in res["error"]
    st = backup.load_state()
    assert st["last_ok"] is False and "disk full" in st["last_error"]


def test_run_weekly_without_data(fresh_env):
    import backup
    res = backup.run_weekly(send_fn=lambda *a: (_ for _ in ()).throw(AssertionError("must not send")))
    assert res["ok"] and res["sent"] == 0 and "لا بيانات" in res["error"]


def test_main_weekly_tick_sends_to_owner_and_notifies_on_failure(fresh_env, tmp_path, monkeypatch):
    import main
    import backup
    import telegram_bot
    _seed_data(tmp_path)
    docs, notes = [], []
    monkeypatch.setattr(telegram_bot, "_send_document", lambda token, chat_id, name, data, mime, cap, rid: docs.append((chat_id, name)))
    rt = main.Runtime("T", "123")
    rt.notifier = type("N", (), {"notify": lambda self, t: notes.append(t)})()
    monkeypatch.setattr(backup.Config, "BACKUP_WEEKLY_DAY", "fri")
    monkeypatch.setattr(backup.Config, "BACKUP_HOUR", 3)
    assert main._weekly_backup_tick(rt, "T", now=_at(3, 3)) is None           # ليس الموعد
    res = main._weekly_backup_tick(rt, "T", now=_at(4, 3))
    assert res["ok"] and docs and docs[0][0] == "123" and docs[0][1].startswith("backup_")
    assert main._weekly_backup_tick(rt, "T", now=_at(4, 4)) is None           # مرة واحدة في الأسبوع
    assert not notes
    # فشل → إبلاغ المالك سطراً (بلا استثناء)
    monkeypatch.setattr(backup, "build_public_backup", lambda: (_ for _ in ()).throw(OSError("boom")))
    res = main._weekly_backup_tick(rt, "T", now=_at(4, 3) + 7 * 86400)
    assert res is not None and not res["ok"] and notes and "فشلت" in notes[-1]


# =====================================================================
# /restore_test — 09 §5 fire drill
# =====================================================================
def test_restore_test_ok_and_reports(fresh_env, tmp_path):
    import backup
    _seed_data(tmp_path)
    zb, inc = backup.build_public_backup()
    paths, _ = backup.save_local(zb)
    rep = backup.restore_test()
    assert rep["ok"] and rep["members"] == len(inc) and rep["json_ok"] == 2 and not rep["missing"][:0]
    assert "roles.json" in rep["missing"]                      # لم يُنشأ بعد → تحذير لا فشل
    txt = backup.restore_test_text(rep)
    assert txt.startswith("✅") and "لم تُمس" in txt
    assert not any(d.startswith("restore_test_") for d in os.listdir(backup.tempfile.gettempdir()) if os.path.isdir(os.path.join(backup.tempfile.gettempdir(), d)) and d.startswith("restore_test_"))


def test_restore_test_detects_corruption_secrets_and_zip_slip(fresh_env, tmp_path):
    import backup
    assert "لا توجد نسخة" in backup.restore_test_text(backup.restore_test())
    os.makedirs(backup.Config.BACKUPS_DIR, exist_ok=True)
    bad = os.path.join(backup.Config.BACKUPS_DIR, "backup_2026-01-01_0000.zip")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("settings.json", "{not json")
        z.writestr("keys.json", "{}")
        z.writestr("../evil.txt", "x")
    rep = backup.restore_test(bad)
    assert not rep["ok"] and rep["json_bad"] == ["settings.json"] and rep["secrets"] == ["keys.json"] and rep["unsafe"] == ["../evil.txt"]
    assert not os.path.exists(os.path.join(os.path.dirname(backup.tempfile.gettempdir()), "evil.txt"))
    txt = backup.restore_test_text(rep)
    assert txt.startswith("❌") and "CRC" not in txt and "JSON تالف" in txt and "سرية" in txt and "خطرة" in txt
    # CRC تالف
    data = bytearray(open(bad, "rb").read())
    with zipfile.ZipFile(bad) as z:
        off = z.getinfo("settings.json").header_offset + 30 + len("settings.json")
    data[off] ^= 0xFF
    open(bad, "wb").write(bytes(data))
    rep = backup.restore_test(bad)
    assert not rep["ok"] and (rep["bad_crc"] or rep["error"])


def test_backup_command_saves_local_and_restore_test_command(bot, tmp_path):
    tb, sent, docs = bot
    import backup
    import audit
    _seed_data(tmp_path)
    _cmd(tb, "/restore_test")
    assert "لا توجد نسخة" in _texts(sent)[-1]
    _cmd(tb, "/backup")
    assert docs and docs[-1][0].startswith("backup_") and "backups/" in docs[-1][2]
    assert len(backup.list_local()) == 1
    _cmd(tb, "/restore_test")
    assert _texts(sent)[-1].startswith("✅ تمرين الاستعادة")
    acts = [ln.split(" | ")[1] for ln in audit.tail(10)]
    assert "backup.manual" in acts and "backup.restore_test" in acts


# =====================================================================
# /doctor getMe — 12 §3.1 (معزول بـ mock)
# =====================================================================
def test_doctor_getme_check_ok_slow_fail_and_offline(fresh_env, monkeypatch):
    import observability
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "_runtime_token", lambda: "T")
    calls = []

    def fake_get(url, timeout=None, **kw):
        calls.append((url, timeout))
        return FakeResp(200, {"ok": True, "result": {"username": "mybot"}})
    monkeypatch.setattr(telegram_bot, "_http_get", fake_get)
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None)
    tg = [ln for ln in lines if "تليجرام" in ln][0]
    assert tg.startswith("✅") and "getMe" in tg and "@mybot" in tg
    assert calls and calls[0][0].endswith("/getMe") and calls[0][1][1] == observability.Config.DOCTOR_TG_TIMEOUT
    # بطيء → ⚠️
    monkeypatch.setattr(observability.Config, "DOCTOR_TG_SLOW_MS", -1)
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None)
    assert [ln for ln in lines if "تليجرام" in ln][0].startswith("⚠️")
    # HTTP 401 → ❌
    monkeypatch.setattr(telegram_bot, "_http_get", lambda *a, **k: FakeResp(401, {}))
    lines, worst = observability.doctor_report(bot_state={"alive": False}, queue=None)
    assert [ln for ln in lines if "تليجرام" in ln][0].startswith("❌") and worst == observability.FAIL
    # استثناء شبكي → ⚠️ والتقرير يكتمل (12 §5 بند 2)
    monkeypatch.setattr(telegram_bot, "_http_get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None)
    assert [ln for ln in lines if "تليجرام" in ln][0].startswith("⚠️") and any("العملية" in ln for ln in lines)
    # بلا توكن → ⚠️
    monkeypatch.setattr(telegram_bot, "_runtime_token", lambda: "")
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None)
    assert "لا توكن" in [ln for ln in lines if "تليجرام" in ln][0]
    # check_telegram=False → لا نداء إطلاقاً (الفحص الدوري الصامت)
    monkeypatch.setattr(telegram_bot, "_http_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None, check_telegram=False)
    assert "لم يُنفَّذ" in [ln for ln in lines if "تليجرام" in ln][0]


def test_doctor_backup_check_and_sizes(fresh_env, monkeypatch, tmp_path):
    import observability
    import backup
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "_runtime_token", lambda: "")
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None)
    bk = [ln for ln in lines if "النسخ الاحتياطي" in ln][0]
    assert bk.startswith("✅") and "لم تُنفَّذ بعد" in bk and "الجمعة 03:00" in bk
    assert any("backups" in ln and "audit" in ln for ln in lines if "الأحجام" in ln)
    backup.mark_done(time.time() - 10 * 86400, [], ok=True)
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None)
    assert [ln for ln in lines if "النسخ الاحتياطي" in ln][0].startswith("⚠️")
    backup.mark_done(time.time(), [], ok=False, error="boom")
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None)
    assert [ln for ln in lines if "النسخ الاحتياطي" in ln][0].startswith("❌")
    monkeypatch.setattr(observability.Config, "BACKUP_WEEKLY_ENABLED", False)
    lines, _ = observability.doctor_report(bot_state={"alive": False}, queue=None)
    assert "معطّل" in [ln for ln in lines if "النسخ الاحتياطي" in ln][0]


def test_periodic_doctor_never_touches_network(fresh_env, monkeypatch):
    import main
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "_runtime_token", lambda: "T")
    monkeypatch.setattr(telegram_bot, "_http_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network in periodic doctor")))
    rt = main.Runtime("T", "1")
    main._DOCTOR_LAST["worst"] = None
    main._periodic_doctor(rt)       # لا استثناء ولا نداء


def test_config_defaults_phase5_1(fresh_env):
    from config import Config
    assert Config.BACKUPS_KEEP == 4 and Config.BACKUP_SPLIT_MB == 50 and Config.BACKUP_HOUR == 3
    assert Config.BACKUP_WEEKLY_DAY == "fri" and Config.BACKUP_WEEKLY_ENABLED is True
    assert Config.DOCTOR_TG_TIMEOUT == 3 and Config.AUDIT_FILE.endswith("audit.log")
