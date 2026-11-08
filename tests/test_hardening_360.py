# -*- coding: utf-8 -*-
"""
test_hardening_360 — حراس انحدار لجراحة 360° (2026-11).

كل اختبار هنا يقابل عيباً أُثبت بـ Runtime Probe قبل الإصلاح، ويفشل لو عاد العيب:
  1. تقسيم HTML طويل لا يقطع <pre> ولا يفقد حرفاً  (كان يردّ 400 ويضيع النسخ بلمسة).
  2. رسالة الخام تصل قبل انتهاء نداء التسمية الذكية (كانت تتأخر 2–5 ث).
  3. رسالة المزود المحلي عند stdout فارغ ليست "خروج غير صفري (0)".
  4. الكعكة تحمل Secure على HTTPS فقط.
  5. النسخ الاحتياطي لا يترك .zip.tmp بعد فشل الكتابة، ويكنس البقايا القديمة.
"""
import html
import os
import sys
import time

import pytest

import backup
import delivery
import telegram_bot as tb
from config import Config


LIMIT = Config.TELEGRAM_MAX_MSG - 50


def _balanced(chunks):
    return all(c.count("<pre>") == c.count("</pre>") for c in chunks)


def _within(chunks):
    return all(len(c) <= LIMIT for c in chunks)


# ---------------------------------------------------------------- 1) تقسيم HTML
def test_html_chunks_never_splits_pre_tag():
    raw = "\n".join("سطر رقم %d من التفريغ الصوتي الطويل" % i for i in range(400))
    full = "<pre>%s</pre>" % html.escape(raw)
    chunks = tb._html_chunks(full, LIMIT)
    assert len(chunks) > 1                       # النص أطول من رسالة واحدة فعلاً
    assert _balanced(chunks)
    assert _within(chunks)
    # لا حرف واحد ضائع: إزالة أغلفة <pre> يجب أن تعيد النص المهرَّب حرفياً
    body = "".join(c.replace("<pre>", "").replace("</pre>", "") for c in chunks)
    assert body == html.escape(raw)


def test_html_chunks_keeps_nested_tags_balanced():
    text = "نص <b>عريض</b> عادي " * 700
    chunks = tb._html_chunks(text, LIMIT)
    assert len(chunks) > 1
    for c in chunks:
        assert c.count("<b>") == c.count("</b>")
    assert _within(chunks)


def test_html_chunks_short_text_is_single_chunk():
    assert tb._html_chunks("<pre>قصير</pre>", LIMIT) == ["<pre>قصير</pre>"]


def test_send_html_get_id_uses_balanced_chunks(monkeypatch):
    """لو رجع التقسيم غير متوازن → تليجرام 400 → سقوط للتنسيق الخام. هنا نمنع ذلك."""
    sent = []

    class Resp:
        status_code = 200

        def json(self):
            return {"result": {"message_id": 1}}

    def fake_post(token, method, payload, timeout=None):
        sent.append(payload["text"])
        return Resp()

    monkeypatch.setattr(tb, "_tg_post", fake_post)
    raw = "\n".join("سطر رقم %d من التفريغ الصوتي الطويل جداً" % i for i in range(400))
    tb.send_html_get_id("T", "1", "<pre>%s</pre>" % html.escape(raw))
    assert len(sent) > 1
    assert all(s.count("<pre>") == s.count("</pre>") for s in sent)
    # لم يسقط أي طلب إلى مسار html.unescape (يحدث فقط عند 400)
    assert all(s.startswith("<pre>") for s in sent)


# ---------------------------------------------------------------- 2) الخام أولاً
class _Recorder:
    send_document = None

    def __init__(self):
        self.events = []
        self.t0 = None

    def send_text(self, text, reply_to=None, parse_mode=None):
        self.events.append(("text", round(time.time() - self.t0, 2), text))
        return 1

    def set_reaction(self, *a):
        pass

    def send_action(self, *a):
        pass

    def edit_text(self, *a):
        pass


def test_raw_message_not_delayed_by_smart_title(monkeypatch):
    """العيب الأصلي: نداء التسمية المتزامن كان يؤخر أول رسالة بمقدار زمن النداء."""
    import archive
    import sessions

    monkeypatch.setattr(archive, "append", lambda *a, **k: True)
    monkeypatch.setattr(archive, "append_title", lambda *a, **k: True)
    monkeypatch.setattr(sessions, "append_entry", lambda *a, **k: True)
    monkeypatch.setattr(sessions, "append_title", lambda *a, **k: True)
    monkeypatch.setattr(sessions, "needs_title_suggestion", lambda: False)
    monkeypatch.setattr(delivery, "_safe_session_name", lambda: None)

    def slow_title(raw):
        time.sleep(1.0)
        return "عنوان"

    rec = _Recorder()
    rec.t0 = time.time()
    res = delivery.deliver("نص خام للاختبار", "m", "K", {}, rec,
                           polish_fn=lambda *a: ("منظم", None),
                           title_fn=slow_title, local_polish_fn=False)
    first_text_at = min(t for kind, t, _ in rec.events if kind == "text")
    assert first_text_at < 0.5, "الخام تأخر %.2f ث — التسمية عادت لتُعطّل التسليم" % first_text_at
    assert res["title"] == "عنوان"          # الوظيفة لم تُفقد


# ---------------------------------------------------------------- 3) رسالة المزود
def test_local_provider_empty_stdout_message_is_not_misleading(monkeypatch):
    import text_assist

    # ملاحظة: fresh_env يعيد تحميل config، لذا نضرب الكلاس الذي يقرأه text_assist فعلياً
    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_CMD",
                        '"%s" -c "import sys; sys.exit(0)"' % sys.executable, raising=False)
    text, err = text_assist._call_local_provider("نص", "sys")
    assert text is None
    assert "خروج غير صفري (0)" not in err
    assert "فارغ" in err


def test_local_provider_nonzero_exit_still_reports_code(monkeypatch):
    import text_assist

    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_CMD",
                        '"%s" -c "import sys; sys.stderr.write(\'boom\'); sys.exit(3)"' % sys.executable,
                        raising=False)
    text, err = text_assist._call_local_provider("نص", "sys")
    assert text is None and "boom" in err


def test_call_local_provider_does_not_use_shell_true(monkeypatch):
    """إثبات أمان SEC-01: التأكد من أن المزود المحلي لا يستدعي shell=True أبداً."""
    import text_assist
    import subprocess
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell", False)
        return subprocess.CompletedProcess(cmd, 0, stdout="منظم", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_CMD", "python dummy.py")
    out, err = text_assist._call_local_provider("sys", "raw")
    assert out == "منظم"
    assert captured["shell"] is False, "خطر أمني: تم رصد shell=True في subprocess.run!"


# ---------------------------------------------------------------- 4) Secure cookie
def test_cookie_secure_only_on_https(monkeypatch):
    import web_service

    class H:
        def __init__(self, proto):
            self.headers = {"X-Forwarded-Proto": proto}
            self.request = object()

    # خلف proxy موثوق (TRUST_PROXY=1)
    monkeypatch.setattr(web_service.Config, "TRUST_PROXY", 1)
    assert web_service._is_https(H("https")) is True
    assert web_service._is_https(H("http")) is False
    assert web_service._is_https(H("")) is False

    # بلا proxy موثوق (TRUST_PROXY=0) -> لا يعتمد على X-Forwarded-Proto
    monkeypatch.setattr(web_service.Config, "TRUST_PROXY", 0)
    assert web_service._is_https(H("https")) is False


# ---------------------------------------------------------------- 5) النسخ الاحتياطي
def test_save_local_leaves_no_tmp_on_write_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "backups_dir", lambda: str(tmp_path))
    real_open = open

    class Boom:
        def __init__(self, p):
            self.p = p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def write(self, b):
            with real_open(self.p, "wb") as f:
                f.write(b[:10])
            raise OSError(28, "No space left on device")

    monkeypatch.setattr("builtins.open",
                        lambda p, m="r", *a, **k: Boom(p) if "b" in m else real_open(p, m, *a, **k))
    with pytest.raises(OSError):
        backup.save_local(b"X" * 5000)
    monkeypatch.undo()
    assert [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")] == []


def test_stale_tmp_purged_before_new_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "backups_dir", lambda: str(tmp_path))
    stale = tmp_path / "backup_2020-01-01_0000.zip.tmp"
    stale.write_bytes(b"junk")
    old = time.time() - 7200
    os.utime(str(stale), (old, old))
    backup._purge_stale_tmp(str(tmp_path))
    assert not stale.exists()


def test_save_local_normal_path_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "backups_dir", lambda: str(tmp_path))
    paths, _removed = backup.save_local(b"OK" * 100)
    assert len(paths) == 1 and os.path.exists(paths[0])
    assert not [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]

