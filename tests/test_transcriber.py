# -*- coding: utf-8 -*-
"""اختبارات محرك التفريغ بـ mock لـ requests.post (P4, سلوك 429، حقول الرد)."""
import importlib
import pytest

VALID_A = "AIzaSy" + "A" * 33
VALID_B = "AIzaSy" + "B" * 33


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def ok_text(text):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def ok_transcription(text):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"audioTranscription": {"text": text}}]}}]})


@pytest.fixture
def engine(fresh_env):
    import transcriber
    importlib.reload(transcriber)
    transcriber.key_manager = fresh_env.key_manager
    return transcriber


def _key_of(call):
    h = call["headers"].get("x-goog-api-key")
    if h:
        return h
    return call["url"].split("key=")[1] if "key=" in call["url"] else ""


def _install_post(monkeypatch, engine, handler):
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        call = {"url": url, "headers": headers or {}}
        calls.append(call)
        return handler(call)

    monkeypatch.setattr(engine.requests, "post", fake_post)
    return calls


def test_success_first_model(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    calls = _install_post(monkeypatch, engine, lambda c: ok_text("  مرحبا  "))
    text, model, name = engine.transcribe_audio_bytes(b"abc", "audio/ogg")
    assert (text, model, name) == ("مرحبا", "gemini-3.8-flash", "A")
    assert len(calls) == 1
    assert engine.key_manager.data["keys"][0]["success_count"] == 1


def test_audio_transcription_field_supported(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    _install_post(monkeypatch, engine, lambda c: ok_transcription("نص"))
    text, _, _ = engine.transcribe_audio_bytes(b"abc")
    assert text == "نص"


def test_429_rotates_to_next_key(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    engine.key_manager.add_key(VALID_B, "B")
    _install_post(monkeypatch, engine,
                  lambda c: FakeResp(429) if _key_of(c) == VALID_A else ok_text("ok"))
    text, model, name = engine.transcribe_audio_bytes(b"abc")
    assert text == "ok" and name == "B"
    assert engine.key_manager.data["keys"][0]["status"] == "exhausted"


def test_no_keys_raises(engine):
    with pytest.raises(RuntimeError):
        engine.transcribe_audio_bytes(b"abc")


def test_empty_audio_raises(engine):
    engine.key_manager.add_key(VALID_A)
    with pytest.raises(ValueError):
        engine.transcribe_audio_bytes(b"")


def test_all_fail_raises_with_last_error(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A)
    _install_post(monkeypatch, engine, lambda c: FakeResp(500, text="boom"))
    with pytest.raises(RuntimeError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    assert "500" in str(ei.value)


def test_api_key_not_in_url(engine, monkeypatch):
    """P4: المفتاح يجب أن يُرسل في Header وليس في الـ URL."""
    engine.key_manager.add_key(VALID_A)
    calls = _install_post(monkeypatch, engine, lambda c: ok_text("x"))
    engine.transcribe_audio_bytes(b"abc")
    assert VALID_A not in calls[0]["url"], "API key leaked into URL query string"
    assert calls[0]["headers"].get("x-goog-api-key") == VALID_A
