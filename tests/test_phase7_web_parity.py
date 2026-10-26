# -*- coding: utf-8 -*-
"""
المرحلة 7b — تكافؤ الويب والأمان (13 §1 S2/S3/S5، §3، DoD 13 §7 بند 2):
 1. /api/transcribe يمر بنفس الـ pipeline: قاموس → دور → تنظيم → ALL.md بوسم «ويب» + الجلسة؛ يعود {raw, polished, session}.
 2. WEB_POLISH=0 → خام فقط؛ فشل التنظيم لا يُسقط الرد.
 3. /api/reset: GET 405 · POST 200.
 4. التوكن: header ✓ · ?token= مرة واحدة → Set-Cookie HttpOnly · cookie ✓ بعدها · بلا شيء 401.
 5. rate limit بالـ IP: 30/دقيقة ثم 429 (نافذة منزلقة).
 6. /api/doctor و /api/sessions (قراءة فقط) و /health يحمل doctor.
كله بلا شبكة (FakeTelegram غير مطلوب — الويب فقط).
"""
import json
import socket
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer

import pytest


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


@pytest.fixture
def web(fresh_env, monkeypatch):
    import importlib
    import sessions
    sessions.reset_cache()
    import web_service
    importlib.reload(web_service)
    web_service.reset_rate_limit()
    monkeypatch.setattr(web_service, "transcribe_audio_bytes_ex",
                        lambda b, m="audio/webm": ("فتحت جرافيك النهارده", "gemini-3.6-flash", "P1", {"elapsed": 1.2, "skipped": []}))
    httpd, base = _start(web_service)
    yield web_service, base
    httpd.shutdown()


def _req(url, method="GET", data=None, headers=None):
    h = dict(headers or {})
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _j(body):
    return json.loads(body.decode("utf-8"))


# =====================================================================
# 1–2: pipeline parity
# =====================================================================

def test_web_transcribe_runs_full_pipeline_and_archives_with_web_tag(web):
    ws, base = web
    import archive, sessions
    sessions.start("جلسة ويب")
    status, body, _ = _req(base + "/api/transcribe", "POST", b"\x00" * 10)
    assert status == 200
    r = _j(body)
    assert r["ok"] and r["raw"] == "فتحت Antigravity النهارده" and r["text"] == r["raw"]      # قاموس (spelling) على الخام
    assert r["polished"].startswith("[منظم-اختبار]") and r["role"] and r["session"] == "جلسة ويب" and r["archived"]
    last = archive.last()
    assert "🌐 ويب" in last["meta"] and last["raw"] == r["raw"] and last["polished"] == r["polished"]
    ents = sessions.entries(sessions.current_id())
    assert len(ents) == 1 and "🌐 ويب" in ents[0]["meta"] and ents[0]["polished"] == r["polished"]


def test_web_polish_off_or_failing_never_breaks_transcription(web, monkeypatch):
    ws, base = web
    import text_assist
    monkeypatch.setattr(ws.Config, "WEB_POLISH", False)
    status, body, _ = _req(base + "/api/transcribe", "POST", b"\x00" * 10)
    assert status == 200 and _j(body)["polished"] is None and _j(body)["raw"]
    monkeypatch.setattr(ws.Config, "WEB_POLISH", True)
    monkeypatch.setattr(text_assist, "polish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    status, body, _ = _req(base + "/api/transcribe", "POST", b"\x00" * 10)
    r = _j(body)
    assert status == 200 and r["polished"] is None and "boom" in (r["polish_error"] or "") and r["raw"]


# =====================================================================
# 3–4: أمان
# =====================================================================

def test_reset_is_post_only(web):
    ws, base = web
    assert _req(base + "/api/reset")[0] == 405
    ws.model_circuit.pause("gemini-3.6-flash", 120, "503")
    status, body, _ = _req(base + "/api/reset", "POST", b"")
    assert status == 200 and _j(body) == {"ok": True, "reset": 1}


def test_token_header_query_once_then_cookie(web, monkeypatch):
    ws, base = web
    monkeypatch.setattr(ws.Config, "WEB_ACCESS_TOKEN", "s3cret-token")
    monkeypatch.setattr(ws.WebRequestHandler, "_is_local", lambda self: False)
    assert _req(base + "/api/status")[0] == 401
    assert _req(base + "/api/status", headers={"X-Access-Token": "s3cret-token"})[0] == 200
    assert _req(base + "/api/status", headers={"X-Access-Token": "wrong"})[0] == 401
    # query مرة واحدة → cookie HttpOnly
    status, _, hdrs = _req(base + "/api/status?token=s3cret-token")
    assert status == 200
    sc = hdrs.get("Set-Cookie", "")
    assert sc.startswith(f"{ws.Config.WEB_COOKIE_NAME}=s3cret-token") and "HttpOnly" in sc
    # cookie وحدها تكفي بعدها
    status, _, hdrs = _req(base + "/api/status", headers={"Cookie": f"{ws.Config.WEB_COOKIE_NAME}=s3cret-token"})
    assert status == 200 and "Set-Cookie" not in hdrs
    assert _req(base + "/api/status", headers={"Cookie": f"{ws.Config.WEB_COOKIE_NAME}=nope"})[0] == 401
    # الصفحة نفسها تُخدم دائماً؛ /health بلا توكن
    assert _req(base + "/")[0] == 200 and _req(base + "/health")[0] in (200, 503)


# =====================================================================
# 5: rate limit
# =====================================================================

def test_rate_limit_sliding_window(web, monkeypatch):
    ws, base = web
    monkeypatch.setattr(ws.Config, "WEB_RATE_LIMIT_PER_MIN", 3)
    for _ in range(3):
        assert _req(base + "/api/transcribe", "POST", b"\x00" * 10)[0] == 200
    status, body, _ = _req(base + "/api/transcribe", "POST", b"\x00" * 10)
    assert status == 429 and "حد الطلبات" in _j(body)["error"]
    # الدالة نفسها: نافذة منزلقة
    ws.reset_rate_limit()
    assert not ws.rate_limited("1.2.3.4", now=1000, limit=2)
    assert not ws.rate_limited("1.2.3.4", now=1001, limit=2)
    assert ws.rate_limited("1.2.3.4", now=1002, limit=2)
    assert not ws.rate_limited("9.9.9.9", now=1002, limit=2)        # IP آخر مستقل
    assert not ws.rate_limited("1.2.3.4", now=1061, limit=2)        # أول ضربة خرجت من النافذة
    assert not ws.rate_limited("x", now=1, limit=0)                 # 0 = معطّل


# =====================================================================
# 6: doctor / sessions / health
# =====================================================================

def test_api_doctor_and_health_carry_doctor(web):
    ws, base = web
    status, body, _ = _req(base + "/api/doctor")
    r = _j(body)
    assert status == 200 and r["worst"] in ("ok", "warn", "fail") and any("الطبيب" in ln for ln in r["lines"])
    assert not any("getMe" in ln and "ms" in ln for ln in r["lines"])        # بلا شبكة
    status, body, _ = _req(base + "/health")
    h = _j(body)
    assert "doctor" in h and h["doctor"]["worst"] in ("ok", "warn", "fail")


def test_api_sessions_read_only(web):
    ws, base = web
    import sessions
    s = sessions.start("قراءة فقط")
    sessions.append_entry("محتوى الجلسة", model="m")
    status, body, _ = _req(base + "/api/sessions")
    r = _j(body)
    assert status == 200 and r["current"] == s["id"] and r["sessions"][0]["name"] == "قراءة فقط"
    status, body, _ = _req(base + f"/api/sessions/{s['id']}")
    r = _j(body)
    assert status == 200 and r["session"]["id"] == s["id"] and r["entries"][-1]["raw"] == "محتوى الجلسة"
    status, body, hdrs = _req(base + f"/api/sessions/{s['id']}/export")
    assert status == 200 and "محتوى الجلسة" in body.decode("utf-8") and "attachment" in hdrs.get("Content-Disposition", "")
    assert _req(base + "/api/sessions/nope")[0] == 404
    # لا مسار كتابة للجلسات عبر الويب
    assert _req(base + f"/api/sessions/{s['id']}", "POST", b"{}")[0] == 404


# =====================================================================
# 7: transcribe_tool يمر بالـ pipeline اختيارياً (--plain) — 17 المرحلة 7
# =====================================================================

def test_transcribe_tool_pipeline_optional(fresh_env):
    import importlib
    import archive
    import transcribe_tool
    importlib.reload(transcribe_tool)
    raw, archived = transcribe_tool.apply_pipeline("فتحت جرافيك", plain=False)
    assert raw == "فتحت Antigravity" and archived and "💻 أداة" in archive.last()["meta"]
    raw, archived = transcribe_tool.apply_pipeline("فتحت جرافيك", plain=True)
    assert raw == "فتحت جرافيك" and archived is False
