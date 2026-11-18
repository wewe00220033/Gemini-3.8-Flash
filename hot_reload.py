# -*- coding: utf-8 -*-
"""
hot_reload.py — مراقب كسول لبصمة الملفات `(st_mtime_ns, st_size)` عند الطلب بلا خيوط خلفية (T3b — DEC-P2-09).

- صفر خيوط `daemon`: لا استهلاك للذاكرة ولا تسريب خيوط بين الاختبارات أو عند إغلاق الحاوية.
- يفرض فاصل تهدئة أدنى `>= 5.0` ثوانٍ دائماً في التشغيل العادي، مع دعم `force=True` الصريح في الاختبارات.
- يدعم `mark_loaded()` عقب الكتابة الذاتية لمنع إعادة التحميل الوهمية، و`mark_bad()` لتسجيل تحذير واحد لكل بصمة تالفة.
"""

import os
import threading
import time

MIN_POLL_INTERVAL_S = 5.0


class FileStampWatcher:
    """يتتبع `(st_mtime_ns, st_size)` لملف واحد بأمان تزامني وفاصل تهدئة `>= 5` ثوانٍ."""

    def __init__(self, poll_interval_s=MIN_POLL_INTERVAL_S):
        self._lock = threading.RLock()
        self._poll_interval_s = max(MIN_POLL_INTERVAL_S, float(poll_interval_s or MIN_POLL_INTERVAL_S))
        self._path = None
        self._loaded_sig = None
        self._bad_sig = None
        self._last_check_mono = 0.0

    @property
    def poll_interval_s(self):
        return self._poll_interval_s

    def set_poll_interval(self, seconds):
        with self._lock:
            if seconds is None or seconds == float("inf"):
                self._poll_interval_s = float("inf")
            else:
                self._poll_interval_s = max(MIN_POLL_INTERVAL_S, float(seconds))

    def reset(self):
        with self._lock:
            self._path = None
            self._loaded_sig = None
            self._bad_sig = None
            self._last_check_mono = 0.0

    @staticmethod
    def _stat_sig(path):
        if not path:
            return None
        try:
            st = os.stat(path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def check(self, path, force=False):
        """
        يفحص حالة الملف ويعيد `(state, sig)`:
        - `("throttled", last_sig)`: لم يمضِ فاصل التهدئة ولم يُطلب `force=True`.
        - `("missing", None)`: الملف غير موجود على القرص.
        - `("unchanged", sig)`: البصمة مطابقة لآخر نسخة سليمة محمّلة.
        - `("changed", sig)`: الملف جديد أو تغيّرت بصمته `(st_mtime_ns, st_size)`.
        """
        now = time.monotonic()
        with self._lock:
            same_path = (self._path == path)
            if same_path and self._loaded_sig is not None and not force:
                if (now - self._last_check_mono) < self._poll_interval_s:
                    return ("throttled", self._loaded_sig)

            self._last_check_mono = now
            sig = self._stat_sig(path)
            if sig is None:
                self._path = path
                self._loaded_sig = None
                self._bad_sig = None
                return ("missing", None)

            if same_path and self._loaded_sig == sig:
                return ("unchanged", sig)

            return ("changed", sig)

    def mark_loaded(self, path, sig=None):
        """يسجّل البصمة الحالية كنسخة سليمة (يُستدعى بعد التحميل الناجح أو بعد الكتابة الذاتية للملف)."""
        with self._lock:
            actual_sig = self._stat_sig(path) if sig is None else sig
            self._path = path
            self._loaded_sig = actual_sig
            self._bad_sig = None
            self._last_check_mono = time.monotonic()
            return actual_sig

    def mark_bad(self, sig):
        """يرجع `True` في المرة الأولى فقط لهذه البصمة التالفة (لمنع تكرار التحذير في السجل)."""
        with self._lock:
            if self._bad_sig == sig:
                return False
            self._bad_sig = sig
            return True

