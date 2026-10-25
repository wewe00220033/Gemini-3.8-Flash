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
    """4 طلبات متتالية كلها 503 → القفل يتدرج 20 → 45 → 90 → 180 بين الطلبات، ونجاح لاحق يصفّر.
    الميزانية 10ث < أول درجة → ضربة واحدة لكل طلب (بميزانية 45 الطلب الواحد يعيد المحاولة بعد 21ث فيضرب مرتين — مقصود)."""
    engine.key_manager.add_key(VALID_A, "A")
    monkeypatch.setattr(engine.Config, "RETRY_BUDGET_SECONDS", 10)
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
    # remaining() ترجع int(left)+1 → 21: ننام حتى يكون القاطع مفتوحاً فعلاً — لا 2ث عمياء
    assert engine._ft.sleeps == [21], "انتظر حتى أقرب فتح قاطع (~20ث) لا 2ث عمياء"
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


def test_default_budget_45_allows_one_smart_retry_then_defers(engine, monkeypatch):
    """بالميزانية الافتراضية (45): جولة → انتظار 21 → جولة (ضربة 2 = 45ث) → 46 > 24 متبقية → تأجيل."""
    engine.key_manager.add_key(VALID_A, "A")
    assert engine.Config.RETRY_BUDGET_SECONDS == 45
    calls = _install_post(monkeypatch, engine, lambda c: R503)
    with pytest.raises(engine.DeferredError) as ei:
        engine.transcribe_audio_bytes(b"abc")
    assert len(calls) == 4 and engine._ft.sleeps == [21]
    assert engine.model_circuit.strikes(M1) == 2 and engine.model_circuit.strikes(M2) == 2
    assert ei.value.kind == "model" and 44 <= ei.value.wait_s <= 46
    assert engine._ft.now - 1_000_000.0 == 21, "الطلب كله داخل الميزانية"


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


# ----------------------------------------------------------------------
# 5. project_group — نفاد مفتاح يعلّم شقيقه
# ----------------------------------------------------------------------
def test_daily_exhaustion_marks_project_siblings(km):
    import key_manager as kmod
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    km.add_key(VALID_C, "C")
    km.set_project_group("A", "proj-A")
    km.set_project_group("B", "proj-A")
    km.mark_key_exhausted(VALID_A, model=M1, daily=True)
    by = {k["name"]: k for k in km.data["keys"]}
    assert by["A"]["exhausted_models"][M1] > 0
    assert by["B"]["exhausted_models"][M1] == by["A"]["exhausted_models"][M1], "الشقيقة تُعلَّم بنفس الوقت"
    assert not by["C"].get("exhausted_models"), "مشروع آخر لا يتأثر"
    assert by["A"]["exhausted_models"].get(M2) is None, "موديل آخر لا يتأثر"
    assert [k["name"] for k in km.get_active_keys(model=M1)] == ["C"]
    on_disk = json.load(open(kmod.Config.KEYS_JSON_FILE, encoding="utf-8"))
    assert {k["name"]: k.get("exhausted_models", {}).get(M1) for k in on_disk["keys"]}["B"] > 0


def test_rate_limit_429_also_applies_to_group(km):
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    km.set_project_group("A", "g")
    km.set_project_group("B", "g")
    km.mark_key_exhausted(VALID_A, model=M1, retry_after=37)
    by = {k["name"]: k for k in km.data["keys"]}
    assert abs(by["B"]["exhausted_models"][M1] - by["A"]["exhausted_models"][M1]) < 0.01


def test_group_from_project_tag_and_explicit_precedence(km):
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    a, b = km.data["keys"]
    assert km.group_of(a) != km.group_of(b), "بدون حقول: كل مفتاح مجموعة مستقلة"
    assert km.group_label(a) is None
    a["project"] = "tag-1"
    b["project"] = "tag-1"
    assert km.group_of(a) == km.group_of(b) == "tag-1"
    b["project_group"] = "explicit"
    assert km.group_of(b) == "explicit", "project_group الصريح يعلو على الوسم"


def test_set_project_group_remove_and_resolve_by_prefix(km):
    km.add_key(VALID_A, "A")
    assert km.set_project_group(VALID_A[:8], "p1")["project_group"] == "p1"
    assert km.set_project_group("a", "-") is not None
    assert "project_group" not in km.data["keys"][0]
    assert km.set_project_group("nope", "x") is None


def test_autodetect_merges_keys_exhausting_together(km, monkeypatch):
    import key_manager as kmod
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    km.add_key(VALID_C, "C")
    monkeypatch.setattr(kmod.Config, "KEY_GROUP_AUTODETECT", True)
    monkeypatch.setattr(kmod.Config, "KEY_GROUP_AUTODETECT_WINDOW", 60)
    km.mark_key_exhausted(VALID_A, model=M1, daily=True)
    km.mark_key_exhausted(VALID_B, model=M1, daily=True)      # خلال النافذة → مشروع واحد
    a, b, c = km.data["keys"]
    assert km.group_of(a) == km.group_of(b)
    assert km.group_of(c) != km.group_of(a)
    assert km.group_label(a) and km.group_label(a).startswith("auto-")
    assert km.groups_summary()
    on_disk = json.load(open(kmod.Config.KEY_GROUPS_FILE, encoding="utf-8"))
    assert set(on_disk["groups"]) == {VALID_A, VALID_B}
    km2 = kmod.KeyManager()                                    # "إعادة تشغيل" تحمّل المجموعات
    a2, b2, c2 = km2.data["keys"]
    assert km2.group_of(a2) == km2.group_of(b2) != km2.group_of(c2)
    km.mark_key_exhausted(VALID_A, model=M2, daily=True)       # بعد الدمج: نفاد A يعلّم B
    assert b["exhausted_models"][M2] > 0 and not c.get("exhausted_models", {}).get(M2)


def test_autodetect_disabled_by_config(km, monkeypatch):
    import key_manager as kmod
    monkeypatch.setattr(kmod.Config, "KEY_GROUP_AUTODETECT", False)
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    km.mark_key_exhausted(VALID_A, model=M1, daily=True)
    km.mark_key_exhausted(VALID_B, model=M1, daily=True)
    a, b = km.data["keys"]
    assert km.group_of(a) != km.group_of(b)


def test_autodetect_respects_window_and_explicit_groups(km, monkeypatch):
    import key_manager as kmod
    monkeypatch.setattr(kmod.Config, "KEY_GROUP_AUTODETECT", True)
    km.add_key(VALID_A, "A")
    km.add_key(VALID_B, "B")
    km.add_key(VALID_C, "C")
    km.set_project_group("C", "fixed")
    t = {"now": 1_000.0}
    monkeypatch.setattr(kmod.time, "time", lambda: t["now"])
    km.mark_key_exhausted(VALID_A, model=M1, daily=True)
    t["now"] += 120                                            # خارج النافذة
    km.mark_key_exhausted(VALID_B, model=M1, daily=True)
    a, b, c = km.data["keys"]
    assert km.group_of(a) != km.group_of(b)
    km.mark_key_exhausted(VALID_C, model=M1, daily=True)       # داخل نافذة B لكن C صريحة → لا دمج
    assert km.group_of(c) == "fixed" and km.group_of(b) != "fixed"


# ----------------------------------------------------------------------
# 6. circuit.json — يُحمَّل بعد restart ويتجاهل المنتهي
# ----------------------------------------------------------------------
def test_circuit_persists_and_ignores_expired(fresh_env, tmp_path, monkeypatch):
    import model_state
    path = str(tmp_path / "state" / "circuit.json")
    mc = model_state.ModelCircuit(persist_path=path)
    mc.strike_5xx(M1, LADDER, "503")
    mc.strike_5xx(M1, LADDER, "503")       # 45ث، ضربتان
    mc.pause(M2, 1, "timeout")
    data = json.load(open(path, encoding="utf-8"))
    assert set(data["paused"]) == {M1, M2} and data["strikes"] == {M1: 2}

    real_time = model_state.time.time
    monkeypatch.setattr(model_state.time, "time", lambda: real_time() + 10)   # restart بعد 10ث
    mc2 = model_state.ModelCircuit(persist_path=path)
    assert mc2.is_open(M1) and not mc2.is_open(M2)
    assert 30 <= mc2.remaining(M1) <= 36
    assert mc2.strikes(M1) == 2 and mc2.reason(M1) == "503 ×2"
    assert mc2.strike_5xx(M1, LADDER) == 90, "الضربة التالية تكمل السلم من حيث توقف"


def test_circuit_load_tolerates_corrupt_file(fresh_env, tmp_path):
    import model_state
    path = tmp_path / "circuit.json"
    path.write_text("{not json", encoding="utf-8")
    mc = model_state.ModelCircuit(persist_path=str(path))
    assert not mc.is_open(M1) and mc.strikes(M1) == 0
    mc.pause(M1, 30, "503")
    assert json.load(open(path, encoding="utf-8"))["paused"][M1]["reason"] == "503"


def test_reset_clears_strikes_and_file(fresh_env, tmp_path):
    import model_state
    path = str(tmp_path / "circuit.json")
    mc = model_state.ModelCircuit(persist_path=path)
    mc.strike_5xx(M1, (20, 45), "503")
    mc.reset()
    assert mc.strikes(M1) == 0 and not mc.is_open(M1)
    data = json.load(open(path, encoding="utf-8"))
    assert data["paused"] == {} and data["strikes"] == {}


def test_in_memory_only_when_not_attached(fresh_env, tmp_path):
    import model_state
    mc = model_state.ModelCircuit()
    mc.strike_5xx(M1, (20,), "503")
    assert not [p for p in tmp_path.iterdir() if p.name.startswith("circuit")], "بدون attach_store لا ملف قاطع"


# ----------------------------------------------------------------------
# Thread-safety (طلب المالك: 200%)
# ----------------------------------------------------------------------
def test_concurrent_strikes_never_lose_count(fresh_env, tmp_path):
    import model_state
    mc = model_state.ModelCircuit(persist_path=str(tmp_path / "c.json"))
    N, T = 50, 8
    barrier = threading.Barrier(T)

    def worker():
        barrier.wait()
        for _ in range(N):
            mc.strike_5xx(M1, LADDER, "503")

    ts = [threading.Thread(target=worker) for _ in range(T)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert mc.strikes(M1) == N * T
    assert json.load(open(tmp_path / "c.json", encoding="utf-8"))["strikes"][M1] == N * T


def test_concurrent_group_exhaustion_keeps_keys_json_valid(km):
    import key_manager as kmod
    for k, n in ((VALID_A, "A"), (VALID_B, "B"), (VALID_C, "C")):
        km.add_key(k, n)
    for n in "AB":
        km.set_project_group(n, "g")
    barrier = threading.Barrier(6)
    errors = []

    def worker(i):
        try:
            barrier.wait()
            for _ in range(20):
                km.mark_key_exhausted([VALID_A, VALID_B, VALID_C][i % 3], model=[M1, M2][i % 2], daily=(i % 2 == 0))
                km.record_success(VALID_C, model=M1)
                km.get_active_keys(model=M1)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    data = json.load(open(kmod.Config.KEYS_JSON_FILE, encoding="utf-8"))
    by = {k["name"]: k for k in data["keys"]}
    assert by["A"]["exhausted_models"][M1] == by["B"]["exhausted_models"][M1]
    assert by["C"]["success_count"] == 120


# ----------------------------------------------------------------------
# دفتر الحصص (04 §3)
# ----------------------------------------------------------------------
def test_quota_ledger_modes(km, monkeypatch):
    import key_manager as kmod
    monkeypatch.setattr(kmod.Config, "MODEL_DAILY_QUOTA", {M1: 10, M2: 10})
    km.add_key(VALID_A, "A")
    led = km.quota_ledger()
    assert (led["remaining"], led["total"], led["mode"]) == (20, 20, "normal")
    for i in range(15):
        km.record_success(VALID_A, model=M1 if i < 10 else M2)
    led = km.quota_ledger()
    assert led["remaining"] == 5 and led["mode"] == "saver" and led["per_model"] == {M1: 0, M2: 5}
    km.mark_key_exhausted(VALID_A, model=M2, daily=True)
    led = km.quota_ledger()
    assert led["remaining"] == 0 and led["mode"] == "freeze"
    txt = km.quota_ledger_text()
    assert "متبقي تقريبي اليوم ~0" in txt and "متجمد" in txt
    assert "متبقي تقريبي اليوم" in km.get_status_summary()
