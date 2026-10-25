# -*- coding: utf-8 -*-
import importlib

import pytest

VALID = "AIzaSy" + "D" * 33


@pytest.fixture
def pf(fresh_env, tmp_path, monkeypatch):
    import preflight
    importlib.reload(preflight)
    monkeypatch.setattr(preflight.Config, "DATA_DIR", str(tmp_path / "data"))
    return preflight, fresh_env.key_manager


def test_version_info_has_version_and_build(pf):
    preflight, _ = pf
    v = preflight.version_info()
    assert v["version"] and v["build"]
    assert v["source"] in ("git", "fingerprint")
    assert "v" + v["version"] in preflight.version_line()


def test_fingerprint_used_when_git_missing(pf, monkeypatch):
    preflight, _ = pf
    monkeypatch.setattr(preflight, "_git_sha", lambda: "")
    v = preflight.version_info()
    assert v["source"] == "fingerprint" and v["build"].startswith("zip-") and len(v["build"]) == 11


def test_preflight_all_ok_line(pf, monkeypatch):
    preflight, km = pf
    km.add_key(VALID, "A")
    monkeypatch.setattr(km, "get_telegram_config", lambda: ("1:t", "", "77"))
    ok, line, details = preflight.run_preflight(km, web_port=8080)
    assert ok is True
    assert line.startswith("✅ ")
    assert "keys 1/1" in line and "tg ok" in line and "web :8080" in line and "DATA_DIR rw" in line
    assert "🏷️ v" in line


def test_preflight_flags_failures_without_raising(pf, monkeypatch, tmp_path):
    preflight, km = pf
    # لا مفاتيح، لا توكن، منفذ فاشل، DATA_DIR ملف (غير قابل كمجلد)
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(preflight.Config, "DATA_DIR", str(f / "sub"))
    ok, line, details = preflight.run_preflight(km, web_port=8080, web_error="OSError: busy")
    assert ok is False and line.startswith("⚠️ ")
    assert details["keys"]["ok"] is False and details["tg"]["ok"] is False
    assert details["web"]["ok"] is False and "FAIL" in details["web"]["text"]
    assert details["data_dir"]["ok"] is False and "RO" in details["data_dir"]["text"]
    assert "❌" in line


def test_preflight_circuit_clear_and_paused_use_real_snapshot_list(pf, monkeypatch):
    """صيد المالك: snapshot() ترجع list؛ القديم استدعى .items() → AttributeError مبتلع → 'circuit ?' دائماً."""
    import model_state
    preflight, km = pf
    km.add_key(VALID, "A")
    monkeypatch.setattr(km, "get_telegram_config", lambda: ("1:t", "", "77"))
    mc = model_state.model_circuit
    ok, line, details = preflight.run_preflight(km, model_circuit=mc, web_port=8080)
    assert details["circuit"]["text"] == "circuit clear" and "circuit ?" not in line and ok is True
    mc.pause(preflight.Config.MODELS[0], 120, reason="503")
    ok, line, details = preflight.run_preflight(km, model_circuit=mc, web_port=8080)
    assert details["circuit"]["ok"] is False and "circuit paused" in line and "circuit ?" not in line


def test_preflight_circuit_failure_is_logged_not_silent(pf, monkeypatch, capsys):
    preflight, km = pf

    class Broken:
        def snapshot(self, models):
            raise RuntimeError("boom")

    _, line, details = preflight.run_preflight(km, model_circuit=Broken())
    assert details["circuit"]["text"] == "circuit ?" and "preflight circuit check failed: RuntimeError: boom" in capsys.readouterr().out
