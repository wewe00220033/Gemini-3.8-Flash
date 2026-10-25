"""تمرين حي (المرحلة 2 — يعمل على Windows وLinux): python smoke_failover.py
503 حقيقي (mock HTTP) → DeferredError → الطابور يؤجل → القاطع يفتح → تفريغ تلقائي بلا /reset.
يكتب في مجلد مؤقت .smoke_failover بجوار الملف (لا يلمس keys.json الحقيقي).
 503 حقيقي (mock HTTP) → DeferredError → الطابور يؤجل → القاطع يفتح → تفريغ تلقائي بلا /reset."""
import os, sys, time, json
SMOKE_DIR = os.environ.get("SMOKE_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".smoke_failover")
os.environ["DATA_DIR"] = os.path.join(SMOKE_DIR, "data"); os.environ["RETRY_BUDGET_SECONDS"] = "3"
os.environ["KEYS_JSON_FILE"] = os.path.join(SMOKE_DIR, "keys.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
config.Config.KEYS_JSON_FILE = os.path.join(SMOKE_DIR, "keys.json")
config.Config.JOBS_FILE = os.path.join(SMOKE_DIR, "data", "state", "jobs.ndjson")
config.Config.CIRCUIT_FILE = os.path.join(SMOKE_DIR, "data", "state", "circuit.json")
config.Config.KEY_GROUPS_FILE = os.path.join(SMOKE_DIR, "data", "state", "key_groups.json")
config.Config.MODEL_PAUSE_LADDER_5XX = (4, 45, 90, 180)   # درجة أولى قصيرة للتجربة الحية
import key_manager, model_state, transcriber, voice_queue, jobs
for m in (jobs, voice_queue):
    m.Config = config.Config
km = key_manager.key_manager
km.add_key("AIzaSy" + "A" * 33, "A"); km.add_key("AIzaSy" + "B" * 33, "B")
km.set_project_group("A", "proj-1")                     # A وB مشروعان مختلفان (B مستقل)
model_state.model_circuit.attach_store(config.Config.CIRCUIT_FILE)

class R:
    def __init__(s, st, p): s.status_code, s._p, s.headers, s.text = st, p, {}, ""
    def json(s): return s._p
state = {"down": True, "calls": []}
def fake_post(url, json=None, timeout=None, headers=None):
    state["calls"].append((url.split("/models/")[1].split(":")[0], headers["x-goog-api-key"][6:7]))
    if state["down"]:
        return R(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})
    return R(200, {"candidates": [{"content": {"parts": [{"text": "تم التفريغ تلقائياً"}]}}]})
transcriber.requests.post = fake_post

events = []
def handler(job):
    text, model, key, info = transcriber.transcribe_audio_bytes_ex(b"xxx", "audio/ogg")
    return {"text": text, "model": model, "key": key}
q = voice_queue.VoiceQueue(handler, on_deferred=lambda j, w, k: events.append(("deferred", w, k)),
                           on_done=lambda j, r: events.append(("done", r["text"], r["model"], r["key"])),
                           on_failed=lambda j, e: events.append(("failed", str(e)[:80])))
q.start()
job = voice_queue.Job(source="web", file_id="f", mime_type="audio/ogg", label="smoke")
t0 = time.time(); q.submit(job)
time.sleep(1.0)
print("بعد 1ث:", events, "| قاطع 3.6:", model_state.model_circuit.remaining("gemini-3.6-flash"), "ث | ضربات:", model_state.model_circuit.strikes("gemini-3.6-flash"))
print("circuit.json:", json.load(open(config.Config.CIRCUIT_FILE))["strikes"])
print("المحاولات:", state["calls"])
state["down"] = False                                   # Google رجعت
deadline = time.time() + 12
while time.time() < deadline and not any(e[0] == "done" for e in events): time.sleep(0.2)
print(f"بعد {time.time()-t0:.1f}ث:", events)
print("ضربات بعد النجاح:", model_state.model_circuit.strikes("gemini-3.6-flash"), "| stats:", {k: v for k, v in q.stats.items() if v})
q.stop(timeout=3)
ok = any(e[0] == "done" for e in events) and any(e[0] == "deferred" for e in events) and not any(e[0] == "failed" for e in events)
print("SMOKE", "PASS ✅" if ok else "FAIL ❌")
sys.exit(0 if ok else 1)
