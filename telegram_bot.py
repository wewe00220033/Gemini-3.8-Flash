# -*- coding: utf-8 -*-
"""
بوت تليجرام للتفريغ الصوتي الذكي وإدارة المفاتيح 360°
- استقبال الفويس نوت والتفريغ الفوري بكود بلوك قابل للنسخ
- كشف المفاتيح التلقائي وإضافتها للـ Key Pool بمجرد إرسالها نصياً في الشات
- أوامر: /status  /keys  /remove <اسم>  /rename <قديم> <جديد>  /project_group <مفتاح> <مجموعة>  /help
- إرسال آمن: HTML + escape + تقسيم الرسائل الطويلة + fallback لنص خام (لا تضيع ردود)
- تفريغ متوازٍ (ThreadPool) حتى لا يحجب فويس طويل باقي الرسائل
"""

import sys
import time
import html
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
from config import Config
from key_manager import key_manager
from transcriber import transcribe_audio_bytes_ex
from model_state import model_circuit
import heartbeat
import backup
import preflight
import netsession
import jobs
import voice_queue
import archive
import settings
import prompts
import delivery
import glossary as glossary_mod

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# حالة حية يقرأها /health والـ watchdog
STATE = {
    "started_at": None,
    "last_poll_ok": None,     # آخر مرة نجح فيها getUpdates (timestamp)
    "last_error": None,
    "processed": 0,
    "alive": False,
    "poller_generation": 0,   # كم مرة أُنشئ خيط poller (1 = الأصلي)
    "net_errors": 0,          # أخطاء شبكة متراكمة في الـ poller
    "read_timeouts": 0,       # ReadTimeout في getUpdates (L0)
    "stopped_reason": None,   # لماذا خرج الـ poller طوعاً (401 / stop_event)
    "in_flight": 0,           # مهام تفريغ جارية الآن (للإيقاف الرشيق)
    "protect_mode": False,    # L3: poll قصير دائماً (يضبطه الـ watchdog)
    "emergency_mode": False,  # L3b: short-poll timeout=0 كل 2ث
}
_in_flight_lock = threading.Lock()


def set_protect_mode(on):
    STATE["protect_mode"] = bool(on)


def set_emergency_mode(on):
    STATE["emergency_mode"] = bool(on)


def current_poll_timeout():
    """long-poll الحالي: 0 في الطوارئ (L3b)، قصير في الحماية (L3) أو الحذر (L0)، وإلا الطبيعي."""
    if STATE["emergency_mode"]:
        return 0
    if STATE["protect_mode"]:
        return Config.TG_POLL_TIMEOUT_CAUTIOUS
    return netsession.cautious.poll_timeout()


def current_stall_after():
    """عتبة stall المتوافقة مع الوضع الحالي (يستخدمها الـ watchdog و/health)."""
    return current_poll_timeout() + Config.TG_READ_MARGIN + Config.STALL_MARGIN


def _api(token, method):
    return f"https://api.telegram.org/bot{token}/{method}"


def _tg_timeout(read=None):
    """مهلة tuple (فتح، قراءة) — كل نداء تليجرام يمر من هنا (03b §2 بند 3)."""
    return (Config.TG_CONNECT_TIMEOUT, read or Config.TELEGRAM_TIMEOUT)


def _http_post(url, **kw):
    """نقطة المرور الوحيدة لـ POST (Session لكل خيط + إغلاق عند الخطأ). الاختبارات تستبدلها."""
    return netsession.post(url, **kw)


def _http_get(url, **kw):
    return netsession.get_url(url, **kw)

AUDIO_EXTS = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".mp4", ".wav", ".aac", ".flac", ".webm", ".amr")


# ======================================================================
# إرسال آمن (P28)
# ======================================================================
def _chunk_text(text, limit):
    """يقسم النص لقطع ≤ limit مع تفضيل القطع عند أسطر/مسافات."""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def _tg_post(token, method, payload, timeout=None):
    return _http_post(_api(token, method), json=payload, timeout=_tg_timeout(timeout))


def send_telegram_message(token, chat_id, text, reply_to_id=None, parse_mode=None):
    """
    يرسل نصاً بأمان:
    - يقسمه إلى رسائل ≤ TELEGRAM_MAX_MSG حرف.
    - لو parse_mode فشل (400) يعيد الإرسال كنص خام حتى لا يضيع الرد أبداً.
    يرجع True لو وصلت كل القطع.
    """
    ok_all = True
    # الحد يُحسب على النص بعد التنسيق، لذا نقسم بهامش
    limit = Config.TELEGRAM_MAX_MSG - 50 if parse_mode else Config.TELEGRAM_MAX_MSG
    for i, chunk in enumerate(_chunk_text(text, limit)):
        payload = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_id and i == 0:
            payload["reply_to_message_id"] = reply_to_id
        try:
            resp = _tg_post(token, "sendMessage", payload)
            if resp.status_code != 200 and parse_mode:
                # fallback: نص خام بدون تنسيق
                payload.pop("parse_mode", None)
                payload["text"] = html.unescape(chunk) if parse_mode == "HTML" else chunk
                resp = _tg_post(token, "sendMessage", payload)
            if resp.status_code != 200:
                ok_all = False
                print(f"⚠️ تليجرام رفض الرسالة ({resp.status_code}): {resp.text[:120]}")
        except Exception as e:
            ok_all = False
            print(f"⚠️ فشل إرسال رسالة لتليجرام: {e}")
    return ok_all


def _footer(model, key_name, info=None):
    """تذييل الرد: الموديل | المفتاح | ⏱ الزمن (+ الموديلات المتخطاة إن وُجدت)."""
    foot = f"⚡ <b>الموديل:</b> <code>{html.escape(model)}</code> | 🔑 <code>{html.escape(str(key_name))}</code>"
    if info:
        if info.get("elapsed") is not None:
            foot += f" | ⏱ {info['elapsed']} ث"
        if info.get("skipped"):
            foot += "\n↪️ تم تخطّي: " + html.escape(" · ".join(info["skipped"]))
    return foot


def send_transcript(token, chat_id, text, model, key_name, reply_to_id=None, info=None):
    """
    يرسل نص التفريغ داخل <pre> (نسخ بلمسة) مع escape كامل، مقسماً لو طويل،
    ثم سطر معلومات الموديل/المفتاح/الزمن.
    """
    limit = Config.TELEGRAM_MAX_MSG - 160
    parts = _chunk_text(text, limit)
    ok = True
    for i, part in enumerate(parts):
        body = f"<pre>{html.escape(part)}</pre>"
        if i == len(parts) - 1:
            body += "\n" + _footer(model, key_name, info)
            if len(parts) > 1:
                body += f"\n📄 ({len(parts)} أجزاء)"
        ok = send_telegram_message(token, chat_id, body, reply_to_id if i == 0 else None, parse_mode="HTML") and ok
    return ok


def send_chat_action(token, chat_id, action="typing"):
    try:
        _tg_post(token, "sendChatAction", {"chat_id": chat_id, "action": action}, timeout=10)
    except Exception:
        pass


def send_html_get_id(token, chat_id, text, reply_to_id=None):
    """يرسل HTML (مقسماً لو طويل) ويرجع message_id لأول قطعة (لربط المنظم بالخام) أو None. fallback نص خام."""
    first_id = None
    limit = Config.TELEGRAM_MAX_MSG - 50
    for i, chunk in enumerate(_chunk_text(text, limit)):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
        if reply_to_id and i == 0:
            payload["reply_to_message_id"] = reply_to_id
        try:
            resp = _tg_post(token, "sendMessage", payload)
            if resp.status_code != 200:
                payload.pop("parse_mode", None)
                payload["text"] = html.unescape(chunk)
                resp = _tg_post(token, "sendMessage", payload)
            if resp.status_code == 200 and first_id is None:
                try:
                    first_id = resp.json().get("result", {}).get("message_id")
                except Exception:
                    first_id = None
            elif resp.status_code != 200:
                print(f"⚠️ تليجرام رفض الرسالة ({resp.status_code}): {resp.text[:120]}")
        except Exception as e:
            print(f"⚠️ فشل إرسال رسالة لتليجرام: {e}")
    return first_id


def make_sender(token, chat_id):
    """Sender لخط الإنتاج (delivery.deliver) فوق دوال تليجرام الحقيقية."""
    return delivery.Sender(
        send_text=lambda text, reply_to, parse_mode=None: send_html_get_id(token, chat_id, text, reply_to),
        send_document=lambda fn, data, mime, caption, reply_to: _send_document(token, chat_id, fn, data, mime, caption, reply_to),
    )


def delete_message(token, chat_id, message_id):
    try:
        _tg_post(token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id}, timeout=10)
    except Exception:
        pass


class ProgressKeeper:
    """
    أثناء التفريغ: يجدد typing كل TYPING_REFRESH_SECONDS (تليجرام تعرضه 5 ث فقط)،
    وبعد PROGRESS_AFTER_SECONDS يبعت رسالة "⏳ لسه شغّال…" تُحذف تلقائياً عند الانتهاء (Q19-أ).
    """

    def __init__(self, token, chat_id, reply_to_id=None):
        self.token, self.chat_id, self.reply_to_id = token, chat_id, reply_to_id
        self._stop = threading.Event()
        self._thread = None
        self.progress_msg_id = None

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self.progress_msg_id:
            delete_message(self.token, self.chat_id, self.progress_msg_id)
        return False

    def _run(self):
        started = time.time()
        send_chat_action(self.token, self.chat_id)
        while not self._stop.wait(max(1, Config.TYPING_REFRESH_SECONDS)):
            send_chat_action(self.token, self.chat_id)
            if self.progress_msg_id is None and time.time() - started >= Config.PROGRESS_AFTER_SECONDS:
                self.progress_msg_id = self._send_progress()

    def _send_progress(self):
        snap = model_circuit.snapshot(Config.MODELS)
        paused = [f"{_short(x['model'])} ({x['reason']})" for x in snap if x["paused"]]
        text = "⏳ لسه شغّال على التفريغ…"
        if paused:
            text += f"\n↪️ تم تخطّي: {', '.join(paused)}"
        try:
            payload = {"chat_id": self.chat_id, "text": text}
            if self.reply_to_id:
                payload["reply_to_message_id"] = self.reply_to_id
            resp = _tg_post(self.token, "sendMessage", payload)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("message_id")
        except Exception:
            pass
        return None


def _short(m):
    return m.replace("gemini-", "")


# ======================================================================
# معالجة الرسائل
# ======================================================================
def _is_audio_message(msg):
    """يرجع (file_id, mime_type, label) لو الرسالة صوتية، وإلا None."""
    if msg.get("voice"):
        v = msg["voice"]
        return v["file_id"], v.get("mime_type", "audio/ogg"), "voice"
    if msg.get("audio"):
        a = msg["audio"]
        return a["file_id"], a.get("mime_type", "audio/mpeg"), a.get("file_name", "audio")
    if msg.get("video_note") and Config.ACCEPT_VIDEO:
        v = msg["video_note"]
        return v["file_id"], v.get("mime_type", "video/mp4"), "video_note"
    if msg.get("video") and Config.ACCEPT_VIDEO:
        v = msg["video"]
        return v["file_id"], v.get("mime_type", "video/mp4"), v.get("file_name", "video")
    doc = msg.get("document")
    if doc:
        mime = (doc.get("mime_type") or "").lower()
        name = (doc.get("file_name") or "").lower()
        if mime.startswith("audio/") or name.endswith(AUDIO_EXTS):
            return doc["file_id"], mime or "audio/mpeg", doc.get("file_name", "document")
    return None


def _download_file(token, file_id):
    info = _http_get(
        _api(token, "getFile"),
        params={"file_id": file_id},
        timeout=_tg_timeout(),
    ).json()
    file_path = info.get("result", {}).get("file_path")
    if not file_path:
        raise RuntimeError("تعذر الحصول على مسار الملف من تليجرام")
    resp = _http_get(
        f"https://api.telegram.org/file/bot{token}/{file_path}",
        timeout=_tg_timeout(Config.TELEGRAM_DOWNLOAD_TIMEOUT),
    )
    resp.raise_for_status()
    return resp.content


# ======================================================================
# الطابور الموحّد (1b): تليجرام + الويب عبر voice_queue واحد — in_flight واحد للإيقاف الرشيق
# ======================================================================
QUEUE = None                 # voice_queue.VoiceQueue — يُنشأ في start_queue()
_queue_lock = threading.Lock()
_quota_prompts = {}          # job_id -> (chat_id, prompt_message_id) لرسالة صمام الحصة


def set_reaction(token, chat_id, message_id, emoji=None):
    """👀 فوري على الفويس (Bot API 7.0+). فشله صامت — ليس جزءاً من المسار الحرج."""
    if not Config.REACTION_ENABLED:
        return False
    emoji = emoji or Config.REACTION_RECEIVED
    try:
        resp = _tg_post(token, "setMessageReaction",
                        {"chat_id": chat_id, "message_id": message_id,
                         "reaction": [{"type": "emoji", "emoji": emoji}]}, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def _download_with_retry(token, file_id):
    """تحميل الملف من تليجرام مع إعادة محاولة ×DOWNLOAD_RETRIES وbackoff؛ يرفع RuntimeError مفهوماً."""
    last = None
    for i in range(max(1, Config.DOWNLOAD_RETRIES)):
        try:
            return _download_file(token, file_id)
        except Exception as e:
            last = e
            if i + 1 < Config.DOWNLOAD_RETRIES:
                delay = Config.DOWNLOAD_BACKOFF[min(i, len(Config.DOWNLOAD_BACKOFF) - 1)]
                print(f"⚠️ تحميل الفويس فشل ({type(e).__name__}) — محاولة {i + 2}/{Config.DOWNLOAD_RETRIES} بعد {delay}ث")
                time.sleep(delay)
    raise RuntimeError(f"📡 تليجرام: تعذر تحميل الفويس بعد {Config.DOWNLOAD_RETRIES} محاولات ({type(last).__name__}: {last})")


def _runtime_token():
    token, _, _ = key_manager.get_telegram_config()
    return token


def process_job(job):
    """
    معالج الطابور (يعمل في خيط transcribe-N). يرفع DeferredError ليؤجل الطابور بدل الفشل.
    - source == "web": البايتات في job.meta["audio_bytes"] — لا تحميل ولا إرسال؛ النتيجة تُعاد للمنادي.
    - source == "telegram"/"resume": dedup → تحميل → تفريغ → إرسال، مع تسجيل كل خطوة في jobs.ndjson.
    """
    heartbeat.beat("worker", note=f"job start {job.job_id}")
    if job.source == "web":
        audio_bytes = job.meta.get("audio_bytes") or b""
        fn = job.meta.get("transcribe_fn") or transcribe_audio_bytes_ex
        text, model, key_name, info = fn(audio_bytes, job.mime_type)
        jobs.record(job.job_id, "sent", model=model, key=str(key_name), elapsed=info.get("elapsed"), text=text[:2000])
        STATE["processed"] += 1
        return {"text": text, "model": model, "key_name": key_name, "info": info}

    token = job.meta.get("token") or _runtime_token()
    chat_id, reply_to = job.chat_id, job.message_id
    cached = jobs.find_recent_result(job.file_unique_id) if job.file_unique_id else None
    if cached:
        print(f"♻️ نفس الملف فُرِّغ قبل قليل — إعادة إرسال النتيجة المحفوظة ({job.job_id})")
        send_transcript(token, chat_id, cached["text"], cached.get("model") or "cache", cached.get("key") or "cache",
                        reply_to_id=reply_to, info={"elapsed": cached.get("elapsed")})
        jobs.record(job.job_id, "sent", dedup_of=cached.get("job_id"), text=cached["text"][:2000],
                    model=cached.get("model"), key=cached.get("key"))
        if QUEUE:
            QUEUE.stats["deduped"] += 1
        return {"text": cached["text"], "dedup": True}

    print(f"🎙️ بدء تفريغ ({job.label}) من {chat_id} [{job.source}:{job.job_id}]...")
    with ProgressKeeper(token, chat_id, reply_to_id=reply_to):
        audio_bytes = _download_with_retry(token, job.file_id)
        jobs.record(job.job_id, "downloaded", bytes=len(audio_bytes))
        text, model, key_name, info = transcribe_audio_bytes_ex(audio_bytes, job.mime_type)
    jobs.record(job.job_id, "transcribed", model=model, key=str(key_name), elapsed=info.get("elapsed"))
    print(f"✅ تم التفريغ عبر [{model}] بالمفتاح [{key_name}] في {info.get('elapsed')} ث: {text[:40]}...")
    # المرحلة 3: قاموس → أرشفة (قبل الإرسال) → الخام → المنظم (07)
    res = delivery.deliver(text, model, key_name, info, make_sender(token, chat_id), reply_to_id=reply_to,
                           footer_html=_footer(model, key_name, info))
    text = res["raw"]
    ok = bool(res["raw_msg_id"] or res["polished_msg_id"])
    if ok:
        jobs.record(job.job_id, "sent", model=model, key=str(key_name), elapsed=info.get("elapsed"), text=text[:2000],
                    polished=bool(res["polished"]), mode=res["mode"])
    else:
        jobs.record(job.job_id, "failed", error="send failed (archived — /last)", model=model)
    if res["polish_error"]:
        print(f"⚠️ التنظيم: {res['polish_error']}")
    STATE["processed"] += 1
    return {"text": text, "model": model, "key_name": key_name, "info": info, "polished": res["polished"]}


def _on_job_failed(job, err):
    STATE["last_error"] = str(err)
    print(f"❌ خطأ تفريغ [{job.job_id}]: {err}")
    if job.source == "web" or not job.chat_id:
        return
    token = job.meta.get("token") or _runtime_token()
    send_telegram_message(token, job.chat_id, f"❌ عذراً، فشل التفريغ:\n{err}", reply_to_id=job.message_id)


def _on_job_deferred(job, wait_s, kind):
    """تأجيل صامت (الاستراتيجية الصامتة): لوج فقط؛ المالك يُسأل عند الحصة فقط عبر _on_quota_deferral."""
    print(f"⏳ تأجيل [{job.job_id}] {wait_s}ث ({kind}) — تأجيل #{job.deferrals}")


def _on_quota_deferral(job, wait_s):
    """صمام الحصة [قرار-مالك]: مرة واحدة لكل مهمة — [⏭ تخطَّ] [⏳ انتظر] (inline فقط؛ الافتراضي الانتظار)."""
    if job.source == "web" or not job.chat_id:
        return
    token = job.meta.get("token") or _runtime_token()
    mins = max(1, int(round(wait_s / 60)))
    payload = {
        "chat_id": job.chat_id,
        "text": f"⏳ الحصة نفدت مؤقتاً على كل المفاتيح — المهمة هتتنفذ تلقائياً بعد ~{mins} د.\n"
                f"لو مش مستعجل سيبها، أو تخطّاها:",
        "reply_markup": {"inline_keyboard": [[
            {"text": "⏭ تخطَّ", "callback_data": f"q:skip:{job.job_id}"},
            {"text": "⏳ انتظر", "callback_data": f"q:wait:{job.job_id}"},
        ]]},
    }
    if job.message_id:
        payload["reply_to_message_id"] = job.message_id
    try:
        resp = _tg_post(token, "sendMessage", payload)
        if resp.status_code == 200:
            mid = resp.json().get("result", {}).get("message_id")
            _quota_prompts[job.job_id] = (job.chat_id, mid)
    except Exception as e:
        print(f"⚠️ تعذر إرسال صمام الحصة: {e}")


def handle_callback(token, cq):
    """callback_query من الأزرار inline: q:skip:<id> / q:wait:<id>. يرد answerCallbackQuery دائماً."""
    data = cq.get("data") or ""
    cq_id = cq.get("id")
    note = ""
    parts = data.split(":")
    if len(parts) == 3 and parts[0] == "q":
        action, job_id = parts[1], parts[2]
        if action == "skip":
            ok = QUEUE.skip(job_id) if QUEUE else False
            note = "⏭ تم التخطّي" if ok else "المهمة انتهت بالفعل"
        elif action == "wait":
            note = "⏳ تمام، هستنى وهنفّذها تلقائياً"
        prompt = _quota_prompts.pop(job_id, None)
        if prompt and prompt[1]:
            delete_message(token, prompt[0], prompt[1])
    try:
        _tg_post(token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": note[:200]}, timeout=10)
    except Exception:
        pass
    return note


def start_queue(resume=True):
    """ينشئ الطابور الموحّد ويشغّله مرة واحدة؛ resume=True يعيد المهام المعلقة من jobs.ndjson (P0)."""
    global QUEUE
    with _queue_lock:
        if QUEUE is None:
            QUEUE = voice_queue.VoiceQueue(
                process_job, on_deferred=_on_job_deferred, on_quota_deferral=_on_quota_deferral,
                on_failed=_on_job_failed,
            ).start()
            if resume:
                try:
                    n = QUEUE.resume()
                    if n:
                        print(f"🔁 استئناف {n} مهمة معلقة من الجلسة السابقة (P0)")
                except Exception as e:
                    print(f"⚠️ فشل استئناف المهام المعلقة: {e}")
        return QUEUE


def stop_queue(timeout=2):
    global QUEUE
    with _queue_lock:
        q, QUEUE = QUEUE, None
    if q:
        q.stop(timeout=timeout)


def queue_in_flight():
    """عداد المهام الجارية الموحّد (تليجرام + ويب) — يقرأه main.shutdown و/health."""
    q = QUEUE
    return q.in_flight if q else 0


def transcribe_via_queue(audio_bytes, mime_type, label="web", timeout=None, transcribe_fn=None):
    """
    مسار الويب الموحّد [متطلب-مالك]: يُدرج Job(source="web") في نفس الطابور وينتظر النتيجة.
    بذلك in_flight واحد للتليجرام والويب — SIGTERM لا يقطع تفريغاً جارياً من الويب.
    يرجع (text, model, key_name, info) أو يرفع الاستثناء الأصلي (QueueFull / RuntimeError ...).
    """
    done = threading.Event()
    job = voice_queue.Job(source="web", file_id=None, mime_type=mime_type, label=label,
                          size=len(audio_bytes), meta={"audio_bytes": audio_bytes, "done": done,
                                                       "transcribe_fn": transcribe_fn})
    q = start_queue()
    q.submit(job, journal_event=None)          # لا استئناف لمهام الويب: البايتات في الذاكرة فقط
    # الطابور يضبط meta["done"] عند أي نهاية (done/failed/skipped) — نستيقظ فوراً
    timeout = timeout or (Config.GEMINI_READ_TIMEOUT_MAX_LARGE * 3)
    done.wait(timeout)
    if job.state == "done" and job.result:
        r = job.result
        return r["text"], r["model"], r["key_name"], r["info"]
    if job.state == "failed":
        raise RuntimeError(job.error or "فشل التفريغ")
    if job.state == "skipped":
        raise RuntimeError("تم تخطّي المهمة")
    raise RuntimeError(f"⏳ انتهت مهلة الانتظار في الطابور ({int(timeout)}ث) — المهمة لا تزال {job.state}")


def enqueue_telegram_audio(token, chat_id, msg, file_id, mime_type, label):
    """
    يعمل في خيط الـ poller — سريع بلا شبكة ثقيلة:
    حجم > MAX_FILE_MB → رسالة؛ 👀 فوراً؛ إدراج في الطابور؛ إيصال فقط لو الطابور > 1 [قرار-مالك].
    """
    media = msg.get("voice") or msg.get("audio") or msg.get("video_note") or msg.get("video") or msg.get("document") or {}
    size = media.get("file_size") or 0
    if size > Config.MAX_FILE_MB * 1024 * 1024:
        send_telegram_message(token, chat_id,
                              f"📦 الملف {size / 1024 / 1024:.1f}MB أكبر من حد تليجرام للبوتات ({Config.MAX_FILE_MB}MB). "
                              f"قصّه أو ابعته كفويس أقصر.", reply_to_id=msg["message_id"])
        return None
    set_reaction(token, chat_id, msg["message_id"])
    q = start_queue()
    job = voice_queue.new_telegram_job(chat_id, msg, file_id, mime_type, label)
    job.meta["token"] = token
    try:
        position = q.submit(job)
    except voice_queue.QueueFull as e:
        send_telegram_message(token, chat_id, f"🚦 {e}", reply_to_id=msg["message_id"])
        return None
    if q.size() > 1:
        send_telegram_message(token, chat_id, f"📥 استلمت — ترتيبك في الطابور: {position} (قدّامك {position - 1})",
                              reply_to_id=msg["message_id"])
    return job


def handle_audio(token, chat_id, msg, file_id, mime_type, label):
    """توافق خلفي: يُدرج في الطابور الموحّد (كان ينفّذ مباشرة في ThreadPool)."""
    return enqueue_telegram_audio(token, chat_id, msg, file_id, mime_type, label)


def _queue_text():
    q = QUEUE
    if not q:
        return "📭 الطابور غير مشغّل بعد (لا مهام)."
    s = q.snapshot()
    st = s["stats"]
    mode = "تخطّي المؤجَّل" if s["skip_deferred"] else "حجب (ترتيب الوصول)"
    lines = [f"📋 الطابور: {s['size']} مهمة | جارية {s['in_flight']} | الوضع: {mode}",
             f"📈 منجز {st['done']} · فشل {st['failed']} · مؤجَّل {st['deferred']} · متخطّى {st['skipped']} · "
             f"مستأنَف {st['resumed']} · مكرر {st['deduped']}"]
    for i, it in enumerate(s["items"][:10], 1):
        extra = f" — يفتح بعد {it['ready_in_s']}ث" if it["ready_in_s"] else ""
        lines.append(f"  {i}. {it['label']} [{it['source']}] {it['state']} ({it['waiting_s']}ث){extra}")
    if s["size"] > 10:
        lines.append(f"  … و{s['size'] - 10} أخرى")
    return "\n".join(lines)


def _help_text():
    return (
        "👋 أهلاً بك في منظومة التفريغ الصوتي الذكي 360° 🎙️⚡\n\n"
        "• للتفريغ: ابعت أي فويس نوت أو ملف صوتي، وهفرغهولك فوراً بنص جاهز للنسخ بلمسة واحدة.\n"
        "• لإضافة مفتاح: ابعت مفتاح Gemini هنا مباشرة (مع اسم أو بدونه) وهيتسجل تلقائياً.\n\n"
        "الأوامر:\n"
        "/status — حالة المفاتيح والرصيد المتبقي اليوم ووقت التصفير\n"
        "/keys — قائمة المفاتيح مرتبة (المتاح أولاً)\n"
        "/remove <اسم> — حذف مفتاح بالاسم أو بأول 8 أحرف منه\n"
        "/rename <قديم> <جديد> — إعادة تسمية مفتاح\n"
        "/mode [dual|raw|polished] — وضع الإرسال: خام + منظم (افتراضي) / خام فقط / منظم فقط\n"
        "/roles · /role <id> — الأدوار (عام/DevOps/معلم/حرفي/شيخ/أطفال/طبيب) والدور الحالي\n"
        "/new_role <id> | <الاسم> | <تعليمات التنظيم> — دور مخصص · /role_del <id>\n"
        "/glossary — القاموس · /add_word شلبي = ChatGPT · /del_word <id|لفظ> · /confirm_word <id> = المقصود · /glossary_export\n"
        "/last — إعادة إرسال آخر تفريغ من الأرشيف · /find <كلمة> — بحث في الأرشيف · /polish (رد على تفريغ) — تنظيم عند الطلب\n"
        "/header [both|raw|polished|off] — بانر «تفريغ آلي» فوق النص\n"
        "/project_group <مفتاح> <مجموعة> — مفاتيح نفس مشروع Google تتشارك الحصة (والقفز عند 503 لمشروع آخر). اكتب - للإزالة\n"
        "/backup — نسخة احتياطية (أرشيف+جلسات+إعدادات) بلا أسرار\n"
        "/backup_secrets — نسخة keys.json بتأكيد صريح\n"
        "/version — رقم النسخة والبناء ومدة التشغيل\n"
        "/queue — الطابور الحالي وإحصاءاته\n"
        "/reset — فتح قواطع الموديلات المقفولة مؤقتاً (لو عايز تجرّب حالاً)\n"
        "/help — هذه الرسالة\n\n"
        f"🛡️ الموديلات: {key_manager.models_chain_text()}"
    )


def _keys_list_text():
    keys = key_manager.data.get("keys", [])
    if not keys:
        return "لا توجد مفاتيح بعد. ابعت مفتاح Gemini هنا لإضافته."
    now = time.time()

    def sort_key(k):
        avail = any(key_manager.is_model_available(k, m, now) for m in Config.MODELS)
        return (0 if avail else 1, k.get("name", ""))

    lines = ["🔑 المفاتيح (المتاح أولاً):"]
    for k in sorted(keys, key=sort_key):
        avail = any(key_manager.is_model_available(k, m, now) for m in Config.MODELS)
        rem = key_manager.remaining_today(k)
        total_rem = sum(rem.values())
        masked = k.get("key", "")[:8] + "..." + k.get("key", "")[-4:]
        lines.append(f"{'🟢' if avail else '⏳'} {k.get('name')}  [{masked}]  متبقي اليوم: {total_rem}")
    return "\n".join(lines)


def _resilience_text():
    """سطر التعافي الذاتي في /status (03b): جيل الـ poller، الوضع، أخطاء الشبكة، المهام الجارية."""
    mode = "طوارئ (short-poll)" if STATE["emergency_mode"] else "حماية (poll قصير)" if STATE["protect_mode"] \
        else f"حذر ({int(netsession.cautious.remaining_s())}ث)" if netsession.cautious.active() else "طبيعي"
    return (f"🛡️ التعافي الذاتي: الوضع {mode} | poller #{STATE['poller_generation']} | "
            f"أخطاء شبكة {STATE['net_errors']} (ReadTimeout {STATE['read_timeouts']}) | مهام جارية {queue_in_flight()}")


def _pipeline_text():
    """سطر خط الإنتاج في /status (المرحلة 3): الدور، وضع الإرسال، البانر، حجم الأرشيف، القاموس."""
    r = prompts.get_role(settings.role()) or {}
    gl = glossary_mod.get_glossary()
    pend = sum(1 for e in gl.entries(False) if e.get("status") == "pending")
    return (f"🏭 خط الإنتاج: دور {settings.role()} ({r.get('name', '?')}) | إرسال {settings.delivery_mode()} | بانر {settings.header_target()} | "
            f"أرشيف {archive.count_entries()} تفريغ | قاموس {len(gl.entries(False))} مدخل" + (f" ({pend} معلَّق — /glossary)" if pend else ""))


def _models_state_text():
    """حالة قواطع الموديلات + آخر المحاولات (للشفافية في /status)."""
    lines = ["─" * 30, "🚦 حالة الموديلات الآن:"]
    for x in model_circuit.snapshot(Config.MODELS):
        if x["paused"]:
            lines.append(f"  ⛔ {_short(x['model'])}: متخطّى ({x['reason']}) — يفتح {x['until']} (بعد {x['remaining_s']} ث)")
        else:
            lines.append(f"  🟢 {_short(x['model'])}: متاح")
    recent = model_circuit.recent_attempts(5)
    if recent:
        lines.append("🕓 آخر المحاولات:")
        for a in recent:
            lines.append(f"  {a['at']} {_short(a['model'])} [{a['key']}] {a['outcome']} {a['elapsed_s']} ث")
    return "\n".join(lines)


def _send_document(token, chat_id, filename, data_bytes, mime, caption, reply_to_id):
    resp = _http_post(
        _api(token, "sendDocument"),
        data={"chat_id": chat_id, "caption": caption, "reply_to_message_id": reply_to_id},
        files={"document": (filename, data_bytes, mime)},
        timeout=_tg_timeout(Config.TELEGRAM_DOWNLOAD_TIMEOUT),
    )
    if resp.status_code != 200:
        send_telegram_message(token, chat_id, f"⚠️ فشل إرسال الملف ({resp.status_code}).", reply_to_id=reply_to_id)
    return resp


def _glossary_text(page="1"):
    gl = glossary_mod.get_glossary()
    ents = gl.entries()
    if not ents:
        return "القاموس فارغ. أضف: /add_word شلبي = ChatGPT"
    size = Config.PANEL_PAGE_SIZE
    try:
        pg = max(1, int(page))
    except ValueError:
        pg = 1
    pages = max(1, (len(ents) + size - 1) // size)
    pg = min(pg, pages)
    lines = [f"📖 القاموس ({len(ents)} مدخل) — صفحة {pg}/{pages}:"]
    for e in ents[(pg - 1) * size: pg * size]:
        st = "⏳ معلَّق" if e.get("status") == "pending" else ("خام+منظم" if e.get("scope") == "both" else "منظم فقط")
        lines.append(f"• [{e['id']}] {' / '.join(e.get('patterns', []))} → {e.get('replacement')}  ({e.get('kind')}, {st}, ×{e.get('hits', 0)})")
        if e.get("note"):
            lines.append(f"    ℹ️ {e['note']} — أكّد: /confirm_word {e['id']} = المقصود")
    if pages > 1:
        lines.append(f"الصفحة التالية: /glossary {pg + 1}")
    if gl.corrupt_notice:
        lines.append(gl.corrupt_notice)
    return "\n".join(lines)


def _strip_banner(text):
    """يزيل البانر من نص منسوخ من رسالة تفريغ قبل تنظيمه."""
    marker = "───────────────"
    if marker in text:
        text = text.split(marker, 1)[1]
    return text.strip()


def handle_text(token, chat_id, msg, msg_text):
    # 1) مفتاح Gemini جديد؟
    extracted_key, extracted_name = key_manager.extract_key_from_text(msg_text)
    if extracted_key:
        entry, is_new = key_manager.add_key(extracted_key, extracted_name)
        status_str = "إضافة مفتاح جديد" if is_new else "تحديث المفتاح"
        reply = (
            f"🎉 تم بنجاح {status_str} لمصفوفة المفاتيح!\n\n"
            f"📌 اسم المشروع: {entry.get('name')}\n"
            f"🔑 المفتاح: {extracted_key[:8]}...{extracted_key[-4:]}\n"
            f"🟢 الحالة: نشط جاهز للاستخدام\n"
            f"⚡ رصيدك اليومي زاد بمعدل {sum(Config.MODEL_DAILY_QUOTA.get(m, 0) for m in Config.MODELS)} ريكورد إضافي! 🚀"
        )
        send_telegram_message(token, chat_id, reply, reply_to_id=msg["message_id"])
        return

    # 2) الأوامر
    parts = msg_text.strip().split()
    cmd = parts[0].lower().split("@")[0] if parts else ""
    args = parts[1:]

    if cmd in ("/status", "حالة", "المفاتيح"):
        send_telegram_message(token, chat_id, key_manager.get_status_summary() + "\n" + _models_state_text() + "\n" + _resilience_text() + "\n" + _pipeline_text(), reply_to_id=msg["message_id"])

    elif cmd == "/reset":
        n = model_circuit.reset()
        send_telegram_message(token, chat_id, f"🔓 تم فتح {n} قاطع. الموديلات كلها هتتجرّب من جديد في الطلب الجاي.", reply_to_id=msg["message_id"])

    elif cmd == "/keys":
        send_telegram_message(token, chat_id, _keys_list_text(), reply_to_id=msg["message_id"])

    elif cmd == "/remove":
        if not args:
            send_telegram_message(token, chat_id, "الاستخدام: /remove <اسم المشروع أو أول 8 أحرف من المفتاح>", reply_to_id=msg["message_id"])
            return
        removed = key_manager.remove_key(" ".join(args))
        if removed:
            send_telegram_message(token, chat_id, f"🗑️ تم حذف المفتاح [{removed.get('name')}] ({removed.get('key', '')[:8]}...).", reply_to_id=msg["message_id"])
        else:
            send_telegram_message(token, chat_id, "لم أجد مفتاحاً بهذا الاسم. جرّب /keys لعرض القائمة.", reply_to_id=msg["message_id"])

    elif cmd == "/rename":
        if len(args) < 2:
            send_telegram_message(token, chat_id, "الاستخدام: /rename <الاسم القديم> <الاسم الجديد>", reply_to_id=msg["message_id"])
            return
        entry = key_manager.rename_key(args[0], " ".join(args[1:]))
        if entry:
            send_telegram_message(token, chat_id, f"✏️ تمت إعادة التسمية إلى [{entry.get('name')}].", reply_to_id=msg["message_id"])
        else:
            send_telegram_message(token, chat_id, "لم أجد مفتاحاً بهذا الاسم. جرّب /keys.", reply_to_id=msg["message_id"])

    elif cmd == "/mode":
        if not args:
            send_telegram_message(token, chat_id, f"وضع الإرسال الحالي: {settings.delivery_mode()}\nالاستخدام: /mode dual | raw | polished", reply_to_id=msg["message_id"])
            return
        m = settings.set_delivery_mode(args[0])
        send_telegram_message(token, chat_id, f"✅ وضع الإرسال: {m}" if m else "قيمة غير معروفة. المتاح: dual | raw | polished", reply_to_id=msg["message_id"])

    elif cmd == "/header":
        if not args:
            send_telegram_message(token, chat_id, f"البانر: {settings.header_target()}\nالاستخدام: /header both | raw | polished | off", reply_to_id=msg["message_id"])
            return
        t = settings.set_header_target(args[0])
        send_telegram_message(token, chat_id, f"✅ البانر: {t}" if t else "قيمة غير معروفة. المتاح: both | raw | polished | off", reply_to_id=msg["message_id"])

    elif cmd == "/roles":
        send_telegram_message(token, chat_id, prompts.roles_list_text(settings.role()), reply_to_id=msg["message_id"])

    elif cmd == "/role":
        if not args:
            r = prompts.get_role(settings.role()) or {}
            send_telegram_message(token, chat_id, f"الدور الحالي: {settings.role()} — {r.get('name', '?')}\n/roles للقائمة · /role <id> للتفعيل", reply_to_id=msg["message_id"])
            return
        rid = args[0].lower()
        if prompts.get_role(rid) is None:
            send_telegram_message(token, chat_id, "دور غير معروف. /roles للقائمة.", reply_to_id=msg["message_id"])
            return
        settings.set_role(rid)
        send_telegram_message(token, chat_id, f"🎭 الدور الآن: {rid} — {prompts.get_role(rid)['name']} (يسري على الفويس القادم بلا إعادة تشغيل).", reply_to_id=msg["message_id"])

    elif cmd == "/new_role":
        parts3 = [x.strip() for x in " ".join(args).split("|")]
        if len(parts3) < 3 or not all(parts3[:3]):
            send_telegram_message(token, chat_id, "الاستخدام: /new_role <id> | <الاسم> | <تعليمات التنظيم> [| <سطر لطبقة التفريغ>]\nمثال: /new_role lawyer | محامي | نظّم النص قانونياً في بنود", reply_to_id=msg["message_id"])
            return
        try:
            r, created = prompts.add_role(parts3[0], parts3[1], parts3[3] if len(parts3) > 3 else "", parts3[2])
            send_telegram_message(token, chat_id, f"{'✅ أُنشئ' if created else '✏️ حُدِّث'} الدور [{parts3[0]}] — {r['name']}. فعّله بـ /role {parts3[0]}", reply_to_id=msg["message_id"])
        except ValueError as e:
            send_telegram_message(token, chat_id, f"❌ {e}", reply_to_id=msg["message_id"])

    elif cmd == "/role_del":
        if not args:
            send_telegram_message(token, chat_id, "الاستخدام: /role_del <id>", reply_to_id=msg["message_id"])
            return
        r = prompts.remove_role(args[0].lower())
        if r is None:
            send_telegram_message(token, chat_id, "لا يمكن: الدور غير موجود أو افتراضي محمي.", reply_to_id=msg["message_id"])
        else:
            if settings.role() == args[0].lower():
                settings.set_role(Config.DEFAULT_ROLE)
            send_telegram_message(token, chat_id, f"🗑️ حُذف الدور [{args[0]}].", reply_to_id=msg["message_id"])

    elif cmd == "/glossary":
        send_telegram_message(token, chat_id, _glossary_text(args[0] if args else "1"), reply_to_id=msg["message_id"])

    elif cmd == "/add_word":
        parsed = glossary_mod.Glossary.parse_add_args(" ".join(args))
        if not parsed:
            send_telegram_message(token, chat_id, "الاستخدام: /add_word شلبي = ChatGPT   (لقب → يظهر في المنظم فقط)\n/add_word جرافيك = Antigravity spelling   (تصحيح إملائي → الخام والمنظم)", reply_to_id=msg["message_id"])
            return
        lhs, rhs = parsed
        kind = "alias"
        rhs_parts = rhs.split()
        if rhs_parts and rhs_parts[-1].lower() in ("spelling", "إملائي", "تصحيح"):
            kind, rhs = "spelling", " ".join(rhs_parts[:-1])
        try:
            e, created = glossary_mod.get_glossary().add(lhs, rhs, kind=kind)
            scope_txt = "الخام والمنظم" if e["scope"] == "both" else "المنظم فقط"
            send_telegram_message(token, chat_id, f"{'✅ أُضيف' if created else '✏️ أُلحق بـ'} [{e['id']}] {lhs} → {e['replacement']} ({e['kind']}، {scope_txt}).", reply_to_id=msg["message_id"])
        except ValueError as err:
            send_telegram_message(token, chat_id, f"❌ {err}", reply_to_id=msg["message_id"])

    elif cmd == "/del_word":
        if not args:
            send_telegram_message(token, chat_id, "الاستخدام: /del_word <id أو لفظ> — ثم أكّد بـ /del_word <id> confirm", reply_to_id=msg["message_id"])
            return
        gl = glossary_mod.get_glossary()
        if args[-1].lower() == "confirm":
            e = gl.remove(" ".join(args[:-1]))
            send_telegram_message(token, chat_id, f"🗑️ حُذف [{e['id']}] {e['replacement']}." if e else "لم أجد المدخل.", reply_to_id=msg["message_id"])
        else:
            e = gl.find(" ".join(args))
            if e is None:
                send_telegram_message(token, chat_id, "لم أجد المدخل. /glossary للقائمة.", reply_to_id=msg["message_id"])
            else:
                send_telegram_message(token, chat_id, f"⚠️ حذف [{e['id']}] {'/'.join(e['patterns'])} → {e['replacement']}؟\nأكّد: /del_word {e['id']} confirm", reply_to_id=msg["message_id"])

    elif cmd == "/confirm_word":
        parsed = glossary_mod.Glossary.parse_add_args(" ".join(args))
        target, repl = (parsed if parsed else ((args[0] if args else ""), None))
        e = glossary_mod.get_glossary().confirm(target, repl)
        send_telegram_message(token, chat_id, f"✅ مؤكد: {'/'.join(e['patterns'])} → {e['replacement']}" if e else "الاستخدام: /confirm_word g_006 = Claude Code  (المدخل المعلَّق يحتاج المقصود)", reply_to_id=msg["message_id"])

    elif cmd == "/glossary_export":
        _send_document(token, chat_id, "glossary.json", glossary_mod.get_glossary().export_bytes(), "application/json", "📖 القاموس المخصص", msg["message_id"])

    elif cmd == "/last":
        entry = archive.last()
        if not entry:
            send_telegram_message(token, chat_id, "الأرشيف فارغ بعد.", reply_to_id=msg["message_id"])
            return
        body = f"🕓 {html.escape(entry['meta'])}\n<pre>{html.escape(entry['raw'])}</pre>"
        if entry.get("polished"):
            body += f"\n<b>📝 المنظم</b>\n<pre>{html.escape(entry['polished'])}</pre>"
        send_telegram_message(token, chat_id, body, reply_to_id=msg["message_id"], parse_mode="HTML")

    elif cmd == "/find":
        if not args:
            send_telegram_message(token, chat_id, "الاستخدام: /find <كلمة>", reply_to_id=msg["message_id"])
            return
        hits = archive.find(" ".join(args))
        if not hits:
            send_telegram_message(token, chat_id, "لا نتائج في الأرشيف.", reply_to_id=msg["message_id"])
        else:
            send_telegram_message(token, chat_id, "🔎 نتائج الأرشيف:\n" + "\n".join(f"• {h['meta']}\n  {h['line']}" for h in hits), reply_to_id=msg["message_id"])

    elif cmd == "/polish":
        src = (msg.get("reply_to_message") or {}).get("text") or " ".join(args)
        src = _strip_banner(src)
        if not src.strip():
            send_telegram_message(token, chat_id, "رُدّ بـ /polish على رسالة تفريغ (أو اكتب النص بعد الأمر).", reply_to_id=msg["message_id"])
            return
        import text_assist
        polished, reason = text_assist.polish(src, settings.role(), glossary_mod.get_glossary())
        if polished:
            send_telegram_message(token, chat_id, delivery.polished_message_html(polished, settings.role()), reply_to_id=msg["message_id"], parse_mode="HTML")
        else:
            send_telegram_message(token, chat_id, f"⚠️ تعذر التنظيم الآن ({reason}).", reply_to_id=msg["message_id"])

    elif cmd in ("/project_group", "/group"):
        # مرحلة 2 (04 §2.4): تثبيت مجموعة المشروع صراحةً في keys.json
        if len(args) < 2:
            groups = key_manager.groups_summary()
            cur = ("\nالمجموعات الحالية: " + " | ".join(f"{g}: {', '.join(n)}" for g, n in groups)) if groups else "\nلا توجد مجموعات بعد (كل مفتاح مستقل)."
            send_telegram_message(token, chat_id, "الاستخدام: /project_group <اسم المفتاح أو أول 8 أحرف> <اسم المجموعة>\n"
                                                  "مثال: /project_group \"Project 2\" proj-A  —  أو - لإزالة المجموعة" + cur,
                                  reply_to_id=msg["message_id"])
            return
        group = args[-1]
        target = " ".join(args[:-1]).strip('"')
        entry = key_manager.set_project_group(target, group)
        if entry is None:
            send_telegram_message(token, chat_id, "لم أجد مفتاحاً بهذا الاسم. جرّب /keys.", reply_to_id=msg["message_id"])
        elif entry.get("project_group"):
            sibs = [k.get("name") for k in key_manager.siblings(entry) if k is not entry]
            extra = f" مع: {', '.join(sibs)}" if sibs else " (أول عضو — أضف شقيقاً بنفس الاسم)"
            send_telegram_message(token, chat_id, f"🗂 [{entry.get('name')}] الآن في مجموعة [{entry['project_group']}]{extra}.", reply_to_id=msg["message_id"])
        else:
            send_telegram_message(token, chat_id, f"🗂 [{entry.get('name')}] أصبح مستقلاً (بلا مجموعة).", reply_to_id=msg["message_id"])

    elif cmd == "/backup":
        # نسخة عامة: الأرشيف + الجلسات + الحالة + القاموس/الإعدادات — **بلا keys.json أبداً** (13 S4)
        try:
            zip_bytes, included = backup.build_public_backup()
            if backup.contains_secret(zip_bytes):
                # دفاع في العمق: لو تسرب ملف سري لأي سبب لا نرسل شيئاً
                send_telegram_message(token, chat_id, "⛔ أُلغي الإرسال: النسخة العامة احتوت ملفاً سرياً — راجع الكود.", reply_to_id=msg["message_id"])
                return
            if not included:
                send_telegram_message(token, chat_id, "ℹ️ لا توجد بيانات للنسخ بعد (لا أرشيف ولا جلسات). للأسرار: /backup_secrets", reply_to_id=msg["message_id"])
                return
            name = f"backup_{time.strftime('%Y-%m-%d_%H%M')}.zip"
            caption = f"🗂️ نسخة احتياطية عامة — {len(included)} ملف، بلا أسرار.\nلمفاتيح API: /backup_secrets"
            _send_document(token, chat_id, name, zip_bytes, "application/zip", caption, msg["message_id"])
        except Exception as e:
            send_telegram_message(token, chat_id, f"⚠️ تعذر إنشاء النسخة الاحتياطية: {e}", reply_to_id=msg["message_id"])

    elif cmd == "/backup_secrets":
        # الأسرار بأمر منفصل + تأكيد صريح بكلمة confirm (09 §4)
        if not args or args[0].lower() != "confirm":
            send_telegram_message(
                token, chat_id,
                "🔐 هذا الأمر يرسل keys.json (مفاتيح Gemini + توكن تليجرام) إلى هذا الشات.\n"
                "لا تستخدمه إلا من جهازك الشخصي. للتأكيد اكتب:\n/backup_secrets confirm",
                reply_to_id=msg["message_id"],
            )
            return
        try:
            zip_bytes, included = backup.build_secrets_backup()
            if not included:
                send_telegram_message(token, chat_id, "ℹ️ لا يوجد keys.json بعد.", reply_to_id=msg["message_id"])
                return
            name = f"secrets_{time.strftime('%Y-%m-%d_%H%M')}.zip"
            caption = "🔐 نسخة الأسرار (keys.json). احذف هذه الرسالة بعد الحفظ ولا تشاركها."
            _send_document(token, chat_id, name, zip_bytes, "application/zip", caption, msg["message_id"])
        except Exception as e:
            send_telegram_message(token, chat_id, f"⚠️ تعذر إنشاء نسخة الأسرار: {e}", reply_to_id=msg["message_id"])

    elif cmd == "/version":
        send_telegram_message(token, chat_id, preflight.version_line(), reply_to_id=msg["message_id"])

    elif cmd in ("/queue", "الطابور"):
        send_telegram_message(token, chat_id, _queue_text(), reply_to_id=msg["message_id"])

    elif cmd in ("/start", "/help", "مساعدة"):
        send_telegram_message(token, chat_id, _help_text(), reply_to_id=msg["message_id"])


# ======================================================================
# الحلقة الرئيسية
# ======================================================================
def main(stop_event=None, pool=None, poll_params=None):
    """
    حلقة الـ poller (03b الطبقات 1–2):
    - Session لكل خيط + مهلات tuples؛ أي خطأ شبكي → netsession.reset() (قتل الـ socket) تلقائياً.
    - L0: ReadTimeout → وضع الحذر (poll 10ث لـ 5 دقائق) صامت.
    - backoff شبكي 3→6→12→30 بدل 3ث ثابتة؛ حدث net_degraded مرة واحدة.
    - 409 يُبلَّغ مرة واحدة في اللوج؛ 401 يوقف الـ poller نهائياً (التوكن باطل — لا فائدة من التكرار).
    - stop_event: يخرج بين الدورات (SIGTERM الرشيق في main.py)؛ pool يُمرَّر من الخارج ليبقى حياً عبر
      إعادة تشغيل الـ poller (L1) ولا تضيع مهام جارية.
    """
    bot_token, bot_user, allowed_chat_id = key_manager.get_telegram_config()

    if not bot_token:
        print("❌ خطأ: لم يتم العثور على TELEGRAM_BOT_TOKEN في keys.json أو متغيرات البيئة!")
        return

    allowed_chat_id = (allowed_chat_id or "").strip()
    if not allowed_chat_id or allowed_chat_id == "None":
        allowed_chat_id = ""
        print("⚠️ تحذير: TELEGRAM_CHAT_ID غير مضبوط — البوت سيرفض جميع الرسائل حتى تضبطه في البيئة أو .env")
        print("   (أرسل /start للبوت وسيُطبَع هنا Chat ID الخاص بك لتنسخه)")

    STATE["poller_generation"] += 1
    gen = STATE["poller_generation"]
    print("=" * 65)
    print(f"🤖 جاري تشغيل بوت التليجرام للتفريغ الصوتي الفوري... (poller #{gen})")
    print(f"👤 البوت: {bot_user}")
    print(f"🔒 مخصص للمستخدم: {allowed_chat_id or '(غير مضبوط)'}")
    print(f"🧵 عمليات تفريغ متوازية: {Config.BOT_WORKERS}")
    print("=" * 65)

    if STATE["started_at"] is None:
        STATE["started_at"] = time.time()
    STATE["alive"] = True
    STATE["stopped_reason"] = None
    own_pool = pool is None
    pool = pool or ThreadPoolExecutor(max_workers=max(1, Config.BOT_WORKERS), thread_name_prefix="transcribe")
    offset = None
    stop_event = stop_event or threading.Event()
    backoff = netsession.NetBackoff()
    conflict_reported = False
    degraded_reported = False
    heartbeat.beat("poller", note=f"start #{gen}")

    try:
        while not stop_event.is_set():
            try:
                poll_t = current_poll_timeout()
                params = {"timeout": poll_t}
                if poll_params:
                    params.update(poll_params)
                if offset:
                    params["offset"] = offset
                try:
                    res = _http_get(_api(bot_token, "getUpdates"), params=params,
                                    timeout=(Config.TG_CONNECT_TIMEOUT, poll_t + Config.TG_READ_MARGIN))
                except requests.exceptions.ReadTimeout as e:
                    # L0: socket ميت/نصف مفتوح — netsession أغلق الـ Session بالفعل؛ نهبط لوضع الحذر بصمت
                    STATE["read_timeouts"] += 1
                    STATE["net_errors"] += 1
                    STATE["last_error"] = f"ReadTimeout: {e}"
                    netsession.cautious.enter()
                    heartbeat.beat("poller", note="L0 read timeout → cautious")
                    print(f"⏱️ getUpdates ReadTimeout — Session جديدة + وضع الحذر (poll {Config.TG_POLL_TIMEOUT_CAUTIOUS}ث لـ {Config.CAUTIOUS_MODE_MINUTES} د)")
                    _wait(stop_event, backoff.next_delay())
                    continue
                except netsession.NETWORK_ERRORS as e:
                    STATE["net_errors"] += 1
                    STATE["last_error"] = f"{type(e).__name__}: {e}"
                    delay = backoff.next_delay()
                    heartbeat.beat("poller", note=f"net error → backoff {delay}s")
                    if backoff.degraded and not degraded_reported:
                        degraded_reported = True
                        print(f"📉 net_degraded: أخطاء شبكة متتالية ({type(e).__name__}) — backoff حتى {max(Config.NET_BACKOFF)}ث")
                    else:
                        print(f"⚠️ خطأ شبكة في getUpdates: {type(e).__name__}: {e} — إعادة بعد {delay}ث")
                    _wait(stop_event, delay)
                    continue

                heartbeat.beat("poller", note=f"getUpdates {res.status_code}")
                if res.status_code == 409:
                    if not conflict_reported:
                        conflict_reported = True
                        print("⛔ 409 Conflict: نسخة أخرى من البوت تعمل بنفس التوكن (محلي + استضافة؟). أوقف إحداهما. (يُبلَّغ مرة واحدة)")
                    STATE["last_error"] = "409 conflict: another instance is polling"
                    _wait(stop_event, 10)
                    continue
                if res.status_code == 401:
                    STATE["last_error"] = "401 Unauthorized: التوكن باطل"
                    STATE["stopped_reason"] = "401"
                    print("🛑 401 Unauthorized: توكن تليجرام باطل — إيقاف الـ poller (صحّح TELEGRAM_BOT_TOKEN وأعد التشغيل).")
                    return
                if res.status_code != 200:
                    STATE["last_error"] = f"getUpdates {res.status_code}"
                    _wait(stop_event, 2)
                    continue

                backoff.succeed()
                degraded_reported = False
                conflict_reported = False
                STATE["last_poll_ok"] = time.time()
                heartbeat.beat("poller", note="getUpdates 200")
                for up in res.json().get("result", []):
                    offset = up["update_id"] + 1          # نتقدم دائماً حتى لو فشلت معالجة الرسالة
                    cq = up.get("callback_query")
                    if cq:
                        cq_chat = str((cq.get("message") or {}).get("chat", {}).get("id", ""))
                        if allowed_chat_id and cq_chat == allowed_chat_id:
                            try:
                                handle_callback(bot_token, cq)
                            except Exception as e:
                                print(f"⚠️ خطأ في callback_query: {e}")
                        continue
                    msg = up.get("message")
                    if not msg:
                        continue
                    msg["_update_id"] = up["update_id"]
                    chat_id = str(msg["chat"]["id"])

                    if not allowed_chat_id or chat_id != allowed_chat_id:
                        print(f"⛔ رسالة محجوبة من مستخدم غير مصرح له: {chat_id}")
                        continue

                    audio = _is_audio_message(msg)
                    if audio:
                        file_id, mime_type, label = audio
                        try:
                            enqueue_telegram_audio(bot_token, chat_id, msg, file_id, mime_type, label)
                        except Exception as e:
                            print(f"⚠️ فشل إدراج الفويس في الطابور: {e}")
                            STATE["last_error"] = str(e)
                        continue

                    if msg.get("document"):
                        send_telegram_message(bot_token, chat_id, "📎 الملف ده مش صوتي. ابعت فويس نوت أو ملف صوتي (mp3, m4a, ogg, wav...).", reply_to_id=msg["message_id"])
                        continue

                    msg_text = msg.get("text", "")
                    if msg_text:
                        try:
                            handle_text(bot_token, chat_id, msg, msg_text)
                        except Exception as e:
                            print(f"⚠️ خطأ في معالجة أمر نصي: {e}")
                            STATE["last_error"] = str(e)
                if poll_t == 0:
                    # L3b short-poll: لا نغرق تليجرام — نفس قصير بين الطلبات
                    _wait(stop_event, Config.EMERGENCY_SHORT_POLL_INTERVAL)

            except Exception as e:
                # لا نبتلع الخطأ بصمت — نطبعه حتى يظهر في كونسول الاستضافة (P12)
                STATE["last_error"] = f"{type(e).__name__}: {e}"
                print(f"⚠️ خطأ في حلقة البوت: {type(e).__name__}: {e} — إعادة المحاولة بعد 3 ثوانٍ...")
                _wait(stop_event, 3)
        if STATE["stopped_reason"] is None:
            STATE["stopped_reason"] = "stop_event"
    finally:
        STATE["alive"] = False
        netsession.reset(reason=f"poller #{gen} exit")
        if own_pool:
            pool.shutdown(wait=False)


def _wait(stop_event, seconds):
    """نوم قابل للمقاطعة: يعود فوراً عند stop_event (لا نحجز SIGTERM خلف time.sleep)."""
    stop_event.wait(seconds)


if __name__ == "__main__":
    main()
