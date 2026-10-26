# -*- coding: utf-8 -*-
"""
خادم الويب والواجهة الصوتية العصرية 360° (Lightweight Web Engine & Voice UI)
- يعمل بـ Pure Python بدون أطر عمل خارجية (Zero-Dependency)
- تسجيل صوت حقيقي مشفر (Real MediaRecorder Audio Blob) مثل تليجرام بالضبط
- واجهة Glassmorphism داكنة مع زر نسخ فوري ومؤقت تفاعلي
- دعم كامل لسحب وإفلات الملفات وإدارة المفاتيح
"""

import os
import json
import hmac
import time
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from config import Config
from key_manager import key_manager, KeyManager
from transcriber import transcribe_audio_bytes_ex
from model_state import model_circuit
import secrets
import heartbeat
import audit
import sessions
import observability
from http.cookies import SimpleCookie


def _transcribe(audio_bytes, mime_type):
    """
    1b [متطلب-مالك]: طلبات الويب تمر من الطابور الموحّد نفسه (telegram_bot.QUEUE) ليشملها in_flight
    والإيقاف الرشيق وفاصل RPM. لو تعذّر استيراد وحدة البوت (بيئة ويب-فقط مكسورة) نرجع للنداء المباشر.
    """
    try:
        import telegram_bot
        # نمرر دالة التفريغ من هذه الوحدة: تُستبدل في الاختبارات ونحافظ على نقطة مرور واحدة للويب
        return telegram_bot.transcribe_via_queue(audio_bytes, mime_type, label="web",
                                                 transcribe_fn=transcribe_audio_bytes_ex)
    except ImportError:
        return transcribe_audio_bytes_ex(audio_bytes, mime_type)


STATIC_DIR = os.path.join(Config.SCRIPT_DIR, "static")
_INDEX_FILE = os.path.join(STATIC_DIR, "index.html")
_FALLBACK_HTML = "<!DOCTYPE html><html lang='ar' dir='rtl'><body style='font-family:sans-serif;padding:40px'>" \
                 "<h2>⚠️ ملف الواجهة static/index.html غير موجود</h2><p>ارفع مجلد static مع الكود.</p></body></html>"


def load_index_html():
    """يقرأ صفحة الواجهة من static/index.html عند كل طلب (تعديل الواجهة بدون إعادة تشغيل)."""
    try:
        with open(_INDEX_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return _FALLBACK_HTML

# ----------------------------------------------------------------------
# 13 §1 S5: rate limit بسيط بالـ IP (نافذة دقيقة منزلقة) — يحمي الحصة لو تسرّب الرابط
# ----------------------------------------------------------------------
_rl_lock = threading.Lock()
_rl_hits = {}      # ip -> [timestamps]


def rate_limited(ip, now=None, limit=None):
    """True لو تجاوز الـ IP الحد خلال آخر 60 ث. يسجّل الضربة الحالية."""
    now = now or time.time()
    limit = limit if limit is not None else int(Config.WEB_RATE_LIMIT_PER_MIN)
    if limit <= 0:
        return False
    with _rl_lock:
        hits = [t for t in _rl_hits.get(ip, []) if now - t < 60]
        if len(hits) >= limit:
            _rl_hits[ip] = hits
            return True
        hits.append(now)
        _rl_hits[ip] = hits
        if len(_rl_hits) > 1000:       # لا تضخم
            for k in [k for k, v in _rl_hits.items() if not v or now - v[-1] > 60]:
                _rl_hits.pop(k, None)
        return False


def reset_rate_limit():
    with _rl_lock:
        _rl_hits.clear()


class WebRequestHandler(BaseHTTPRequestHandler):
    # ------------------------------------------------------------------
    # أدوات مساعدة
    # ------------------------------------------------------------------
    def _send_json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if getattr(self, "_set_cookie", False) and Config.WEB_ACCESS_TOKEN:
            self.send_header("Set-Cookie", f"{Config.WEB_COOKIE_NAME}={Config.WEB_ACCESS_TOKEN}; HttpOnly; SameSite=Strict; Path=/")
            self._set_cookie = False
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _is_local(self):
        return bool(self.client_address) and self.client_address[0] in ("127.0.0.1", "::1")

    def _authorized(self):
        """
        مصادقة اختيارية: لو WEB_ACCESS_TOKEN فاضي → مفتوح بالكامل (منظومة شخصية).
        لو مضبوط → يُقبل من localhost دائماً، أو بالتوكن في Header `X-Access-Token` أو `?token=`.
        """
        token = Config.WEB_ACCESS_TOKEN
        if not token or self._is_local():
            return True
        supplied = self.headers.get("X-Access-Token", "")
        if not supplied:
            supplied = self._cookie_token()
        if not supplied:
            # 13 §1 S2: query string يُقبل لمرة واحدة لتحويله إلى cookie HttpOnly (ترويسة تُضاف في _send_json)
            qs = parse_qs(urlparse(self.path).query)
            supplied = (qs.get("token") or [""])[0]
            if supplied and hmac.compare_digest(supplied, token):
                self._set_cookie = True
        return hmac.compare_digest(supplied, token)

    _set_cookie = False

    def _cookie_token(self):
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        try:
            c = SimpleCookie()
            c.load(raw)
            m = c.get(Config.WEB_COOKIE_NAME)
            return m.value if m else ""
        except Exception:
            return ""

    def _client_ip(self):
        # 13 S5: X-Forwarded-For قابل للتزوير — لا يُعتمد إلا خلف proxy موثوق (TRUST_PROXY=1)
        if Config.TRUST_PROXY:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "?"

    def _route(self):
        return urlparse(self.path).path

    def _drain_body(self, limit=1 << 20):
        """يقرأ ويهمل body الطلب (حتى limit) كي لا يُقطع الاتصال قبل وصول الرد للعميل."""
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            n = 0
        remaining = min(n, limit)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)

    @staticmethod
    def parse_multipart(body, content_type):
        """
        يستخرج أول ملف من multipart/form-data. يرجع (bytes, mime_type) أو (None, None).
        - يدعم boundary مقتبساً (RFC 2046).
        - يقص CRLF واحداً فقط قبل الـ boundary التالي (لا يُفسد بيانات تنتهي بـ \\r\\n).
        """
        if "boundary=" not in content_type:
            return None, None
        boundary = content_type.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
        if not boundary:
            return None, None
        delim = b"--" + boundary.encode()
        for part in body.split(delim):
            if b"filename=" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            headers_part = part[:header_end].decode("latin-1")
            mime_type = "application/octet-stream"
            for line in headers_part.split("\r\n"):
                if line.lower().startswith("content-type:"):
                    mime_type = line.split(":", 1)[1].strip()
            data = part[header_end + 4:]
            if data.endswith(b"\r\n"):
                data = data[:-2]
            return data, mime_type
        return None, None

    # ------------------------------------------------------------------
    def do_GET(self):
        path = self._route()
        if path in ("/", "/index.html"):
            # الصفحة نفسها تُخدَم دائماً؛ الحماية على الـ API فقط (الواجهة تطلب التوكن عند 401)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(load_index_html().encode("utf-8"))
            return

        if path == "/health":
            # فحص صحة عام (بدون توكن) لمراقبة خارجية مثل UptimeRobot — لا يكشف أسراراً.
            # عميق (G26): 503 لو أي خيط حيوي متوقف عن النبض — كي يكتشف المراقب الخارجي التجمد لا موت العملية فقط.
            health = build_health()
            return self._send_json(200 if health["ok"] else 503, health)

        if path == "/api/status":
            if not self._authorized():
                return self._send_json(401, {"ok": False, "error": "unauthorized", "need_token": True})
            return self._send_json(200, {
                "ok": True,
                "summary": key_manager.get_status_summary(),
                "models": Config.MODELS,
                "models_state": model_circuit.snapshot(Config.MODELS),
                "recent_attempts": model_circuit.recent_attempts(5),
            })

        if path == "/api/reset":
            # 13 §1 S3: الإعادة عملية تغيير حالة → POST فقط
            return self._send_json(405, {"ok": False, "error": "method not allowed — use POST"})

        if path == "/api/doctor":
            # 13 §3: نفس تقرير /doctor المحلي (JSON) — بلا getMe ولا Gemini
            if not self._authorized():
                return self._send_json(401, {"ok": False, "error": "unauthorized", "need_token": True})
            lines, worst = observability.doctor_report(check_telegram=False)
            return self._send_json(200, {"ok": worst != "fail", "worst": worst, "lines": lines})

        if path == "/api/sessions":
            # 13 §3: عرض الجلسات للقراءة فقط
            if not self._authorized():
                return self._send_json(401, {"ok": False, "error": "unauthorized", "need_token": True})
            cur = sessions.current()
            items = [{k: s.get(k) for k in ("id", "name", "status", "count", "created", "last_ts", "summary")}
                     for s in sessions.list_sessions()]
            return self._send_json(200, {"ok": True, "current": (cur or {}).get("id"), "sessions": items})

        if path.startswith("/api/sessions/"):
            if not self._authorized():
                return self._send_json(401, {"ok": False, "error": "unauthorized", "need_token": True})
            rest = path[len("/api/sessions/"):]
            sid, _, action = rest.partition("/")
            s = sessions.get(sid) if sid else None
            if not s:
                return self._send_json(404, {"ok": False, "error": "session not found"})
            if action == "export":
                data = sessions.export_bytes(s["id"]) or b""
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="session_{s["id"]}.md"')
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send_json(200, {"ok": True, "session": s, "entries": sessions.entries(s["id"])[-50:]})

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        path = self._route()
        audit.set_actor("web")

        if path not in ("/api/transcribe", "/api/add_key", "/api/reset"):
            # نستهلك الـ body قبل الرد: على Windows إغلاق السوكت وبه بيانات غير مقروءة يرسل RST (10054) بدل 404
            self._drain_body()
            return self._send_json(404, {"ok": False, "error": "not found"})

        if not self._authorized():
            return self._send_json(401, {"ok": False, "error": "unauthorized", "need_token": True})

        if path == "/api/reset":
            self._drain_body()
            return self._send_json(200, {"ok": True, "reset": model_circuit.reset()})

        if path == "/api/transcribe":
            if rate_limited(self._client_ip()):
                self._drain_body()
                return self._send_json(429, {"ok": False, "error": f"حد الطلبات: {Config.WEB_RATE_LIMIT_PER_MIN}/دقيقة — جرّب بعد قليل"})
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                max_bytes = Config.MAX_UPLOAD_MB * 1024 * 1024
                if content_length > max_bytes:
                    return self._send_json(413, {
                        "ok": False,
                        "error": f"الملف أكبر من الحد المسموح ({Config.MAX_UPLOAD_MB}MB)",
                    })
                body = self.rfile.read(content_length)

                audio_bytes, mime_type = self.parse_multipart(body, self.headers.get("Content-Type", ""))
                if audio_bytes is None:
                    # جسم خام بدون multipart
                    audio_bytes, mime_type = body, (self.headers.get("Content-Type") or "audio/webm")

                text, model, key_name, info = _transcribe(audio_bytes, mime_type)
                # 13 §3: {raw, polished, session} + الحقول القديمة (text = الخام) للتوافق مع الواجهة
                self._send_json(200, {"ok": True, "text": text, "raw": text, "polished": info.get("polished"),
                                      "role": info.get("role"), "session": info.get("session"),
                                      "polish_error": info.get("polish_error"), "archived": info.get("archived"),
                                      "model": model, "key_name": key_name,
                                      "elapsed": info.get("elapsed"), "skipped": info.get("skipped", [])})
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})

        elif path == "/api/add_key":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                raw_key = (payload.get("key") or "").strip()
                name = (payload.get("name") or "").strip() or None

                if not raw_key:
                    raise ValueError("المفتاح فارغ!")

                # التحقق من صيغة مفتاح Google الحقيقية (نفس منطق البوت) — يمنع مفاتيح وهمية من دخول الـ Pool
                key, _ = KeyManager.extract_key_from_text(raw_key)
                if not key:
                    raise ValueError("صيغة المفتاح غير صالحة — مفاتيح Google تبدأ بـ AIzaSy أو AQ.")

                entry, is_new = key_manager.add_key(key, name)
                self._send_json(200, {"ok": True, "key": entry, "is_new": is_new})
            except Exception as e:
                self._send_json(400, {"ok": False, "error": str(e)})

    def log_message(self, format, *args):
        # كتم سجلات الـ HTTP لعدم التشويش على الكونسول إلا في الأخطاء
        return

WEB_STATE = {"started_at": None, "alive": False, "last_error": None}


# مرجع اختياري يضبطه main.py (RUNTIME["watchdog"] = Watchdog) ليعرض /health سلم التصعيد بلا دورة استيراد
RUNTIME = {}


def build_health():
    """تقرير صحة JSON يقرأه /health والمراقب الخارجي (UptimeRobot)."""
    telegram_bot = None
    try:
        import telegram_bot
        bot_state = telegram_bot.STATE
    except Exception:
        bot_state = {}
    now = time.time()
    bot_configured = bool(key_manager.get_telegram_config()[0])
    keys = key_manager.data.get("keys", [])
    keys_available = sum(
        1 for k in keys if any(key_manager.is_model_available(k, m, now) for m in Config.MODELS)
    )
    last_poll = bot_state.get("last_poll_ok")
    bot_alive = bool(bot_state.get("alive")) and (last_poll is not None and now - last_poll < 120)
    # النبضات (monotonic) — الخيوط المسجلة فقط تُحكم؛ في وضع ويب-فقط (لا بوت) لا حكم على خيوط البوت
    beats = heartbeat.snapshot()
    # العتبة تتبع الوضع الحالي (عادي ≈55 / حذر أو حماية ≈35 / طوارئ ≈25) ولا تقل عن HEALTH_STALE_AFTER المضبوطة يدوياً
    stale_after = Config.HEALTH_STALE_AFTER
    try:
        stale_after = max(Config.HEALTH_STALE_AFTER, int(telegram_bot.current_stall_after()))
    except Exception:
        pass
    stale = []
    if bot_configured:
        for name in Config.HEALTH_WATCH_THREADS:
            if heartbeat.is_stale(name, stale_after):
                stale.append(name)
    resilience = {
        "poller_generation": bot_state.get("poller_generation", 0),
        "net_errors": bot_state.get("net_errors", 0),
        "read_timeouts": bot_state.get("read_timeouts", 0),
        "protect_mode": bool(bot_state.get("protect_mode")),
        "emergency_mode": bool(bot_state.get("emergency_mode")),
        "in_flight": bot_state.get("in_flight", 0),
        "stopped_reason": bot_state.get("stopped_reason"),
    }
    queue_snap = None
    try:
        q = telegram_bot.QUEUE
        if q is not None:
            queue_snap = q.snapshot()
            resilience["in_flight"] = queue_snap["in_flight"]      # العداد الموحّد (تليجرام + ويب)
    except Exception:
        pass
    try:
        import netsession
        resilience["cautious_remaining_s"] = int(netsession.cautious.remaining_s())
        resilience["sessions"] = netsession.stats()
    except Exception:
        pass
    wd = RUNTIME.get("watchdog")
    if wd is not None:
        try:
            resilience["watchdog"] = wd.snapshot()
        except Exception:
            pass
    doctor = None
    try:
        # 13 §3: /health يعرض ملخص /doctor المحلي (بلا شبكة) — مفيد لـ UptimeRobot
        d_lines, d_worst = observability.doctor_report(check_telegram=False, now=now)
        doctor = {"worst": d_worst, "fails": [ln for ln in d_lines if ln.startswith("❌")][:5]}
    except Exception as e:
        doctor = {"worst": "unknown", "error": f"{type(e).__name__}: {e}"}
    return {
        "ok": not stale,
        "doctor": doctor,
        "stale_threads": stale,
        "stale_after_s": stale_after,
        "beats": beats,
        "resilience": resilience,
        "queue": queue_snap,
        "web_alive": True,
        "bot_alive": bot_alive,
        "bot_configured": bot_configured,
        "bot_last_poll_age_s": int(now - last_poll) if last_poll else None,
        "bot_processed": bot_state.get("processed", 0),
        "bot_last_error": bot_state.get("last_error"),
        "keys_total": len(keys),
        "keys_available": keys_available,
        "uptime_s": int(now - WEB_STATE["started_at"]) if WEB_STATE["started_at"] else 0,
        "models": Config.MODELS,
        "models_state": model_circuit.snapshot(Config.MODELS),
    }


# ----------------------------------------------------------------------
# 13 §1.1 S1: توكن ويب مولَّد لو فارغ على عنوان غير محلي
# ----------------------------------------------------------------------
WEB_TOKEN_STATE = {"generated": False, "open": False}


def ensure_web_token():
    """
    يُستدعى قبل ربط المنفذ. يرجع (mode, token|None) حيث mode ∈ {"env", "generated", "open", "local"}:
    - توكن في البيئة → "env".
    - فارغ و WEB_HOST محلي (127.0.0.1/localhost) → "local" (لا حاجة).
    - فارغ و WEB_ALLOW_OPEN=1 → "open" (قرار صريح من المالك).
    - غير ذلك → توليد secrets.token_hex(16) وضبطه في Config (الذاكرة فقط) → "generated".
    التوكن المولَّد لا يُطبع هنا؛ main يطبعه مرة واحدة في بانر ويرسله للمالك على تليجرام.
    """
    WEB_TOKEN_STATE.update(generated=False, open=False)
    if Config.WEB_ACCESS_TOKEN:
        return "env", Config.WEB_ACCESS_TOKEN
    if str(Config.WEB_HOST).strip() in ("127.0.0.1", "localhost", "::1"):
        return "local", None
    if Config.WEB_ALLOW_OPEN:
        WEB_TOKEN_STATE["open"] = True
        return "open", None
    token = secrets.token_hex(16)
    Config.WEB_ACCESS_TOKEN = token
    WEB_TOKEN_STATE["generated"] = True
    return "generated", token


def bind_web_server():
    """
    يربط المنفذ **في خيط المستدعي** ويعيد httpd. فشل الربط (منفذ مشغول/محجوز/بلا صلاحية) يرفع OSError هنا
    فوراً — لا داخل خيط خلفي يموت بصمت (G27). ThreadingHTTPServer: طلب تفريغ طويل لا يحجب /health (P11).
    """
    httpd = ThreadingHTTPServer((Config.WEB_HOST, Config.WEB_PORT), WebRequestHandler)
    httpd.daemon_threads = True
    return httpd


def serve(httpd):
    WEB_STATE["started_at"] = time.time()
    WEB_STATE["alive"] = True
    print(f"🌐 واجهة الويب الصوتية الذكية تعمل على: http://{Config.WEB_HOST}:{Config.WEB_PORT}")
    try:
        httpd.serve_forever()
    except Exception as e:
        WEB_STATE["last_error"] = str(e)
        raise
    finally:
        WEB_STATE["alive"] = False


def run_web_server():
    """تشغيل مباشر (محلياً): ربط + خدمة في نفس الخيط. الخادم ThreadingHTTPServer (انظر bind_web_server)."""
    httpd = bind_web_server()
    assert isinstance(httpd, ThreadingHTTPServer)
    serve(httpd)


def start_web_server_thread():
    """الربط هنا (قد يرفع OSError للمستدعي)، ثم الخدمة في خيط خلفي."""
    httpd = bind_web_server()
    t = threading.Thread(target=serve, args=(httpd,), daemon=True, name="web")
    t.start()
    return t
