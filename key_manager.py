# -*- coding: utf-8 -*-
"""
مدير المفاتيح الذكي ودورة حياة الكوتا (Smart Key Pool & Quota Lifecycle Manager)
- يدير ملف keys.json الديناميكي
- يكتشف المفاتيح التلقائية المرسلة عبر الشات
- يدير التدوير التلقائي وفترات التهدئة (Cooldown) عند كود 429
- يحسب الإحصائيات والرصيد اليومي الفعلي
"""

import os
import re
import json
import time
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from config import Config

class KeyManager:
    def __init__(self):
        # قفل واحد يحمي keys.json من الكتابة المتزامنة (الويب والبوت في threads مختلفة)
        self._lock = threading.RLock()
        self.data = {
            "keys": [],
            "telegram": {
                "token": "",
                "username": "",
                "allowed_chat_id": Config.DEFAULT_CHAT_ID
            },
            "recordings_dir": Config.DEFAULT_RECORDINGS_DIR
        }
        self.load()

    def load(self):
        # 1. لو keys.json موجود نقرأه ثم ندمج معه أي مفاتيح جديدة من متغيرات البيئة
        #    (مهم على الاستضافة: تضيف مفتاح في اللوحة + Restart → يتضاف بدون مسح الحالة المحفوظة)
        if os.path.exists(Config.KEYS_JSON_FILE):
            try:
                with open(Config.KEYS_JSON_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                if self._merge_env_keys():
                    self.save()
                return
            except Exception as e:
                print(f"⚠️ خطأ أثناء قراءة keys.json: {e}")

        # 2. الهجرة التلقائية من keys.txt أو متغيرات البيئة إن لم يوجد keys.json
        self._bootstrap_from_legacy()
        self.save()

    @staticmethod
    def _env_keys():
        env_keys = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or ""
        return [x.strip() for x in env_keys.split(",") if x.strip()]

    def _merge_env_keys(self):
        """يضيف مفاتيح البيئة غير الموجودة في keys.json. يرجع True لو أضاف شيئاً."""
        keys_list = self.data.setdefault("keys", [])
        existing = {k.get("key") for k in keys_list}
        added = False
        for k in self._env_keys():
            if k in existing:
                continue
            new_id = len(keys_list) + 1
            keys_list.append({
                "id": new_id,
                "key": k,
                "name": f"Project {new_id}",
                "status": "active",
                "exhausted_until": 0,
                "success_count": 0,
                "last_used": None,
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "source": "env",
            })
            existing.add(k)
            added = True
            print(f"➕ تم دمج مفتاح جديد من متغيرات البيئة: {k[:8]}...{k[-4:]}")
        return added

    def save(self):
        """
        حفظ ذرّي (temp → rename) حتى لا يتلف الملف لو توقفت العملية أثناء الكتابة،
        + صلاحيات 600 لأن الملف يحوي مفاتيح API وتوكن تليجرام.
        """
        with self._lock:
            target = Config.KEYS_JSON_FILE
            directory = os.path.dirname(target) or "."
            tmp_path = None
            try:
                fd, tmp_path = tempfile.mkstemp(prefix=".keys-", suffix=".tmp", dir=directory)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    pass  # Windows لا يدعم الصلاحيات بنفس الطريقة — غير حرج
                os.replace(tmp_path, target)
            except Exception as e:
                print(f"❌ خطأ أثناء حفظ keys.json: {e}")
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def _bootstrap_from_legacy(self):
        raw_keys = []
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        bot_user = os.environ.get("TELEGRAM_BOT_USERNAME", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", Config.DEFAULT_CHAT_ID)
        rec_dir = os.environ.get("RECORDINGS_DIR", Config.DEFAULT_RECORDINGS_DIR)

        # قراءة keys.txt
        if os.path.exists(Config.KEYS_TXT_FILE):
            with open(Config.KEYS_TXT_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k in ["GEMINI_API_KEYS", "GEMINI_API_KEY"]:
                            raw_keys.extend([x.strip() for x in v.split(",") if x.strip()])
                        elif k == "TELEGRAM_BOT_TOKEN" and not bot_token:
                            bot_token = v
                        elif k == "TELEGRAM_BOT_USERNAME" and not bot_user:
                            bot_user = v
                        elif k == "TELEGRAM_CHAT_ID" and chat_id == Config.DEFAULT_CHAT_ID:
                            chat_id = v
                        elif k == "RECORDINGS_DIR":
                            rec_dir = v

        # قراءة متغيرات البيئة
        raw_keys.extend(self._env_keys())

        # تنظيف والتخلص من التكرار
        seen = set()
        clean_keys = []
        for idx, k in enumerate(raw_keys, 1):
            if k not in seen:
                seen.add(k)
                clean_keys.append({
                    "id": idx,
                    "key": k,
                    "name": f"Project {idx}",
                    "status": "active",
                    "exhausted_until": 0,
                    "success_count": 0,
                    "last_used": None
                })

        self.data["keys"] = clean_keys
        self.data["telegram"] = {
            "token": bot_token,
            "username": bot_user,
            "allowed_chat_id": chat_id
        }
        self.data["recordings_dir"] = rec_dir

    def get_telegram_config(self):
        # متغيرات البيئة لها الأولوية على الخادم
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or self.data.get("telegram", {}).get("token", "")
        username = os.environ.get("TELEGRAM_BOT_USERNAME") or self.data.get("telegram", {}).get("username", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID") or self.data.get("telegram", {}).get("allowed_chat_id", Config.DEFAULT_CHAT_ID)
        return token, username, str(chat_id)

    # ==================================================================
    # التهدئة لكل (مفتاح، موديل) + العدّاد اليومي
    # ==================================================================
    @staticmethod
    def _tz(name):
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception:
            return timezone.utc

    def _quota_day(self):
        """تاريخ اليوم بتوقيت تصفير كوتا Google (منتصف الليل Pacific)."""
        return datetime.now(self._tz(Config.DAILY_RESET_TZ)).strftime("%Y-%m-%d")

    def seconds_until_quota_reset(self):
        tz = self._tz(Config.DAILY_RESET_TZ)
        now = datetime.now(tz)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return int((tomorrow - now).total_seconds())

    def next_reset_display(self):
        """وقت التصفير القادم بتوقيت العرض المحلي (DISPLAY_TZ)."""
        tz_reset = self._tz(Config.DAILY_RESET_TZ)
        now = datetime.now(tz_reset)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return tomorrow.astimezone(self._tz(Config.DISPLAY_TZ)).strftime("%H:%M (%Y-%m-%d)")

    def _model_blocked_until(self, key_entry, model):
        """متى يفك الموديل على هذا المفتاح (0 = متاح). يراعي الحقول القديمة أيضاً."""
        until = 0
        if key_entry.get("status") == "exhausted":
            until = max(until, key_entry.get("exhausted_until", 0) or 0)
        until = max(until, (key_entry.get("exhausted_models") or {}).get(model, 0) or 0)
        return until

    def _usage_today(self, key_entry, model):
        usage = key_entry.get("daily_usage") or {}
        return (usage.get(self._quota_day()) or {}).get(model, 0)

    def is_model_available(self, key_entry, model, now=None):
        now = now or time.time()
        if self._model_blocked_until(key_entry, model) > now:
            return False
        quota = Config.MODEL_DAILY_QUOTA.get(model)
        if quota is not None and self._usage_today(key_entry, model) >= quota:
            return False
        return True

    def _expire_cooldowns(self, now):
        """يفك أي تهدئة انتهت (قديمة أو لكل موديل). يرجع True لو تغيّر شيء."""
        changed = False
        for k in self.data.get("keys", []):
            if k.get("status") == "exhausted" and now >= (k.get("exhausted_until", 0) or 0):
                k["status"] = "active"
                k["exhausted_until"] = 0
                changed = True
                print(f"🔄 تم إعادة تنشيط المفتاح [{k.get('name')}] تلقائياً بعد انتهاء فترة التهدئة.")
            em = k.get("exhausted_models") or {}
            expired = [m for m, until in em.items() if now >= (until or 0)]
            for m in expired:
                em.pop(m, None)
                changed = True
                print(f"🔄 الموديل [{m}] عاد متاحاً على المفتاح [{k.get('name')}].")
            if em != (k.get("exhausted_models") or {}):
                k["exhausted_models"] = em
        return changed

    def get_active_keys(self, model=None):
        """
        المفاتيح المتاحة الآن. لو مُرر model تُرجع فقط المفاتيح المتاحة لهذا الموديل
        (غير مقفولة عليه ولم تستنفد كوتاه اليومية).
        لو لا يوجد أي مفتاح متاح تُرجع قائمة فاضية (بدون محاولة يائسة تجدد القفل).
        """
        with self._lock:
            now = time.time()
            if self._expire_cooldowns(now):
                self.save()
            active = []
            for k in self.data.get("keys", []):
                if model is None:
                    if k.get("status", "active") == "active":
                        active.append(k)
                elif self.is_model_available(k, model, now):
                    active.append(k)
            return active

    def earliest_availability(self):
        """
        أقرب وقت (ثوانٍ) يصبح فيه أي (مفتاح، موديل) متاحاً، أو None لو لا مفاتيح.
        يراعي التهدئة وتصفير الكوتا اليومية.
        """
        with self._lock:
            keys = self.data.get("keys", [])
            if not keys:
                return None
            now = time.time()
            best = None
            for k in keys:
                for m in Config.MODELS:
                    until = self._model_blocked_until(k, m)
                    quota = Config.MODEL_DAILY_QUOTA.get(m)
                    quota_hit = quota is not None and self._usage_today(k, m) >= quota
                    if until <= now and not quota_hit:
                        return 0
                    wait = max(until - now, 0)
                    if quota_hit:
                        wait = max(wait, self.seconds_until_quota_reset())
                    best = wait if best is None else min(best, wait)
            return int(best) if best is not None else None

    def mark_key_exhausted(self, key_str, model=None, retry_after=None):
        """
        يقفل المفتاح. لو مُرر model يُقفل هذا الموديل فقط على هذا المفتاح
        (الموديلات الأخرى تبقى متاحة — كل موديل له كوتا مستقلة).
        retry_after: ثوانٍ من رد Google (retryDelay) إن وُجدت، وإلا الافتراضي من Config.
        """
        with self._lock:
            now = time.time()
            cooldown = retry_after if (retry_after and retry_after > 0) else Config.KEY_COOLDOWN_SECONDS
            until = now + cooldown
            for k in self.data.get("keys", []):
                if k.get("key") != key_str:
                    continue
                k["exhausted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if model:
                    em = k.setdefault("exhausted_models", {})
                    em[model] = until
                    # لو كل الموديلات مقفولة نعلّم المفتاح كله للتوافق مع العرض القديم
                    if all(em.get(m, 0) > now for m in Config.MODELS):
                        k["status"] = "exhausted"
                        k["exhausted_until"] = max(em.values())
                    label = f"الموديل [{model}] على المفتاح [{k.get('name')}]"
                else:
                    k["status"] = "exhausted"
                    k["exhausted_until"] = until
                    label = f"المفتاح [{k.get('name')}]"
                print(f"🔒 تم قفل {label} حتى: {datetime.fromtimestamp(until).strftime('%H:%M:%S')} ({int(cooldown // 60)} د)")
                self.save()
                break

    def record_success(self, key_str, model=None):
        with self._lock:
            for k in self.data.get("keys", []):
                if k.get("key") != key_str:
                    continue
                k["success_count"] = k.get("success_count", 0) + 1
                k["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if model:
                    day = self._quota_day()
                    usage = k.setdefault("daily_usage", {})
                    # نحتفظ باليوم الحالي فقط حتى لا يتضخم الملف
                    for old_day in [d for d in usage if d != day]:
                        usage.pop(old_day, None)
                    today = usage.setdefault(day, {})
                    today[model] = today.get(model, 0) + 1
                self.save()
                break

    def remaining_today(self, key_entry):
        """المتبقي التقديري اليوم لكل موديل على مفتاح معيّن."""
        out = {}
        for m in Config.MODELS:
            quota = Config.MODEL_DAILY_QUOTA.get(m)
            if quota is None:
                continue
            out[m] = max(quota - self._usage_today(key_entry, m), 0)
        return out

    def remove_key(self, name_or_prefix):
        """يحذف مفتاحاً بالاسم (غير حساس لحالة الأحرف) أو بأول 8 أحرف منه. يرجع المدخل المحذوف أو None."""
        with self._lock:
            keys = self.data.get("keys", [])
            target = (name_or_prefix or "").strip()
            if not target:
                return None
            for i, k in enumerate(keys):
                if k.get("name", "").lower() == target.lower() or k.get("key", "").startswith(target):
                    removed = keys.pop(i)
                    self.save()
                    return removed
            return None

    def rename_key(self, old_name, new_name):
        with self._lock:
            new_name = (new_name or "").strip()
            if not new_name:
                return None
            for k in self.data.get("keys", []):
                if k.get("name", "").lower() == (old_name or "").strip().lower():
                    k["name"] = new_name
                    self.save()
                    return k
            return None

    @staticmethod
    def extract_key_from_text(text):
        """
        يكتشف بذكاء إذا كان النص يحتوي على مفتاح Gemini API
        ويستخرج الاسم والمفتاح تلقائياً
        """
        text = text.strip()
        # نمط مفاتيح Google: تبدأ بـ AQ. أو AIzaSy بطول محدد
        key_pattern = r"(?<![A-Za-z0-9_-])(AQ\.[a-zA-Z0-9_-]{40,65}|AIzaSy[a-zA-Z0-9_-]{33})(?![A-Za-z0-9_-])"
        match = re.search(key_pattern, text)
        if not match:
            return None, None

        api_key = match.group(1).strip()
        
        # استخراج الاسم إن وجد قبل المفتاح مثل: Project 3: AQ... أو مشروع 3: AQ...
        prefix = text[:match.start()].strip()
        name = None
        if prefix:
            prefix = re.sub(r"[:=-]+$", "", prefix).strip()
            if prefix and len(prefix) < 40:
                name = prefix

        return api_key, name

    def add_key(self, api_key, name=None):
        with self._lock:
            keys_list = self.data.get("keys", [])

            # التحقق إن كان موجوداً مسبقاً
            for k in keys_list:
                if k.get("key") == api_key:
                    if name:
                        k["name"] = name
                    k["status"] = "active"
                    k["exhausted_until"] = 0
                    self.save()
                    return k, False

            # إضافة مفتاح جديد
            new_id = len(keys_list) + 1
            key_entry = {
                "id": new_id,
                "key": api_key,
                "name": name or f"Project {new_id}",
                "status": "active",
                "exhausted_until": 0,
                "success_count": 0,
                "last_used": None,
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            keys_list.append(key_entry)
            self.data["keys"] = keys_list
            self.save()
            return key_entry, True

    @staticmethod
    def _short_model(m):
        return m.replace("gemini-", "").replace("-flash", " Flash").replace("-transcribe", " Transcribe")

    def get_status_summary(self):
        """
        تقرير نصي عادي (بدون Markdown — يُرسل كـ HTML آمن أو نص خام).
        يعرض لكل مفتاح: المتبقي اليوم لكل موديل، وأي قفل مؤقت ومتى يفك.
        """
        with self._lock:
            now = time.time()
            self._expire_cooldowns(now)
            keys = self.data.get("keys", [])
            total = len(keys)
            per_key_quota = sum(Config.MODEL_DAILY_QUOTA.get(m, 0) for m in Config.MODELS)

            total_remaining = 0
            available_keys = 0
            for k in keys:
                rem = self.remaining_today(k)
                total_remaining += sum(
                    v for m, v in rem.items() if self._model_blocked_until(k, m) <= now
                )
                if any(self.is_model_available(k, m, now) for m in Config.MODELS):
                    available_keys += 1

            lines = [
                "📊 لوحة حالة المنظومة الذكية 360°",
                "─" * 30,
                f"🔑 إجمالي المفاتيح: {total}",
                f"🟢 مفاتيح متاحة الآن: {available_keys}",
                f"⚡ المتبقي التقديري اليوم: {total_remaining} / {total * per_key_quota} ريكورد",
                f"🕛 تصفير الكوتا القادم: {self.next_reset_display()} بتوقيت {Config.DISPLAY_TZ}",
                "─" * 30,
            ]

            for k in keys:
                key_masked = k.get("key", "")[:8] + "..." + k.get("key", "")[-4:]
                lines.append(f"• {k.get('name')}  [{key_masked}]  عمليات: {k.get('success_count', 0)}")
                rem = self.remaining_today(k)
                for m in Config.MODELS:
                    until = self._model_blocked_until(k, m)
                    quota = Config.MODEL_DAILY_QUOTA.get(m, 0)
                    if until > now:
                        mins = max(1, int((until - now) // 60))
                        state = f"⏳ مقفول {mins} د"
                    elif rem.get(m, 1) <= 0:
                        state = "🔴 نفدت لليوم"
                    else:
                        state = f"🟢 {rem.get(m)}/{quota}"
                    lines.append(f"    {self._short_model(m)}: {state}")

            wait = self.earliest_availability()
            if keys and wait:
                lines.append("─" * 30)
                lines.append(f"⏱ أقرب مفتاح يعود للعمل بعد: {wait // 60} د")

            lines.append("─" * 30)
            lines.append("🤖 الموديلات: 3.8 Flash ➔ 3.6 Flash ➔ 3.5 Transcribe (بدون Lite)")
            return "\n".join(lines)

# نسخة مفردة (Singleton)
key_manager = KeyManager()
