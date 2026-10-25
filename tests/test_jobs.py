# -*- coding: utf-8 -*-
"""اختبارات سجل المهام jobs.ndjson (03 §3، 22 U1): تسجيل، إعادة بناء، pending بعد restart، idempotency، ضغط."""
import json

import pytest

import jobs


@pytest.fixture
def jf(fresh_env, tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config.Config, "JOBS_FILE", str(tmp_path / "state" / "jobs.ndjson"))
    monkeypatch.setattr(jobs.Config, "JOBS_FILE", str(tmp_path / "state" / "jobs.ndjson"))
    return tmp_path / "state" / "jobs.ndjson"


def test_record_creates_dir_and_appends_fsynced_lines(jf):
    assert jobs.record("j1", "received", file_id="F1", file_unique_id="U1", chat_id="7", message_id=5)
    assert jobs.record("j1", "downloaded", size=1234)
    rows = [json.loads(l) for l in jf.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["received", "downloaded"]
    assert rows[0]["file_id"] == "F1" and rows[1]["size"] == 1234 and "ts" in rows[0]


def test_load_state_merges_fields_latest_wins(jf):
    jobs.record("j1", "received", file_id="F1", chat_id="7")
    jobs.record("j1", "transcribed", model="m", text="نص")
    jobs.record("j1", "sent")
    st = jobs.load_state()
    j = st["j1"]
    assert j["state"] == "sent" and j["file_id"] == "F1" and j["text"] == "نص"
    assert j["events"] == ["received", "transcribed", "sent"]


def test_pending_jobs_excludes_terminal_and_old(jf):
    import time
    now = time.time()
    jobs.record("old", "received", file_id="F0")                       # سيُعتبر قديماً عبر now مصطنع
    jobs.record("a", "received", file_id="FA")
    jobs.record("b", "received", file_id="FB")
    jobs.record("b", "downloaded")
    jobs.record("c", "received", file_id="FC")
    jobs.record("c", "sent")
    jobs.record("d", "received", file_id="FD")
    jobs.record("d", "failed", error="x")
    jobs.record("nofile", "received")                                  # بلا file_id → لا يمكن استئنافه
    pend = jobs.pending_jobs(max_age_h=20, now=now + 10)
    assert [j["job_id"] for j in pend] == ["old", "a", "b"]           # بترتيب الاستلام
    pend2 = jobs.pending_jobs(max_age_h=0, now=now + 3600)              # كلها أقدم من 0 ساعة
    assert pend2 == []


def test_tolerates_corrupt_trailing_line(jf, capsys):
    jobs.record("j1", "received", file_id="F1")
    with open(jf, "a", encoding="utf-8") as f:
        f.write('{"job_id": "j2", "event": "rec')          # سطر مبتور بعد انقطاع كهرباء
    st = jobs.load_state()
    assert list(st) == ["j1"]
    assert "تالف" in capsys.readouterr().out


def test_find_recent_result_idempotency(jf):
    jobs.record("j1", "received", file_id="F1", file_unique_id="U1")
    jobs.record("j1", "sent", text="النص الأول", model="m1")
    assert jobs.find_recent_result("U1")["text"] == "النص الأول"
    assert jobs.find_recent_result("U9") is None
    assert jobs.find_recent_result("U1", hours=0, now=__import__("time").time() + 10) is None
    # مهمة لم تُرسل لا تُستخدم كنتيجة
    jobs.record("j2", "received", file_unique_id="U2")
    jobs.record("j2", "transcribed", text="غير مرسل")
    assert jobs.find_recent_result("U2") is None


def test_compact_keeps_latest_jobs_only(jf):
    for i in range(10):
        jobs.record(f"j{i}", "received", file_id=f"F{i}")
        jobs.record(f"j{i}", "sent", text="t")
    assert jobs.compact_if_needed(keep=3) is True
    st = jobs.load_state()
    assert sorted(st) == ["j7", "j8", "j9"] and all(j["state"] == "sent" for j in st.values())
    assert jobs.compact_if_needed(keep=3) is False          # 3 ≤ 2×3
    assert jobs.count_by_state() == {"sent": 3}


def test_record_disk_failure_is_swallowed(jf, monkeypatch, capsys):
    monkeypatch.setattr(jobs.store, "append_json_line", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert jobs.record("j1", "received") is False
    assert "تعذر تسجيل" in capsys.readouterr().out
