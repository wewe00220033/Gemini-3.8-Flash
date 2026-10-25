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
}

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
    url = f"https://api.telegram.org/bot{token}/{method}"
    return requests.post(url, json=payload, timeout=timeout or Config.TELEGRAM_TIMEOUT)


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
    info = requests.get(
        f"https://api.telegram.org/bot{token}/getFile",
        params={"file_id": file_id},
        timeout=Config.TELEGRAM_TIMEOUT,
    ).json()
    file_path = info.get("result", {}).get("file_path")
    if not file_path:
        raise RuntimeError("تعذر الحصول على مسار الملف من تليجرام")
    resp = requests.get(
        f"https://api.telegram.org/file/bot{token}/{file_path}",
        timeout=Config.TELEGRAM_DOWNLOAD_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.content


def handle_audio(token, chat_id, msg, file_id, mime_type, label):
    """يعمل داخل ThreadPool."""
    heartbeat.beat("worker", note="job start")
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
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data={"chat_id": chat_id, "caption": caption, "reply_to_message_id": reply_to_id},
        files={"document": (filename, data_bytes, mime)},
        timeout=Config.TELEGRAM_TIMEOUT,
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
def main(stop_event=None):
    bot_token, bot_user, allowed_chat_id = key_manager.get_telegram_config()

    if not bot_token:
        print("❌ خطأ: لم يتم العثور على TELEGRAM_BOT_TOKEN في keys.json أو متغيرات البيئة!")
        return

    allowed_chat_id = (allowed_chat_id or "").strip()
    if not allowed_chat_id or allowed_chat_id == "None":
        allowed_chat_id = ""
        print("⚠️ تحذير: TELEGRAM_CHAT_ID غير مضبوط — البوت سيرفض جميع الرسائل حتى تضبطه في البيئة أو .env")
        print("   (أرسل /start للبوت وسيُطبَع هنا Chat ID الخاص بك لتنسخه)")

    print("=" * 65)
    print("🤖 جاري تشغيل بوت التليجرام للتفريغ الصوتي الفوري...")
    print(f"👤 البوت: {bot_user}")
    print(f"🔒 مخصص للمستخدم: {allowed_chat_id or '(غير مضبوط)'}")
    print(f"🧵 عمليات تفريغ متوازية: {Config.BOT_WORKERS}")
    print("=" * 65)

    STATE["started_at"] = time.time()
    STATE["alive"] = True
    pool = ThreadPoolExecutor(max_workers=max(1, Config.BOT_WORKERS), thread_name_prefix="transcribe")
    offset = None
    stop_event = stop_event or threading.Event()

    try:
        while not stop_event.is_set():
            try:
                params = {"timeout": 30}
                if offset:
                    params["offset"] = offset
                res = requests.get(
                    f"https://api.telegram.org/bot{bot_token}/getUpdates",
                    params=params,
                    timeout=40,
                )
                heartbeat.beat("poller", note=f"getUpdates {res.status_code}")
                if res.status_code == 409:
                    print("⛔ 409 Conflict: نسخة أخرى من البوت تعمل بنفس التوكن (محلي + استضافة؟). أوقف إحداهما.")
                    STATE["last_error"] = "409 conflict: another instance is polling"
                    time.sleep(10)
                    continue
                if res.status_code != 200:
                    STATE["last_error"] = f"getUpdates {res.status_code}"
                    time.sleep(2)
                    continue

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
                time.sleep(3)
    finally:
        STATE["alive"] = False
        pool.shutdown(wait=False)


if __name__ == "__main__":
    main()
