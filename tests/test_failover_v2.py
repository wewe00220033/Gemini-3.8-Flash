# -*- coding: utf-8 -*-
"""
مرحلة 2 — Failover v2 (مواصفة 04 §6 DoD):
1. 503 على key_A → تُجرَّب key_B (مشروع مختلف) قبل قفل الموديل؛ ولا تُجرَّب شقيقة من نفس المشروع.
2. 503 متتالٍ ×1/2/3/4 → أقفال 20/45/90/180، والنجاح يصفّر العداد.
3. كل الموديلات مقفولة لمدة قصيرة والميزانية تكفي → الحلقة تنتظر ثم تنجح (وقت وهمي).
4. القفل أطول من الميزانية → DeferredError (kind=model) فوراً بلا دوران.
5. project_group — نفاد مفتاح يعلّم شقيقه (يومي و429)، والاكتشاف التلقائي يدمج.
6. circuit.json يُحمَّل بعد restart ويتجاهل المنتهي.
+ thread-safety: ضربات متوازية لا تفقد عدّاً ولا تُتلف keys.json.
"""
import json
import threading
import importlib
import pytest

VALID_A = "AIzaSy" + "A" * 33
VALID_B = "AIzaSy" + "B" * 33
VALID_C = "AIzaSy" + "C" * 33
M1, M2 = "gemini-3.6-flash", "gemini-3.5-transcribe"
LADDER = (20, 45, 90, 180)


class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = ""
        self.headers = {}

    def json(self):
        return self._payload


def ok(text="ok"):
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def err(status, msg="busy", st="UNAVAILABLE"):
    return FakeResp(status, {"error": {"message": msg, "status": st}})


R503 = err(503, "high demand", "UNAVAILABLE")


class FakeTime:
    """ساعة وهمية: time.time تتقدم بـ sleep فقط — لا انتظار حقيقي."""

    def __init__(self, start=1_000_000.0):
        self.now = start
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


@pytest.fixture
def engine(fresh_env, monkeypatch):
    import model_state
    importlib.reload(model_state)
    import transcriber
    importlib.reload(transcriber)
    transcriber.key_manager = fresh_env.key_manager
    transcriber.model_circuit = model_state.model_circuit
    ft = FakeTime()
    monkeypatch.setattr(transcriber.time, "time", ft.time)
    monkeypatch.setattr(transcriber.time, "sleep", ft.sleep)
    monkeypatch.setattr(model_state.time, "time", ft.time)
    transcriber._ft = ft
    return transcriber


def _install_post(monkeypatch, engine, handler):
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        call = {"url": url, "key": (headers or {}).get("x-goog-api-key", ""), "model": M1 if M1 in url else M2}
        calls.append(call)
        r = handler(call)
        if isinstance(r, BaseException):
            raise r
        return r

    monkeypatch.setattr(engine.requests, "post", fake_post)
    return calls


# ----------------------------------------------------------------------
# 1. القفز بين المشاريع عند 503
# ----------------------------------------------------------------------
def test_503_hops_to_key_from_other_project_before_locking(engine, monkeypatch):
    km = engine.key_manager
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    calls = _install_post(monkeypatch, engine, lambda c: R503 if c["key"] == VALID_A else ok("via B"))
    text, model, name = engine.transcribe_audio_bytes(b"abc")
    assert (text, model, name) == ("via B", M1, "B")
    assert [c["key"] for c in calls] == [VALID_A, VALID_B]
    assert not engine.model_circuit.is_open(M1), "لا قفل للموديل طالما مشروع آخر نجح"
    assert engine.model_circuit.strikes(M1) == 0


def test_503_does_not_hop_to_sibling_in_same_project(engine, monkeypatch):
    km = engine.key_manager
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    km.add_key(VALID_C, "C")
    km.set_project_group("A", "proj-X")
    km.set_project_group("B", "proj-X")       # B شقيقة A → لا فائدة من تجربتها على 503
    calls = _install_post(monkeypatch, engine, lambda c: R503 if c["key"] in (VALID_A, VALID_B) else ok("via C"))
    text, model, name = engine.transcribe_audio_bytes(b"abc")
    assert (text, name) == ("via C", "C")
    assert [c["key"] for c in calls] == [VALID_A, VALID_C]


def test_503_hops_bounded_by_config_then_graded_lock(engine, monkeypatch):
    km = engine.key_manager
    monkeypatch.setattr(engine.Config, "MODEL_503_KEY_HOPS", 1)
    for k, n in ((VALID_A, "A"), (VALID_B, "B"), (VALID_C, "C")):
        km.add_key(k, n)
    calls = _install_post(monkeypatch, engine, lambda c: R503 if c["model"] == M1 else ok("m2"))
    _, model, _ = engine.transcribe_audio_bytes(b"abc")
    assert model == M2
    assert len([c for c in calls if c["model"] == M1]) == 2, "قفزة واحدة فقط ثم قفل"
    assert engine.model_circuit.is_open(M1)
    assert engine.model_circuit.remaining(M1) <= LADDER[0] + 1
    assert engine.model_circuit.strikes(M1) == 1


# ----------------------------------------------------------------------
# 2. السلم المتدرج 20/45/90/180 + تصفير عند النجاح
# ----------------------------------------------------------------------
def test_graded_ladder_20_45_90_180_and_cap(fresh_env):
    import model_state
    mc = model_state.ModelCircuit()
    got = [mc.strike_5xx(M1, LADDER, "503") for _ in range(6)]
    assert got == [20, 45, 90, 180, 180, 180]
    assert mc.strikes(M1) == 6
    assert mc.reason(M1) == "503 ×6"
    assert mc.remaining(M1) >= 180


def test_success_resets_strikes(fresh_env):
    import model_state
    mc = model_state.ModelCircuit()
    mc.strike_5xx(M1, LADDER)
    mc.strike_5xx(M1, LADDER)
    assert mc.strikes(M1) == 2
    mc.mark_success(M1)
    assert mc.strikes(M1) == 0
    assert mc.strike_5xx(M1, LADDER) == 20, "بعد النجاح يبدأ السلم من أوله"
    snap = {x["model"]: x for x in mc.snapshot([M1, M2])}
    assert snap[M1]["strikes"] == 1 and snap[M2]["strikes"] == 0


def test_ladder_end_to_end_across_requests(engine, monkeypatch):
    """4 طلبات متتالية كلها 503 → القفل يتدرج 20 → 45 → 90 → 180 بين الطلبات، ونجاح لاحق يصفّر."""
    engine.key_manager.add_key(VALID_A, "A")
    _install_post(monkeypatch, engine, lambda c: R503)
    ft = engine._ft
    seen = []
    for _ in LADDER:
        with pytest.raises(engine.DeferredError):
            engine.transcribe_audio_bytes(b"abc")
        seen.append(engine.model_circuit.remaining(M1))
        ft.now += 10_000           # ينتهي القفل بين الطلبات (لكن الضربات تبقى)
    assert all(e <= s <= e + 1 for s, e in zip(seen, LADDER)), seen
    _install_post(monkeypatch, engine, lambda c: ok("back"))
    engine.transcribe_audio_bytes(b"abc")
    assert engine.model_circuit.strikes(M1) == 0


# ----------------------------------------------------------------------
# 3 + 4. الانتظار الذكي داخل الميزانية / التأجيل عند نفادها
# ----------------------------------------------------------------------
def test_waits_for_circuit_within_budget_then_succeeds(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    monkeypatch.setattr(engine.Config, "RETRY_BUDGET_SECONDS", 75)
    state = {"n": 0}

    def handler(c):
        state["n"] += 1
        return R503 if state["n"] <= 2 else ok("late ok")

    _install_post(monkeypatch, engine, handler)
    text, model, _ = engine.transcribe_audio_bytes(b"abc")
    assert text == "late ok" and model == M1 and state["n"] == 3
    assert engine._ft.sleeps == [20], "انتظر حتى أقرب فتح قاطع (20ث) لا 2ث عمياء"
    assert engine.model_circuit.strikes(M1) == 0


def test_budget_exhausted_raises_deferred_without_spinning(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    monkeypatch.setattr(engine.Config, "RETRY_BUDGET_SECONDS", 45)
    engine.model_circuit.pause(M1, 200, "503")
    engine.model_circuit.pause(M2, 200, "503")
    calls = _install_post(monkeypatch, engine, lambda c: ok("never"))
    with pytest.raises(engine.DeferredError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    assert calls == [] and engine._ft.sleeps == []
    assert ei.value.kind == "model" and 195 <= ei.value.wait_s <= 201
    msg = str(ei.value)
    assert msg.startswith("🤖 Gemini:") and "3 د" in msg


def test_budget_shorter_than_ladder_defers_after_first_round(engine, monkeypatch):
    engine.key_manager.add_key(VALID_A, "A")
    monkeypatch.setattr(engine.Config, "RETRY_BUDGET_SECONDS", 10)   # أقل من 20ث قفل
    calls = _install_post(monkeypatch, engine, lambda c: R503)
    with pytest.raises(engine.DeferredError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    assert len(calls) == 2 and engine._ft.sleeps == []
    assert ei.value.kind == "model" and 19 <= ei.value.wait_s <= 21


def test_deferred_job_auto_transcribes_after_circuit_opens_without_reset(engine, monkeypatch):
    """معيار القبول (17 مرحلة 2): 503 → تأجيل → بعد الفتح يُفرَّغ تلقائياً بلا /reset."""
    engine.key_manager.add_key(VALID_A, "A")
    monkeypatch.setattr(engine.Config, "RETRY_BUDGET_SECONDS", 10)
    state = {"down": True}
    _install_post(monkeypatch, engine, lambda c: R503 if state["down"] else ok("auto"))
    with pytest.raises(engine.DeferredError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    engine._ft.now += ei.value.wait_s + 1       # الطابور يعيد المهمة بعد wait_s
    state["down"] = False
    text, model, _ = engine.transcribe_audio_bytes(b"abc")
    assert text == "auto" and model == M1
    assert engine.model_circuit.strikes(M1) == 0
