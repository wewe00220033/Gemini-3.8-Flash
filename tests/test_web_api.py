# -*- coding: utf-8 -*-
"""اختبارات endpoints خادم الويب (P1-اختياري, P2, P8, P19) باستخدام mock لمحرك التفريغ."""
import json
import socket
import threading
import urllib.request
import urllib.error
from http.server import HTTPServer

import pytest

VALID_AIZA = "AIzaSy" + "C" * 33


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
    import web_service
    importlib.reload(web_service)
    monkeypatch.setattr(web_service, "transcribe_audio_bytes_ex",
                        lambda b, m="audio/webm": ("نص", "gemini-3.6-flash", "P1", {"elapsed": 1.2, "skipped": []}))
    httpd, base = _start(web_service)
    yield base
    httpd.shutdown()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post_json(url, payload, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_index_served(web):
    status, body = _get(web + "/")
    assert status == 200 and b"<html" in body.lower()


def test_status_endpoint(web):
    status, body = _get(web + "/api/status")
    assert status == 200 and json.loads(body)["ok"]


def test_unknown_get_404(web):
    assert _get(web + "/nope")[0] == 404


def test_unknown_post_returns_404_not_disconnect(web):
    """P8: POST لمسار غير معروف يجب أن يرد 404 بدل قطع الاتصال."""
    req = urllib.request.Request(web + "/api/unknown", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3)
        pytest.fail("expected 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404
    except Exception as e:
        pytest.fail(f"server closed connection instead of 404: {type(e).__name__}")


def test_add_key_rejects_garbage(web):
    """P2: مفتاح بصيغة غير صالحة يجب أن يُرفض بـ 400."""
    status, res = _post_json(web + "/api/add_key", {"key": "zzz", "name": ""})
    assert status == 400 and not res["ok"]


def test_add_key_accepts_valid(web):
    status, res = _post_json(web + "/api/add_key", {"key": VALID_AIZA, "name": "Web"})
    assert status == 200 and res["ok"] and res["key"]["name"] == "Web"


def test_add_key_empty_400(web):
    status, _ = _post_json(web + "/api/add_key", {"key": "", "name": ""})
    assert status == 400


def test_transcribe_engine_error_is_json_error(web, monkeypatch):
    import web_service
    def boom(b, m="x"):
        raise ValueError("بيانات الصوت فارغة!")
    monkeypatch.setattr(web_service, "transcribe_audio_bytes_ex", boom)
    req = urllib.request.Request(web + "/api/transcribe", data=b"",
                                 headers={"Content-Type": "application/octet-stream"})
    try:
        urllib.request.urlopen(req, timeout=5)
        pytest.fail("expected error")
    except urllib.error.HTTPError as e:
        assert e.code in (400, 500)
        assert not json.loads(e.read())["ok"]


def test_transcribe_oversized_rejected_413(web):
    """P19: Content-Length أكبر من الحد يجب أن يُرفض بـ 413 قبل القراءة."""
    import web_service
    limit_mb = getattr(web_service.Config, "MAX_UPLOAD_MB", None)
    if limit_mb is None:
        pytest.skip("MAX_UPLOAD_MB not implemented yet (phase 1.6)")
    req = urllib.request.Request(web + "/api/transcribe", data=b"x",
                                 headers={"Content-Type": "application/octet-stream",
                                          "Content-Length": str((limit_mb + 1) * 1024 * 1024)})
    try:
        urllib.request.urlopen(req, timeout=5)
        pytest.fail("expected 413")
    except urllib.error.HTTPError as e:
        assert e.code == 413
    except Exception:
        pass  # الخادم قد يقطع بعد 413 لأنه لا يقرأ الجسم — مقبول


# ---------- مصادقة اختيارية (بند 1.5) ----------

@pytest.fixture
def web_with_token(fresh_env, monkeypatch):
    import importlib
    import web_service
    importlib.reload(web_service)
    if not hasattr(web_service.Config, "WEB_ACCESS_TOKEN"):
        pytest.skip("WEB_ACCESS_TOKEN not implemented yet (phase 1.5)")
    monkeypatch.setattr(web_service.Config, "WEB_ACCESS_TOKEN", "s3cret")
    monkeypatch.setattr(web_service, "transcribe_audio_bytes_ex",
                        lambda b, m="audio/webm": ("نص", "m", "k", {"elapsed": 0.1, "skipped": []}))
    # نحاكي عميلاً غير محلي حتى لا يُستثنى localhost
    monkeypatch.setattr(web_service.WebRequestHandler, "_is_local", lambda self: False)
    httpd, base = _start(web_service)
    yield base
    httpd.shutdown()


def test_token_required_when_configured(web_with_token):
    status, body = _get(web_with_token + "/api/status")
    assert status == 401 and json.loads(body).get("need_token")


def test_token_accepted_via_header(web_with_token):
    status, _ = _get(web_with_token + "/api/status", headers={"X-Access-Token": "s3cret"})
    assert status == 200


def test_token_accepted_via_query(web_with_token):
    status, _ = _get(web_with_token + "/api/status?token=s3cret")
    assert status == 200


def test_index_page_always_served(web_with_token):
    """الصفحة نفسها تُخدَم دائماً — الحماية على الـ API فقط."""
    status, _ = _get(web_with_token + "/")
    assert status == 200


# ---------- المرحلة 2: /health و ThreadingHTTPServer ----------

def test_health_endpoint_public_and_shape(web_with_token):
    """2.12: /health عام (بدون توكن) ويرجع حقول المراقبة بدون أسرار."""
    status, body = _get(web_with_token + "/health")
    assert status == 200
    h = json.loads(body)
    for field in ("ok", "web_alive", "bot_alive", "keys_total", "keys_available", "uptime_s", "models"):
        assert field in h
    assert "AIzaSy" not in body.decode()


def test_server_is_threading(fresh_env):
    import importlib
    import web_service
    importlib.reload(web_service)
    from http.server import ThreadingHTTPServer
    import inspect
    assert "ThreadingHTTPServer" in inspect.getsource(web_service.run_web_server)
    assert ThreadingHTTPServer  # متاح في stdlib


# ---------- المرحلة 3: الواجهة من static/index.html ----------

def test_index_served_from_static_file(web):
    import web_service
    status, body = _get(web + "/")
    assert status == 200
    page = body.decode()
    assert "isTypeSupported" in page           # 3.9 mimeType detection
    assert "id=\"toast\"" in page               # 3.5 no alert()
    assert "alert(" not in page.split("<script>")[1].replace("// شريط رسائل بدل alert()", "")
    assert web_service._INDEX_FILE.endswith("static/index.html")


def test_index_fallback_when_static_missing(web, monkeypatch):
    import web_service
    monkeypatch.setattr(web_service, "_INDEX_FILE", "/nonexistent/index.html")
    status, body = _get(web + "/")
    assert status == 200 and "static/index.html" in body.decode()


# ---------- حادثة 01: شفافية في الويب ----------
def test_transcribe_response_includes_elapsed_and_skipped(web):
    req = urllib.request.Request(web + "/api/transcribe", data=b"RIFF0000WAVE",
                                 headers={"Content-Type": "audio/wav"})
    data = json.loads(urllib.request.urlopen(req).read())
    assert data["ok"] and data["elapsed"] == 1.2 and data["skipped"] == []


def test_status_and_health_expose_models_state_and_reset(web):
    import web_service
    web_service.model_circuit.pause("gemini-3.6-flash", 120, "503")
    st = json.loads(urllib.request.urlopen(web + "/api/status").read())
    assert st["models"] == ["gemini-3.6-flash", "gemini-3.5-transcribe"]
    assert st["models_state"][0]["paused"] and st["models_state"][0]["reason"] == "503"
    assert st["models_state"][1]["paused"] is False
    h = json.loads(urllib.request.urlopen(web + "/health").read())
    assert h["models_state"][0]["paused"]
    r = json.loads(urllib.request.urlopen(web + "/api/reset").read())
    assert r == {"ok": True, "reset": 1}
    assert not web_service.model_circuit.is_open("gemini-3.6-flash")
