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
from datetime import datetime
from config import Config

class KeyManager:
    def __init__(self):
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
        # 1. لو ملف keys.json موجود نقرأه مباشرة
        if os.path.exists(Config.KEYS_JSON_FILE):
            try:
                with open(Config.KEYS_JSON_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                return
            except Exception as e:
                print(f"⚠️ خطأ أثناء قراءة keys.json: {e}")

        # 2. الهجرة التلقائية من keys.txt أو .env أو متغيرات البيئة إن لم يوجد keys.json
        self._bootstrap_from_legacy()
        self.save()

    def save(self):
        try:
            with open(Config.KEYS_JSON_FILE, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"❌ خطأ أثناء حفظ keys.json: {e}")

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
        env_keys = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY")
        if env_keys:
            raw_keys.extend([x.strip() for x in env_keys.split(",") if x.strip()])

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

    def get_active_keys(self):
        now = time.time()
        active = []
        changed = False

        for k in self.data.get("keys", []):
            # فحص انتهاء فترة التهدئة للمفاتيح المستهلكة
            if k.get("status") == "exhausted":
                if now >= k.get("exhausted_until", 0):
                    k["status"] = "active"
                    k["exhausted_until"] = 0
                    changed = True
                    print(f"🔄 تم إعادة تنشيط المفتاح [{k.get('name')}] تلقائياً بعد انتهاء فترة التهدئة.")
            if k.get("status") == "active":
                active.append(k)

        if changed:
            self.save()

        # لو كل المفاتيح مستهلكة، نرجعهم كلهم مؤقتاً لتجنب التوقف
        if not active and self.data.get("keys"):
            print("⚠️ جميع المفاتيح في فترة تهدئة، جاري المحاولة بجميع المفاتيح كحل أخير...")
            return self.data.get("keys", [])

        return active

    def mark_key_exhausted(self, key_str):
        now = time.time()
        cooldown_until = now + Config.KEY_COOLDOWN_SECONDS
        for k in self.data.get("keys", []):
            if k.get("key") == key_str:
                k["status"] = "exhausted"
                k["exhausted_until"] = cooldown_until
                k["exhausted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"🔒 تم قفل المفتاح [{k.get('name')}] حتى: {datetime.fromtimestamp(cooldown_until).strftime('%H:%M:%S')}")
                self.save()
                break

    def record_success(self, key_str):
        for k in self.data.get("keys", []):
            if k.get("key") == key_str:
                k["success_count"] = k.get("success_count", 0) + 1
                k["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.save()
                break

    @staticmethod
    def extract_key_from_text(text):
        """
        يكتشف بذكاء إذا كان النص يحتوي على مفتاح Gemini API
        ويستخرج الاسم والمفتاح تلقائياً
        """
        text = text.strip()
        # نمط مفاتيح Google: تبدأ بـ AQ. أو AIzaSy بطول محدد
        key_pattern = r"(AQ\.[a-zA-Z0-9_-]{40,65}|AIzaSy[a-zA-Z0-9_-]{33})"
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

    def get_status_summary(self):
        keys = self.data.get("keys", [])
        total = len(keys)
        active_count = sum(1 for k in keys if k.get("status") == "active")
        exhausted_count = total - active_count

        total_daily_quota = total * 65 # 20 (3.8) + 20 (3.6) + 25 (3.5 transcribe)

        lines = [
            "📊 **لوحة حالة المنظومة الذكية 360°**",
            "─" * 32,
            f"🔑 **إجمالي المفاتيح:** `{total}` مشاريع",
            f"🟢 **المفاتيح النشطة الآن:** `{active_count}`",
            f"⏳ **المفاتيح في فترة تهدئة (429):** `{exhausted_count}`",
            f"⚡ **إجمالي الرصيد اليومي الفائق:** `{total_daily_quota}` ريكورد/يوم",
            "─" * 32,
            "**قائمة المشاريع والمفاتيح:**"
        ]

        now = time.time()
        for k in keys:
            status_icon = "🟢 نشط" if k.get("status") == "active" else "⏳ تهدئة"
            extra = ""
            if k.get("status") == "exhausted":
                rem = max(0, int(k.get("exhausted_until", 0) - now))
                extra = f" (متبقي {rem // 60} د)"
            
            key_masked = k.get("key", "")[:8] + "..." + k.get("key", "")[-4:]
            lines.append(f"• **{k.get('name')}**: `{key_masked}` | {status_icon}{extra} | عمليات: `{k.get('success_count', 0)}`")

        lines.append("─" * 32)
        lines.append(f"🤖 **الموديلات النشطة:** `3.8 Flash` ➔ `3.6 Flash` ➔ `3.5 Transcribe`")
        return "\n".join(lines)

# نسخة مفردة (Singleton)
key_manager = KeyManager()
