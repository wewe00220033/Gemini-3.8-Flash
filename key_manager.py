# -*- coding: utf-8 -*-
"""
مدير المفاتيح الذكي ودورة حياة الكوتا (Smart Key Pool & Quota Lifecycle Manager)
- يدير ملف keys.json الديناميكي
- يكتشف المفاتيح التلقائية المرسلة عبر الشات
- يدير التدوير التلقائي وفترات التهدئة (Cooldown) عند كود 429
- يحسب الإحصائيات والرصيد اليومي الفعلي
- مرحلة 2 (مواصفة 04 §2.4/§3): مجموعات المشاريع project_group — نفاد يومي/429 على مفتاح يُعلَّم
  على كل مفاتيح مجموعته (نفس مشروع Google = نفس الحصة)، اكتشاف تلقائي للمجموعات، ودفتر الحصص
  (normal / saver / freeze).

Thread-safety: كل تعديل على self.data يتم تحت self._lock (RLock) وينتهي بـ save() واحد ذري.
"""

import os
import re
import json
import time
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from config import Config

try:
    import store
except ImportError:  # pragma: no cover
    store = None

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
        # مجموعات مكتشفة تلقائياً: key_str -> group  (تُحفظ في state/key_groups.json)
        self._auto_groups = {}
        # آخر نفاد يومي لكل موديل: model -> [(ts, key_str), ...] لنافذة الاكتشاف
        self._daily_exhaust_log = {}
        self._invalid_notified = set()
        self.load()
        self._load_auto_groups()

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

    # ==================================================================
    # مجموعات المشاريع (project_group) — مواصفة 04 §2.4
    # ==================================================================
    def group_of(self, key_entry):
        """
        مجموعة المفتاح: (1) project_group الصريح؛ (2) وسم project؛ (3) مجموعة مكتشفة تلقائياً؛
        (4) وإلا المفتاح نفسه مجموعة مستقلة (السلوك القديم — آمن بلا كسر).
        """
        if not isinstance(key_entry, dict):
            return None
        explicit = (key_entry.get("project_group") or "").strip()
        if explicit:
            return explicit
        tag = (key_entry.get("project") or "").strip()
        if tag:
            return tag
        key_str = key_entry.get("key") or ""
        auto = self._auto_groups.get(key_str)
        if auto:
            return auto
        return "key:" + key_str

    def group_label(self, key_entry):
        """اسم المجموعة للعرض أو None لو المفتاح مستقل (لا نعرض شيئاً زائداً)."""
        g = self.group_of(key_entry)
        return None if not g or g.startswith("key:") else g

    def siblings(self, key_entry):
        """كل المفاتيح في نفس مجموعة key_entry (بما فيها هو). يُستدعى تحت القفل."""
        g = self.group_of(key_entry)
        return [k for k in self.data.get("keys", []) if self.group_of(k) == g]

    def _find_key(self, key_str):
        for k in self.data.get("keys", []):
            if k.get("key") == key_str:
                return k
        return None

    def _resolve(self, name_or_prefix):
        """مفتاح بالاسم (غير حساس للحالة) أو ببادئة المفتاح أو بالرقم id."""
        target = (name_or_prefix or "").strip()
        if not target:
            return None
        for k in self.data.get("keys", []):
            if k.get("name", "").lower() == target.lower() or k.get("key", "").startswith(target):
                return k
            if target.isdigit() and str(k.get("id")) == target:
                return k
        return None

    def set_project_group(self, name_or_prefix, group):
        """
        /project_group <key> <group>: يثبّت المجموعة صراحةً في keys.json.
        group فارغ أو '-' يزيل المجموعة (يرجع المفتاح مستقلاً). يرجع المدخل أو None.
        """
        with self._lock:
            k = self._resolve(name_or_prefix)
            if k is None:
                return None
            group = (group or "").strip()
            if group in ("", "-", "none", "بدون"):
                k.pop("project_group", None)
                self._auto_groups.pop(k.get("key", ""), None)
                self._save_auto_groups()
            else:
                k["project_group"] = group[:40]
            self.save()
            return k

    def _load_auto_groups(self):
        path = getattr(Config, "KEY_GROUPS_FILE", None)
        if not path or store is None:
            return
        data = store.load_json_tolerant(path, default={})
        groups = data.get("groups") if isinstance(data, dict) else None
        if isinstance(groups, dict):
            self._auto_groups = {str(k): str(v) for k, v in groups.items() if v}

    def _save_auto_groups(self):
        """يُستدعى تحت القفل. يكتب state/key_groups.json ذرياً (المفاتيح مقنّعة لا تُكتب كاملة)."""
        path = getattr(Config, "KEY_GROUPS_FILE", None)
        if not path or store is None:
            return
        try:
            store.atomic_write_json(path, {
                "groups": dict(self._auto_groups),
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:  # pragma: no cover
            print(f"⚠️ key_groups.json: تعذّر الحفظ ({e})")

    def _autodetect_group(self, key_entry, model, now):
        """
        نفاد يومي على (model, key) الآن: لو مفتاح آخر نفد يومياً على نفس الموديل خلال
        KEY_GROUP_AUTODETECT_WINDOW ثانية → غالباً نفس مشروع Google → ندمجهما في مجموعة واحدة.
        يُستدعى تحت القفل. يرجع اسم المجموعة الجديدة أو None.
        """
        if not getattr(Config, "KEY_GROUP_AUTODETECT", True):
            return None
        window = getattr(Config, "KEY_GROUP_AUTODETECT_WINDOW", 60)
        key_str = key_entry.get("key") or ""
        log = self._daily_exhaust_log.setdefault(model, [])
        log[:] = [(ts, k) for ts, k in log if now - ts <= window and k != key_str]
        merged = None
        for _, other_str in log:
            other = self._find_key(other_str)
            if other is None or self.group_of(other) == self.group_of(key_entry):
                continue
            # لا ندمج مفتاحاً له project_group صريح مع مجموعة أخرى — قرار المالك يعلو
            if key_entry.get("project_group") or other.get("project_group") or key_entry.get("project"):
                continue
            g_other = self.group_of(other)
            group = g_other if not g_other.startswith("key:") else f"auto-{other.get('id', '?')}"
            # ندمج كل أعضاء مجموعة المفتاح الحالي في مجموعة الآخر
            for member in self.siblings(key_entry):
                self._auto_groups[member.get("key") or ""] = group
            if g_other.startswith("key:"):
                self._auto_groups[other_str] = group
            merged = group
            print(f"🔗 اكتشاف تلقائي: [{key_entry.get('name')}] و[{other.get('name')}] نفدا يومياً معاً على {model} → مجموعة {group}")
        log.append((now, key_str))
        if merged:
            self._save_auto_groups()
        return merged

    def _model_blocked_until(self, key_entry, model):
        """متى يفك الموديل على هذا المفتاح (0 = متاح). يراعي الحقول القديمة أيضاً."""
        until = 0
        if key_entry.get("status") in ("exhausted", "invalid"):
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
            if k.get("status") in ("exhausted", "invalid") and now >= (k.get("exhausted_until", 0) or 0):
                k["status"] = "active"
                k["exhausted_until"] = 0
                k.pop("invalid_reason", None)
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

    def mark_key_exhausted(self, key_str, model=None, retry_after=None, daily=False):
        """
        يقفل المفتاح. لو مُرر model يُقفل هذا الموديل فقط على هذا المفتاح
        (الموديلات الأخرى تبقى متاحة — كل موديل له كوتا مستقلة).
        retry_after: ثوانٍ من رد Google (retryDelay) إن وُجدت، وإلا الافتراضي من Config.
        daily=True: الكوتا اليومية (RPD) خلصت → القفل حتى تصفير Google (منتصف الليل Pacific).
        """
        with self._lock:
            now = time.time()
            if daily:
                cooldown = max(60, self.seconds_until_quota_reset())
            elif retry_after and retry_after > 0:
                cooldown = retry_after
            else:
                cooldown = Config.KEY_COOLDOWN_SECONDS
            until = now + cooldown
            k = self._find_key(key_str)
            if k is None:
                return
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            k["exhausted_at"] = stamp
            if model:
                if daily:
                    self._autodetect_group(k, model, now)
                # نفاد يومي أو 429 معدل على الموديل = على المشروع كله → نعلّم كل الأشقاء (04 §2.4)
                targets = self.siblings(k)
                for t in targets:
                    em = t.setdefault("exhausted_models", {})
                    em[model] = max(em.get(model, 0) or 0, until)
                    # لو كل الموديلات مقفولة نعلّم المفتاح كله للتوافق مع العرض القديم
                    if all(em.get(m, 0) > now for m in Config.MODELS):
                        t["status"] = "exhausted"
                        t["exhausted_until"] = max(em.values())
                    if t is not k:
                        t["exhausted_at"] = stamp
                label = f"الموديل [{model}] على المفتاح [{k.get('name')}]"
                if len(targets) > 1:
                    label += f" + {len(targets) - 1} شقيق في مجموعة [{self.group_label(k)}]"
            else:
                k["status"] = "exhausted"
                k["exhausted_until"] = until
                label = f"المفتاح [{k.get('name')}]"
            print(f"🔒 تم قفل {label} حتى: {datetime.fromtimestamp(until).strftime('%H:%M:%S')} ({int(cooldown // 60)} د)")
            self.save()

    def mark_key_invalid(self, key_str, reason=""):
        """
        Google رفضت المفتاح نفسه (400 API key not valid / 401 / 403 مسرّب أو بلا صلاحية).
        يُقفل المفتاح كله (كل الموديلات) لمدة INVALID_KEY_COOLDOWN_SECONDS ويُعلَّم status="invalid"
        حتى يظهر في /keys بعلامة ❌. لا يُحذف تلقائياً — القرار للمالك (/remove).
        """
        with self._lock:
            now = time.time()
            until = now + Config.INVALID_KEY_COOLDOWN_SECONDS
            for k in self.data.get("keys", []):
                if k.get("key") != key_str:
                    continue
                k["status"] = "invalid"
                k["exhausted_until"] = until
                k["invalid_reason"] = (reason or "")[:120]
                k["exhausted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"❌ Google رفضت المفتاح [{k.get('name')}] ({reason[:60] if reason else 'invalid'}) — مقفول 24 ساعة. احذفه بـ /remove لو مش هيرجع.")
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

    # ==================================================================
    # دفتر الحصص (Quota Ledger) — مواصفة 04 §3
    # ==================================================================
    def quota_ledger(self):
        """
        ملخص مجمّع للمتبقي التقريبي اليوم عبر كل المفاتيح/الموديلات:
        {"remaining": N, "total": T, "per_model": {model: n}, "ratio": r, "mode": normal|saver|freeze,
         "reset_in_s": s}
        المتبقي يحسب فقط ما هو متاح فعلاً اليوم (يستبعد المقفول حتى تصفير الكوتا أو الباطل).
        """
        with self._lock:
            now = time.time()
            keys = self.data.get("keys", [])
            per_model = {m: 0 for m in Config.MODELS if Config.MODEL_DAILY_QUOTA.get(m) is not None}
            total = 0
            reset_in = self.seconds_until_quota_reset()
            for k in keys:
                invalid = k.get("status") == "invalid" and (k.get("exhausted_until", 0) or 0) > now
                rem = self.remaining_today(k)
                for m, quota in Config.MODEL_DAILY_QUOTA.items():
                    if m not in per_model:
                        continue
                    total += quota
                    if invalid:
                        continue
                    until = self._model_blocked_until(k, m)
                    # قفل يمتد حتى التصفير = نفاد يومي فعلي (حتى لو العدّاد المحلي أقل)
                    if until - now >= reset_in - 5:
                        continue
                    per_model[m] += rem.get(m, 0)
            remaining = sum(per_model.values())
            ratio = (remaining / total) if total else 0.0
            saver = getattr(Config, "QUOTA_SAVER_RATIO", 0.30)
            if remaining <= 0:
                mode = "freeze"
            elif ratio <= saver:
                mode = "saver"
            else:
                mode = "normal"
            return {
                "remaining": remaining,
                "total": total,
                "per_model": per_model,
                "ratio": round(ratio, 3),
                "mode": mode,
                "reset_in_s": reset_in,
            }

    def quota_mode(self):
        return self.quota_ledger()["mode"]

    def remaining_total(self):
        return self.quota_ledger()["remaining"]

    def quota_ledger_text(self):
        """سطر العرض: "متبقي تقريبي اليوم ~31 فويس (3.6 Flash: 14، 3.5 Transcribe: 17) — يتصفر بعد 6س"."""
        led = self.quota_ledger()
        parts = "، ".join(f"{self._short_model(m)}: {n}" for m, n in led["per_model"].items())
        h, rem = divmod(led["reset_in_s"], 3600)
        when = f"{h}س" if h else f"{max(1, rem // 60)}د"
        mode_tag = {"saver": " · 🟡 وضع التوفير", "freeze": " · 🧊 متجمد حتى التصفير"}.get(led["mode"], "")
        return f"متبقي تقريبي اليوم ~{led['remaining']} فويس ({parts}) — يتصفر بعد {when}{mode_tag}"

    def groups_summary(self):
        """[(group, [names])] للمجموعات التي تضم أكثر من مفتاح — للعرض الصامت في /status."""
        with self._lock:
            groups = {}
            for k in self.data.get("keys", []):
                label = self.group_label(k)
                if label:
                    groups.setdefault(label, []).append(k.get("name", "?"))
            return sorted(groups.items())

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

            lines.insert(5, f"📒 {self.quota_ledger_text()}")

            for k in keys:
                key_masked = k.get("key", "")[:8] + "..." + k.get("key", "")[-4:]
                flag = "  ❌ مرفوض من Google" if k.get("status") == "invalid" else ""
                grp = self.group_label(k)
                grp_txt = f"  🗂 {grp}" if grp else ""
                lines.append(f"• {k.get('name')}  [{key_masked}]  عمليات: {k.get('success_count', 0)}{grp_txt}{flag}")
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

            groups = self.groups_summary()
            if groups:
                lines.append("─" * 30)
                lines.append("🗂 مجموعات المشاريع (حصة مشتركة): " + " | ".join(
                    f"{g}: {', '.join(names)}" for g, names in groups))

            lines.append("─" * 30)
            lines.append(f"🤖 الموديلات: {self.models_chain_text()}")
            return "\n".join(lines)

    @classmethod
    def models_chain_text(cls):
        """نص الموديلات بالترتيب يتولد من Config (مثال: 3.6 Flash ➔ 3.5 Transcribe) + المعطّلة."""
        chain = " ➔ ".join(cls._short_model(m) for m in Config.MODELS)
        disabled = sorted(getattr(Config, "DISABLED_MODELS", set()) or [])
        if disabled:
            chain += f" (معطّل: {', '.join(cls._short_model(m) for m in disabled)} — بدون Lite)"
        else:
            chain += " (بدون Lite)"
        return chain

# نسخة مفردة (Singleton)
key_manager = KeyManager()
