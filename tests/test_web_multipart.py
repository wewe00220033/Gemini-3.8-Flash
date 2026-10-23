# -*- coding: utf-8 -*-
"""
اختبارات تحليل multipart/form-data في خادم الويب (P6, P7).
نشغّل الخادم فعلياً على منفذ عشوائي ونستبدل محرك التفريغ بـ mock يلتقط البايتات.
"""
import json
import socket
import threading
import urllib.request
from http.server import HTTPServer

import pytest


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def web(fresh_env, monkeypatch):
    import importlib
    import web_service
    importlib.reload(web_service)

    captured = {}

    def fake_transcribe(audio_bytes, mime_type="audio/webm"):
        captured["bytes"] = audio_bytes
        captured["mime"] = mime_type
        return "النص", "gemini-3.6-flash", "P1", {"elapsed": 0.5, "skipped": []}

    monkeypatch.setattr(web_service, "transcribe_audio_bytes_ex", fake_transcribe)

    port = _free_port()
    httpd = HTTPServer(("127.0.0.1", port), web_service.WebRequestHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"base": f"http://127.0.0.1:{port}", "captured": captured}
    httpd.shutdown()


def _multipart(audio, boundary, quoted=False, filename="r.webm", ctype="audio/webm"):
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + audio + f"\r\n--{boundary}--\r\n".encode()
    b = f'"{boundary}"' if quoted else boundary
    return body, f"multipart/form-data; boundary={b}"


def _post(base, body, content_type):
    req = urllib.request.Request(base + "/api/transcribe", data=body,
                                 headers={"Content-Type": content_type})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def test_multipart_basic(web):
    audio = b"\x00\x01OggS\x02\x03"
    body, ct = _multipart(audio, "----XYZ")
    res = _post(web["base"], body, ct)
    assert res["ok"] and web["captured"]["bytes"] == audio
    assert web["captured"]["mime"] == "audio/webm"


def test_multipart_payload_ending_with_crlf_is_not_truncated(web):
    """P6: بيانات صوت حقيقية تنتهي بـ \\r\\n يجب ألا تُقص."""
    audio = b"\x00\x01OggS\x02\x03\r\n\r\n"
    body, ct = _multipart(audio, "----XYZ")
    _post(web["base"], body, ct)
    assert web["captured"]["bytes"] == audio, "multipart parser truncated trailing CR/LF bytes"


def test_multipart_quoted_boundary(web):
    """P7: boundary بين علامات اقتباس مسموح في RFC 2046."""
    audio = b"\x00\x01OggS\x02\x03"
    body, ct = _multipart(audio, "----XYZ", quoted=True)
    _post(web["base"], body, ct)
    assert web["captured"]["bytes"] == audio, "quoted boundary not handled"


def test_multipart_mime_from_part_header(web):
    audio = b"ID3\x03\x00"
    body, ct = _multipart(audio, "----B", filename="a.mp3", ctype="audio/mpeg")
    _post(web["base"], body, ct)
    assert web["captured"]["mime"] == "audio/mpeg"
