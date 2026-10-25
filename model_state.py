# -*- coding: utf-8 -*-
"""
قاطع الدائرة على مستوى الموديل (Model Circuit Breaker) — حادثة 01 + Failover v2 (مرحلة 2، مواصفة 04).

الفكرة: لما موديل في Google يرجّع 5xx أو يعمل Timeout، المشكلة في الموديل نفسه مش في المفتاح.
فبدل ما نجرّب كل المفاتيح عليه (ونضيّع دقايق)، نقفله مؤقتاً على كل المفاتيح وننزل للموديل التالي.

مرحلة 2 أضافت:
- سلم قواطع متدرج لأخطاء 5xx: strike_5xx() يقفل 20 → 45 → 90 → 180 ثانية حسب عدد الضربات المتتالية،
  وعداد الضربات يُصفَّر عند أول نجاح (mark_success).
- استمرار اختياري على القرص (state/circuit.json) عبر attach_store(): إعادة التشغيل لا تنسى قاطعاً
  مفتوحاً ولا الضربات — لكن المنتهي صلاحيته يُهمَل عند التحميل. بدون attach_store = ذاكرة فقط (كما كان).
- Thread-safe: كل القراءة/الكتابة تحت RLock واحد، والكتابة للقرص ذرية عبر store.atomic_write_json.
- يحتفظ بآخر المحاولات للعرض في /status.
"""

import time
import threading
from collections import deque
from datetime import datetime

try:
    import store
except ImportError:  # pragma: no cover - الأداة المحلية بدون store
    store = None


class ModelCircuit:
    def __init__(self, history_size=10, persist_path=None):
        self._lock = threading.RLock()
        self._paused = {}            # model -> {"until", "reason", "since", "hits"}
        self._strikes = {}           # model -> عدد ضربات 5xx المتتالية (يُصفَّر عند النجاح)
        self._history = deque(maxlen=history_size)
        self._persist_path = None
        if persist_path:
            self.attach_store(persist_path)

    # ------------------------------------------------------------------
    # الاستمرار على القرص (اختياري)
    # ------------------------------------------------------------------
    def attach_store(self, path):
        """يفعّل الحفظ على القرص ويحمّل الحالة السابقة (القواطع غير المنتهية + الضربات)."""
        with self._lock:
            self._persist_path = path
            self._load()

    def detach_store(self):
        with self._lock:
            self._persist_path = None

    def _load(self):
        if not self._persist_path or store is None:
            return
        data = store.load_json_tolerant(self._persist_path, default={})
        if not isinstance(data, dict):
            return
        now = time.time()
        paused = data.get("paused") or {}
        if isinstance(paused, dict):
            for model, e in paused.items():
                if not isinstance(e, dict):
                    continue
                try:
                    until = float(e.get("until", 0))
                except (TypeError, ValueError):
                    continue
                if until > now:
                    self._paused[model] = {
                        "until": until,
                        "reason": e.get("reason") or "persisted",
                        "since": float(e.get("since") or now),
                        "hits": int(e.get("hits") or 1),
                    }
        strikes = data.get("strikes") or {}
        if isinstance(strikes, dict):
            for model, n in strikes.items():
                try:
                    n = int(n)
                except (TypeError, ValueError):
                    continue
                if n > 0:
                    self._strikes[model] = n

    def _save(self):
        """يُستدعى دائماً تحت القفل. فشل الكتابة لا يكسر التفريغ — تحذير فقط."""
        if not self._persist_path or store is None:
            return
        now = time.time()
        active = {m: e for m, e in self._paused.items() if e.get("until", 0) > now}
        try:
            store.atomic_write_json(self._persist_path, {
                "paused": active,
                "strikes": dict(self._strikes),
                "saved_at": now,
            })
        except Exception as e:  # pragma: no cover - قرص ممتلئ/صلاحيات
            print(f"⚠️ circuit.json: تعذّر الحفظ ({e.__class__.__name__}: {e})")

    # ------------------------------------------------------------------
    # القفل / الفتح
    # ------------------------------------------------------------------
    def pause(self, model, seconds, reason):
        """يقفل الموديل لمدة seconds. لو كان مقفولاً بالفعل تُمدَّد المدة (ولا تُقصَّر)."""
        if not seconds or seconds <= 0:
            return
        with self._lock:
            now = time.time()
            entry = self._paused.get(model)
            until = now + seconds
            if entry and entry.get("until", 0) > now:
                entry["until"] = max(entry["until"], until)
                entry["reason"] = reason
                entry["hits"] = entry.get("hits", 0) + 1
            else:
                self._paused[model] = {"until": until, "reason": reason, "since": now, "hits": 1}
            self._save()

    def strike_5xx(self, model, ladder, reason="5xx"):
        """ضربة 5xx جديدة على الموديل: يرفع العداد ويقفل حسب السلم (20, 45, 90, 180).

        الضربة n تقفل ladder[n-1]، وما بعد آخر درجة يثبت على أعلى درجة. يرجع ثواني القفل.
        """
        ladder = tuple(ladder) or (20,)
        with self._lock:
            n = self._strikes.get(model, 0) + 1
            self._strikes[model] = n
            seconds = ladder[min(n, len(ladder)) - 1]
            self.pause(model, seconds, f"{reason} ×{n}")   # pause() يحفظ على القرص
            return seconds

    def strikes(self, model):
        with self._lock:
            return self._strikes.get(model, 0)

    def mark_success(self, model):
        """نجاح على الموديل = تصفير عداد ضرباته (ولا يمس القواطع)."""
        with self._lock:
            if self._strikes.pop(model, None) is not None:
                self._save()

    def reset(self, model=None):
        """يفتح قاطع موديل واحد أو الكل (ويصفّر الضربات). يرجع عدد القواطع التي فُتحت."""
        with self._lock:
            if model is None:
                n = len([m for m, e in self._paused.items() if e.get("until", 0) > time.time()])
                self._paused.clear()
                self._strikes.clear()
            else:
                n = 1 if self._paused.pop(model, None) else 0
                self._strikes.pop(model, None)
            self._save()
            return n

    # ------------------------------------------------------------------
    # الاستعلام
    # ------------------------------------------------------------------
    def is_open(self, model, now=None):
        """True = الموديل مقفول حالياً (يُتخطّى)."""
        return self.remaining(model, now) > 0

    def remaining(self, model, now=None):
        """الثواني المتبقية على فتح الموديل (0 = متاح)."""
        now = now or time.time()
        with self._lock:
            entry = self._paused.get(model)
            if not entry:
                return 0
            left = entry.get("until", 0) - now
            if left <= 0:
                self._paused.pop(model, None)
                return 0
            return int(left) + 1

    def reason(self, model):
        with self._lock:
            entry = self._paused.get(model)
            return entry.get("reason") if entry else None

    def snapshot(self, models):
        """قائمة قابلة للعرض/JSON لكل موديل: متاح أم مقفول ولماذا وحتى متى."""
        now = time.time()
        out = []
        for m in models:
            left = self.remaining(m, now)
            with self._lock:
                entry = self._paused.get(m) or {}
                strikes = self._strikes.get(m, 0)
            out.append({
                "model": m,
                "paused": left > 0,
                "remaining_s": left,
                "reason": entry.get("reason") if left > 0 else None,
                "until": datetime.fromtimestamp(entry["until"]).strftime("%H:%M:%S") if left > 0 else None,
                "hits": entry.get("hits", 0) if left > 0 else 0,
                "strikes": strikes,
            })
        return out

    def earliest_open(self, models):
        """أقرب (ثوانٍ، موديل) يفتح — أو (0, None) لو في موديل متاح الآن."""
        best = None
        for m in models:
            left = self.remaining(m)
            if left == 0:
                return 0, m
            if best is None or left < best[0]:
                best = (left, m)
        return best or (0, None)

    # ------------------------------------------------------------------
    # سجل المحاولات (للشفافية في /status)
    # ------------------------------------------------------------------
    def record_attempt(self, model, key_name, outcome, elapsed):
        with self._lock:
            self._history.appendleft({
                "at": datetime.now().strftime("%H:%M:%S"),
                "model": model,
                "key": key_name,
                "outcome": outcome,
                "elapsed_s": round(elapsed, 1),
            })

    def recent_attempts(self, limit=5):
        with self._lock:
            return list(self._history)[:limit]


# Singleton مشترك بين البوت والويب والأداة المحلية
model_circuit = ModelCircuit()
