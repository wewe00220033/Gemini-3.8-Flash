# -*- coding: utf-8 -*-
"""
قاطع الدائرة على مستوى الموديل (Model Circuit Breaker) — حادثة 01.

الفكرة: لما موديل في Google يرجّع 5xx أو يعمل Timeout، المشكلة في الموديل نفسه مش في المفتاح.
فبدل ما نجرّب كل المفاتيح عليه (ونضيّع دقايق)، نقفله مؤقتاً على كل المفاتيح وننزل للموديل التالي.

- في الذاكرة فقط (مش في keys.json): إعادة تشغيل البرنامج تمسح الحالة — مقصود، لأن Google ممكن ترجع.
- Thread-safe: البوت بيشغّل أكتر من تفريغ بالتوازي.
- يحتفظ بآخر المحاولات للعرض في /status.
"""

import time
import threading
from collections import deque
from datetime import datetime


class ModelCircuit:
    def __init__(self, history_size=10):
        self._lock = threading.RLock()
        self._paused = {}            # model -> {"until", "reason", "since", "hits"}
        self._history = deque(maxlen=history_size)

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

    def reset(self, model=None):
        """يفتح قاطع موديل واحد أو الكل. يرجع عدد القواطع التي فُتحت."""
        with self._lock:
            if model is None:
                n = len([m for m, e in self._paused.items() if e.get("until", 0) > time.time()])
                self._paused.clear()
                return n
            return 1 if self._paused.pop(model, None) else 0

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
            out.append({
                "model": m,
                "paused": left > 0,
                "remaining_s": left,
                "reason": entry.get("reason") if left > 0 else None,
                "until": datetime.fromtimestamp(entry["until"]).strftime("%H:%M:%S") if left > 0 else None,
                "hits": entry.get("hits", 0) if left > 0 else 0,
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
