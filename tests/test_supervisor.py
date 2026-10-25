# -*- coding: utf-8 -*-
"""
اختبارات المشرف النحيف (03b الطبقة 4) بعمليات فرعية حقيقية صغيرة (سكربت وهمي) — لا شبكة، نوم مُحقَن.
exit 0 → المشرف يخرج 0 | exit 3 → إعادة فورية | crash → backoff + restart_reason.json | تجمد النبض → kill
| حد الساعة → استسلام بكود 1 | SIGTERM يُمرَّر للعامل.
"""
import json
import os
import sys
import threading
import time

import pytest

import supervisor as sv

FAKE_WORKER = r'''
import os, sys, time
# يقرأ السيناريو من FAKE_PLAN: قائمة أكواد خروج حسب الجيل؛ "freeze" = لا نبض ويعلّق؛ "hang" = نبض ثم يتجاهل SIGTERM
plan = os.environ.get("FAKE_PLAN", "0").split(",")
gen = int(sys.argv[sys.argv.index("--generation") + 1])
step = plan[min(gen - 1, len(plan) - 1)]
assert os.environ.get("SUPERVISED_CHILD") == "1" and os.environ.get("WORKER_GENERATION") == str(gen)
beat = os.path.join(os.environ["DATA_DIR"], "state", "worker.beat")
with open(os.path.join(os.environ["DATA_DIR"], "state", f"gen{gen}.txt"), "w") as f:
    f.write(step)
if step == "freeze":
    time.sleep(60)                     # لا يلمس النبض أبداً → المشرف يعتبره متجمداً
elif step == "wait":
    while True:
        with open(beat, "w") as f: f.write(str(time.time()))
        time.sleep(0.2)
else:
    time.sleep(0.1)
    sys.exit(int(step))
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        time.sleep(min(s, 0.05))

    def make(plan, **kw):
        monkeypatch.setenv("FAKE_PLAN", plan)
        kw.setdefault("backoff", (5, 10, 20, 60))
        kw.setdefault("max_per_hour", 20)
        kw.setdefault("beat_max", 60)
        kw.setdefault("shutdown_grace", 2)
        return sv.Supervisor(data_dir=str(data), script=str(script), python=sys.executable, sleep=fake_sleep,
                             log=lambda *a: None, **kw)

    return make, data, sleeps


def _gens(data):
    return sorted(p.name for p in (data / "state").glob("gen*.txt"))


def test_worker_exit_zero_stops_supervisor_with_zero(env):
    make, data, sleeps = env
    s = make("0")
    assert s.run() == 0
    assert s.generation == 1 and _gens(data) == ["gen1.txt"] and not os.path.exists(s.reason_file)


def test_exit_code_3_restarts_immediately_without_backoff_and_writes_reason(env):
    make, data, sleeps = env
    s = make("3,0")
    assert s.run() == 0
    assert s.generation == 2 and _gens(data) == ["gen1.txt", "gen2.txt"]
    assert sleeps[-1] == 1 and s.consecutive_crashes == 0
    # restart_reason كُتب للجيل 2 (يقرأه العامل الجديد)؛ العامل الوهمي لا يحذفه
    r = json.load(open(s.reason_file, encoding="utf-8"))
    assert r["reason"] == "restart_requested" and r["exit_code"] == 3 and r["generation"] == 1


def test_crash_uses_backoff_ladder_and_resets_on_clean_run(env):
    make, data, sleeps = env
    s = make("1,1,1,1,1,0")
    assert s.run() == 0
    crash_delays = [x for x in sleeps if x != 1]
    assert crash_delays == [5, 10, 20, 60, 60]                   # السلم ثم السقف
    assert s.generation == 6
    r = json.load(open(s.reason_file, encoding="utf-8"))
    assert r["reason"] == "crash" and r["consecutive_crashes"] == 5


def test_frozen_worker_is_killed_and_restarted(env):
    make, data, sleeps = env
    s = make("freeze,0", beat_max=1, beat_interval=0.3)   # الحد الأدنى 3×0.3 → beat_max=1
    t0 = time.monotonic()
    assert s.run() == 0
    assert time.monotonic() - t0 < 10 and s.beat_max == 1
    assert s.generation == 2
    r = json.load(open(s.reason_file, encoding="utf-8"))
    assert r["reason"] == "frozen" and s.history[-1][2] == "frozen"


def test_crash_loop_gives_up_after_hourly_cap(env):
    make, data, sleeps = env
    s = make("1", max_per_hour=3)
    assert s.run() == 1
    assert s.generation == 4                                        # 3 إعادات مسموحة ثم الرابعة تتجاوز الحد
    r = json.load(open(s.reason_file, encoding="utf-8"))
    assert r["reason"] == "gave_up"


def test_hourly_window_prunes_old_restarts(env):
    make, data, sleeps = env
    s = make("0")
    now = [1000.0]
    s._clock = lambda: now[0]
    for _ in range(5):
        s.restart_times.append(now[0])
    assert s.restarts_last_hour() == 5
    now[0] += 3601
    assert s.restarts_last_hour() == 0


def test_stop_request_terminates_waiting_worker_and_exits_zero(env):
    make, data, sleeps = env
    s = make("wait", shutdown_grace=2)
    threading.Timer(0.6, lambda: setattr(s, "stopping", True)).start()
    t0 = time.monotonic()
    assert s.run() == 0
    assert time.monotonic() - t0 < 8 and s.proc.poll() is not None


def test_read_and_clear_restart_reason_and_touch_beat(tmp_path):
    p = tmp_path / "state" / "restart_reason.json"
    assert sv.read_and_clear_restart_reason(str(p)) is None
    p.parent.mkdir()
    p.write_text(json.dumps({"reason": "frozen", "generation": 2}), encoding="utf-8")
    assert sv.read_and_clear_restart_reason(str(p))["reason"] == "frozen" and not p.exists()
    p.write_text("{broken", encoding="utf-8")
    assert sv.read_and_clear_restart_reason(str(p)) is None
    b = tmp_path / "deep" / "worker.beat"
    assert sv.touch_beat(str(b)) is True and b.exists()


def test_beat_max_clamped_above_beat_interval(env):
    make, _, _ = env
    assert make("0", beat_max=3).beat_max == 15 and make("0", beat_max=60).beat_max == 60   # فاصل 5ث → حد أدنى 15


def test_supervisor_module_has_no_project_imports():
    """القاعدة الذهبية: المشرف stdlib فقط — لا يتجمد بسبب خطأ في كود المشروع."""
    src = open(sv.__file__, encoding="utf-8").read()
    for name in ("config", "requests", "telegram_bot", "voice_queue", "heartbeat", "netsession"):
        assert f"import {name}" not in src and f"from {name}" not in src
