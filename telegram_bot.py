# -*- coding: utf-8 -*-
"""
بوت تليجرام للتفريغ الصوتي الذكي وإدارة المفاتيح 360°
- استقبال الفويس نوت والتفريغ الفوري بكود بلوك قابل للنسخ
- كشف المفاتيح التلقائي وإضافتها للـ Key Pool بمجرد إرسالها نصياً في الشات
- أوامر: /status  /keys  /remove <اسم>  /rename <قديم> <جديد>  /help
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
}
_in_flight_lock = threading.Lock()


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
    if msg.get("video_note"):
        v = msg["video_note"]
        return v["file_id"], v.get("mime_type", "video/mp4"), "video_note"
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


def handle_audio(token, chat_id, msg, file_id, mime_type, label):
    """يعمل داخل ThreadPool."""
    heartbeat.beat("worker", note="job start")
    with _in_flight_lock:
        STATE["in_flight"] += 1
    try:
        _handle_audio_inner(token, chat_id, msg, file_id, mime_type, label)
    finally:
        with _in_flight_lock:
            STATE["in_flight"] -= 1
        heartbeat.beat("worker", note="job end")


def _handle_audio_inner(token, chat_id, msg, file_id, mime_type, label):
    try:
        print(f"🎙️ تم استلام تسجيل صوتي ({label}) من {chat_id}...")
        with ProgressKeeper(token, chat_id, reply_to_id=msg["message_id"]):
            audio_bytes = _download_file(token, file_id)
            text, model, key_name, info = transcribe_audio_bytes_ex(audio_bytes, mime_type)
        print(f"✅ تم التفريغ عبر [{model}] بالمفتاح [{key_name}] في {info.get('elapsed')} ث: {text[:40]}...")
        send_transcript(token, chat_id, text, model, key_name, reply_to_id=msg["message_id"], info=info)
        STATE["processed"] += 1
    except Exception as e:
        print(f"❌ خطأ تفريغ: {e}")
        STATE["last_error"] = str(e)
        send_telegram_message(token, chat_id, f"❌ عذراً، فشل التفريغ:\n{e}", reply_to_id=msg["message_id"])


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
        "/backup — نسخة احتياطية (أرشيف+جلسات+إعدادات) بلا أسرار\n"
        "/backup_secrets — نسخة keys.json بتأكيد صريح\n"
        "/version — رقم النسخة والبناء ومدة التشغيل\n"
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
        send_telegram_message(token, chat_id, key_manager.get_status_summary() + "\n" + _models_state_text(), reply_to_id=msg["message_id"])

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
                params = {"timeout": netsession.cautious.poll_timeout()}
                if poll_params:
                    params.update(poll_params)
                if offset:
                    params["offset"] = offset
                try:
                    res = _http_get(_api(bot_token, "getUpdates"), params=params, timeout=netsession.cautious.timeouts())
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
                    msg = up.get("message")
                    if not msg:
                        continue
                    chat_id = str(msg["chat"]["id"])

                    if not allowed_chat_id or chat_id != allowed_chat_id:
                        print(f"⛔ رسالة محجوبة من مستخدم غير مصرح له: {chat_id}")
                        continue

                    audio = _is_audio_message(msg)
                    if audio:
                        file_id, mime_type, label = audio
                        pool.submit(handle_audio, bot_token, chat_id, msg, file_id, mime_type, label)
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
