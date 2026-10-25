# -*- coding: utf-8 -*-
"""
اختبارات voice_queue (03 §2.3، 19 §2): FIFO، تأجيل يحجب (قرار المالك) أو يتخطى، صمام الحصة، pacing،
backpressure، in_flight موحّد، تسجيل jobs.ndjson، استئناف pending بعد restart (P0).
"""
import threading
import time

import pytest

import heartbeat
import jobs
import voice_queue as vq
from transcriber import DeferredError


@pytest.fixture
def env(fresh_env, tmp_path, monkeypatch):
    import config
    jf = str(tmp_path / "state" / "jobs.ndjson")
    monkeypatch.setattr(config.Config, "JOBS_FILE", jf)
    monkeypatch.setattr(jobs.Config, "JOBS_FILE", jf)
    monkeypatch.setattr(vq.Config, "JOBS_FILE", jf)
    heartbeat.reset()
    yield tmp_path
    heartbeat.reset()


def _job(i, source="telegram"):
    return vq.Job(source=source, file_id=f"F{i}", mime_type="audio/ogg", label="voice", chat_id="7",
                  message_id=i, file_unique_id=f"U{i}", size=100 + i)


def _wait(cond, timeout=5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_fifo_order_single_worker_and_journal(env):
    order, done = [], []
    def proc(job):
        order.append(job.message_id); time.sleep(0.02); return {"text": f"t{job.message_id}"}
    q = vq.VoiceQueue(proc, on_done=lambda j, r: done.append((j.job_id, r)), pacing_s=0).start()
    try:
        positions = [q.submit(_job(i)) for i in range(5)]
        assert positions == [1, 2, 3, 4, 5] or positions[0] == 1   # الأول قد يُلتقط فوراً
        assert _wait(lambda: len(done) == 5)
        assert order == [0, 1, 2, 3, 4]
        assert q.size() == 0 and q.in_flight == 0 and q.stats["done"] == 5
        st = jobs.load_state()
        assert len(st) == 5 and all(j["state"] == "received" for j in st.values())   # الإرسال يسجّل sent من المعالج نفسه
    finally:
        q.stop()


def test_deferred_blocks_following_jobs_by_default(env):
    """[قرار-مالك] QUEUE_SKIP_DEFERRED=0: المهمة المؤجَّلة تحجب التي بعدها حتى موعدها."""
    calls = []
    def proc(job):
        calls.append((job.message_id, time.monotonic()))
        if job.message_id == 0 and job.attempts == 1:
            raise DeferredError("مزدحم", wait_s=1, kind="model")
        return {}
    deferred = []
    q = vq.VoiceQueue(proc, on_deferred=lambda j, w, k: deferred.append((j.message_id, w, k)),
                      pacing_s=0, skip_deferred=False).start()
    try:
        q.submit(_job(0)); q.submit(_job(1))
        assert _wait(lambda: q.stats["done"] == 2, timeout=6)
        ids = [c[0] for c in calls]
        assert ids == [0, 0, 1]                       # 1 لم تعمل قبل إعادة 0
        assert calls[1][1] - calls[0][1] >= 0.9       # انتظر الموعد فعلاً
        assert deferred == [(0, 1, "model")] and q.stats["deferred"] == 1
        assert jobs.load_state()[[k for k in jobs.load_state()][0]]["events"].count("deferred") == 1
    finally:
        q.stop()


def test_skip_deferred_mode_runs_ready_jobs_first(env):
    calls = []
    def proc(job):
        calls.append(job.message_id)
        if job.message_id == 0 and job.attempts == 1:
            raise DeferredError("مزدحم", wait_s=1, kind="model")
        return {}
    q = vq.VoiceQueue(proc, pacing_s=0, skip_deferred=True).start()
    try:
        q.submit(_job(0)); q.submit(_job(1)); q.submit(_job(2))
        assert _wait(lambda: q.stats["done"] == 3, timeout=6)
        assert calls == [0, 1, 2, 0]
    finally:
        q.stop()


def test_quota_valve_asks_once_and_skip_marks_skipped(env):
    asked = []
    def proc(job):
        raise DeferredError("حصة", wait_s=3600, kind="quota")
    q = vq.VoiceQueue(proc, on_quota_deferral=lambda j, w: asked.append((j.job_id, w)), pacing_s=0).start()
    try:
        job = _job(0)
        q.submit(job)
        assert _wait(lambda: len(asked) == 1)
        assert asked[0] == (job.job_id, 3600)
        snap = q.snapshot()
        assert snap["items"][0]["state"] == "deferred" and snap["items"][0]["ready_in_s"] > 3000
        assert q.skip(job.job_id) is True
        assert _wait(lambda: q.size() == 0)
        assert q.stats["skipped"] == 1
        assert jobs.load_state()[job.job_id]["state"] == "skipped"
        assert q.skip("nope") is False
    finally:
        q.stop()


def test_failed_job_recorded_and_callback(env):
    failed = []
    def proc(job):
        raise RuntimeError("boom")
    q = vq.VoiceQueue(proc, on_failed=lambda j, e: failed.append(str(e)), pacing_s=0).start()
    try:
        job = _job(0); q.submit(job)
        assert _wait(lambda: failed == ["boom"])
        st = jobs.load_state()[job.job_id]
        assert st["state"] == "failed" and st["error"] == "boom" and st["attempts"] == 1
        assert q.stats["failed"] == 1 and q.in_flight == 0
    finally:
        q.stop()


def test_pacing_gap_between_jobs(env):
    stamps = []
    q = vq.VoiceQueue(lambda j: stamps.append(time.monotonic()) or {}, pacing_s=0.5).start()
    try:
        q.submit(_job(0)); q.submit(_job(1))
        assert _wait(lambda: len(stamps) == 2)
        assert stamps[1] - stamps[0] >= 0.45
    finally:
        q.stop()


def test_queue_full_backpressure(env):
    gate = threading.Event()
    q = vq.VoiceQueue(lambda j: gate.wait(5) or {}, pacing_s=0, max_size=2).start()
    try:
        q.submit(_job(0)); q.submit(_job(1))
        _wait(lambda: q.in_flight == 1)
        with pytest.raises(vq.QueueFull):
            q.submit(_job(2))
        gate.set()
        assert _wait(lambda: q.size() == 0)
    finally:
        gate.set(); q.stop()


def test_in_flight_unified_across_sources(env):
    gate = threading.Event()
    q = vq.VoiceQueue(lambda j: gate.wait(5) or {}, pacing_s=0).start()
    try:
        q.submit(_job(0, source="web"))
        assert _wait(lambda: q.in_flight == 1)
        q.submit(_job(1, source="telegram"))
        assert q.size() == 2 and q.position(q.snapshot()["items"][-1]["job_id"]) == 2
        gate.set()
        assert _wait(lambda: q.in_flight == 0 and q.size() == 0)
    finally:
        gate.set(); q.stop()


def test_resume_pending_after_restart_in_order(env):
    """P0: مهام لم تكتمل قبل restart تعود للطابور بنفس الترتيب ومن file_id فقط."""
    jobs.record("a", "received", file_id="FA", chat_id="7", message_id=1, mime="audio/ogg", label="voice", source="telegram")
    jobs.record("b", "received", file_id="FB", chat_id="7", message_id=2, mime="audio/mpeg", label="audio")
    jobs.record("b", "downloaded")
    jobs.record("c", "received", file_id="FC"); jobs.record("c", "sent", text="x")
    got = []
    q = vq.VoiceQueue(lambda j: got.append((j.job_id, j.source, j.mime_type, j.meta.get("resumed_from"))) or {}, pacing_s=0)
    assert q.resume() == 2
    q.start()
    try:
        assert _wait(lambda: len(got) == 2)
        assert got == [("a", "resume", "audio/ogg", "received"), ("b", "resume", "audio/mpeg", "downloaded")]
        st = jobs.load_state()
        assert "resumed" in st["a"]["events"] and q.stats["resumed"] == 2
    finally:
        q.stop()


def test_worker_beats_and_stops_cleanly(env):
    before = threading.active_count()
    q = vq.VoiceQueue(lambda j: {}, pacing_s=0).start()
    assert _wait(lambda: "worker" in heartbeat.snapshot())
    q.stop()
    assert not q.is_alive() and threading.active_count() == before


def test_new_telegram_job_extracts_unique_id_and_size(env):
    msg = {"message_id": 9, "voice": {"file_id": "F", "file_unique_id": "U", "file_size": 4321}}
    j = vq.new_telegram_job("7", msg, "F", "audio/ogg", "voice")
    assert (j.file_unique_id, j.size, j.chat_id, j.message_id, j.source) == ("U", 4321, "7", 9, "telegram")
