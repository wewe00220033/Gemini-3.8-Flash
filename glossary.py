# -*- coding: utf-8 -*-
"""
القاموس المخصص (Glossary) — مواصفة 05 (المرحلة 3a).

الهدف: تحويل ألقاب المالك وكلماته المنطوقة إلى المصطلح الصحيح **بأمان**، بلا "بحث واستبدال أعمى".

- نوعان: `spelling` (تصحيح إملائي — يُطبَّق على الخام والمنظم) و`alias` (لقب/كناية — المنظم فقط افتراضياً؛
  الخام يبقى كما نُطق = أمانة النص).
- `scope` لكل مدخل: both / polished_only / raw_only.
- مطابقة بحدود الكلمة مع السوابق العربية (و/ف/ب/ل/ال/وال/بال/لل) — السابقة تُحفظ ("وشلبي" → "وChatGPT").
- تطبيع الهمزات/الياء/التاء المربوطة في **المطابقة فقط** لا في النص الناتج.
- `context_rules`: {"if_near_any": [...], "then": "skip"} ضمن ±5 كلمات (أوبس قرب "ستوديو" = OBS لا Opus).
- الأنماط الأطول أولاً (منع "فلاش" من أكل "فلاش لايت").
- `hits` يزداد عند كل استبدال؛ `status`: confirmed / pending (المعلَّق لا يُطبَّق حتى يؤكده المالك).
- ملف تالف → الافتراضي يُحمَّل ويُبلَّغ (on_corrupt) ولا انهيار. Thread-safe (RLock) وكتابة ذرية.
"""

import re
import json
import threading
from datetime import datetime, timezone

from config import Config
import store

SCHEMA_VERSION = 1
PREFIXES = ("وال", "بال", "لل", "ال", "و", "ف", "ب", "ل")
_PREFIX_RE = "(?:" + "|".join(sorted(PREFIXES, key=len, reverse=True)) + ")?"
# حدود الكلمة: حروف عربية (الأحرف فقط — لا علامات الترقيم العربية مثل ، ؛ ؟) + \w
_AR_LETTER = r"\u0621-\u064A\u066E-\u06D3"
_WB_L = r"(?<![\w" + _AR_LETTER + r"])"
_WB_R = r"(?![\w" + _AR_LETTER + r"])"
_NEAR_WORDS = 5

# القيم الافتراضية: المؤكد فقط (05 §1)؛ غير المؤكد pending ليؤكده المالك من البوت
DEFAULT_ENTRIES = [
    {"id": "g_001", "patterns": ["شلبي", "شلباي", "شلبى"], "replacement": "ChatGPT",
     "kind": "alias", "scope": "polished_only", "status": "confirmed", "context_rules": []},
    {"id": "g_002", "patterns": ["جرافيك", "انتي جرافيتي", "أنتي جرافيتي", "AntiGrafitti", "Antigrafitti"],
     "replacement": "Antigravity", "kind": "spelling", "scope": "both", "status": "confirmed",
     "context_rules": [{"if_near_any": ["جرافيكس", "تصميم", "ديزاين", "كارت"], "then": "skip"}]},
    {"id": "g_003", "patterns": ["فلاش"], "replacement": "Gemini Flash",
     "kind": "alias", "scope": "polished_only", "status": "confirmed",
     "context_rules": [{"if_near_any": ["فلاشة", "يو اس بي", "USB", "ميموري", "لايت"], "then": "skip"}]},
    {"id": "g_004", "patterns": ["سانتا"], "replacement": "Sonnet",
     "kind": "alias", "scope": "polished_only", "status": "confirmed",
     "context_rules": [{"if_near_any": ["كلوز", "كريسماس", "بابا نويل"], "then": "skip"}]},
    {"id": "g_005", "patterns": ["أوبس", "اوبس", "أوبوس", "اوبوس"], "replacement": "Opus",
     "kind": "alias", "scope": "polished_only", "status": "confirmed",
     "context_rules": [{"if_near_any": ["ستوديو", "تسجيل الشاشة", "بث", "استريم"], "then": "skip"}]},
    # غير مؤكد المقصود (05 §1) — معلَّق حتى يؤكده المالك (/glossary يعرضه بعلامة ⏳)
    {"id": "g_006", "patterns": ["فابل", "فافل"], "replacement": "?", "kind": "alias", "scope": "polished_only",
     "status": "pending", "context_rules": [], "note": "المالك: «تبع كلود — ف أ ب ل». المقصود غير مؤكد (Claude Code؟)"},
    {"id": "g_007", "patterns": ["أسترا", "استرا"], "replacement": "?", "kind": "alias", "scope": "polished_only",
     "status": "pending", "context_rules": [], "note": "المالك: «تبع ChatGPT». المقصود غير مؤكد"},
]

_NORM_TABLE = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})
_DIACRITICS = re.compile(r"[\u064B-\u0652\u0640]")


def normalize(s):
    """تطبيع للمطابقة فقط: أ/إ/آ → ا، ى → ي، ة → ه، إزالة التشكيل والتطويل، حروف صغيرة."""
    return _DIACRITICS.sub("", str(s)).translate(_NORM_TABLE).lower()


def normalize_keep_len(text):
    """تطبيع حرف-بحرف يحافظ على الطول (للمطابقة بمواضع صحيحة على النص الأصلي). التشكيل → مسافة."""
    return _DIACRITICS.sub(" ", text.translate(_NORM_TABLE).lower())


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_doc():
    return {"schema_version": SCHEMA_VERSION, "entries": [dict(e, hits=0, created=_now()) for e in DEFAULT_ENTRIES]}


class Glossary:
    def __init__(self, path=None, on_corrupt=None):
        self._lock = threading.RLock()
        self.path = path or Config.GLOSSARY_FILE
        self._on_corrupt = on_corrupt
        self.corrupt_notice = None
        self.doc = {"schema_version": SCHEMA_VERSION, "entries": []}
        self._compiled = []     # [(entry, regex)] الأطول أولاً
        self.reload()

    # ------------------------------------------------------------------
    # التحميل / الحفظ
    # ------------------------------------------------------------------
    def reload(self):
        with self._lock:
            def _corrupt(path, dest, err):   # توقيع store.load_json_tolerant: (path, dest, err)
                self.corrupt_notice = f"⚠️ glossary.json تالف ({err}) — حُمِّل القاموس الافتراضي (النسخة التالفة محفوظة بجانبه)."
                print(self.corrupt_notice)
                if self._on_corrupt:
                    try:
                        self._on_corrupt(self.corrupt_notice)
                    except Exception:
                        pass
            doc = store.load_json_tolerant(self.path, default=None, on_corrupt=_corrupt)
            if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
                doc = _default_doc()
                self._write(doc)
            self.doc = doc
            self._compile()

    def _write(self, doc=None):
        try:
            store.atomic_write_json(self.path, doc if doc is not None else self.doc)
        except Exception as e:  # pragma: no cover
            print(f"⚠️ glossary.json: تعذّر الحفظ ({e})")

    def save(self):
        with self._lock:
            self._write()

    def _compile(self):
        compiled = []
        for e in self.doc.get("entries", []):
            pats = [str(p).strip() for p in (e.get("patterns") or []) if p and str(p).strip()]
            if not pats or not e.get("replacement"):
                continue
            alts = sorted({normalize(p) for p in pats}, key=len, reverse=True)
            rx = re.compile(_WB_L + "(" + _PREFIX_RE + ")(" + "|".join(re.escape(a) for a in alts) + ")" + _WB_R,
                            re.IGNORECASE)
            compiled.append((e, rx, max(len(a) for a in alts)))
        compiled.sort(key=lambda t: t[2], reverse=True)     # الأنماط الأطول أولاً (05 §3 قاعدة 4)
        self._compiled = [(e, rx) for e, rx, _ in compiled]

    # ------------------------------------------------------------------
    # التطبيق
    # ------------------------------------------------------------------
    @staticmethod
    def _scope_of(entry):
        return entry.get("scope") or ("polished_only" if entry.get("kind") == "alias" else "both")

    def apply(self, text, target="polished", count_hits=True):
        """
        يطبّق القاموس على النص. target: "raw" أو "polished".
        يرجع (النص الجديد، عدد الاستبدالات). المعلَّق (pending) لا يُطبَّق.
        """
        if not text:
            return text, 0
        with self._lock:
            compiled = list(self._compiled)
        total = 0
        hit_entries = []
        for entry, rx in compiled:
            if entry.get("status", "confirmed") != "confirmed":
                continue
            scope = self._scope_of(entry)
            if (scope == "polished_only" and target != "polished") or (scope == "raw_only" and target != "raw"):
                continue
            text, n = self._apply_entry(text, entry, rx)
            if n:
                total += n
                hit_entries.append((entry, n))
        if hit_entries and count_hits:
            with self._lock:
                for entry, n in hit_entries:
                    entry["hits"] = int(entry.get("hits", 0) or 0) + n
                self._write()
        return text, total

    def _apply_entry(self, text, entry, rx):
        """نطابق على النسخة المطبَّعة (نفس الطول حرفاً بحرف) ونستبدل في النص الأصلي مع حفظ السابقة."""
        norm = normalize_keep_len(text)
        rules = entry.get("context_rules") or []
        out, last, n = [], 0, 0
        for m in rx.finditer(norm):
            if rules and self._skip_by_context(norm, m.start(), m.end(), rules):
                continue
            prefix = text[m.start(1):m.end(1)]
            out.append(text[last:m.start()])
            out.append(prefix + entry["replacement"])
            last = m.end()
            n += 1
        if not n:
            return text, 0
        out.append(text[last:])
        return "".join(out), n

    @staticmethod
    def _skip_by_context(norm, start, end, rules):
        before = norm[:start].split()[-_NEAR_WORDS:]
        after = norm[end:].split()[:_NEAR_WORDS]
        window = " " + " ".join(before + after) + " "
        for r in rules:
            if (r.get("then") or "skip") != "skip":
                continue
            for w in r.get("if_near_any") or []:
                if normalize(w) in window:
                    return True
        return False

    def hints_for_prompt(self, limit=8):
        """تلميح إملائي قصير لطبقة A (05 §5): الأسماء الإنجليزية المؤكدة من نوع spelling فقط."""
        with self._lock:
            names = [e["replacement"] for e in self.doc.get("entries", [])
                     if e.get("status") == "confirmed" and e.get("kind") == "spelling"
                     and re.search(r"[A-Za-z]", e.get("replacement", ""))]
        names = list(dict.fromkeys(names))[:limit]
        return ("أسماء تقنية قد تُنطق بالعامية، اكتبها بالإنجليزية: " + ", ".join(names)) if names else ""

    # ------------------------------------------------------------------
    # الإدارة (أوامر البوت)
    # ------------------------------------------------------------------
    def entries(self, sort_by_hits=True):
        with self._lock:
            ents = list(self.doc.get("entries", []))
        if sort_by_hits:
            ents.sort(key=lambda e: (-int(e.get("hits", 0) or 0), e.get("id", "")))
        return ents

    def _next_id(self):
        nums = [int(e["id"][2:]) for e in self.doc.get("entries", []) if re.fullmatch(r"g_\d+", str(e.get("id", "")))]
        return f"g_{(max(nums) + 1 if nums else 1):03d}"

    def add(self, pattern, replacement, kind="alias", scope=None, status="confirmed", context_rules=None):
        """يضيف مدخلاً (أو يضيف النمط لمدخل له نفس replacement والنوع). يرجع (entry, created)."""
        pattern = (pattern or "").strip()
        replacement = (replacement or "").strip()
        if not pattern or not replacement:
            raise ValueError("اللفظ والمقصود مطلوبان")
        kind = "spelling" if kind in ("spelling", "إملائي", "تصحيح", "s") else "alias"
        scope = scope or ("both" if kind == "spelling" else "polished_only")
        with self._lock:
            for e in self.doc["entries"]:
                if e.get("replacement", "").lower() == replacement.lower() and e.get("kind") == kind:
                    if normalize(pattern) not in {normalize(p) for p in e.get("patterns", [])}:
                        e.setdefault("patterns", []).append(pattern)
                    e["status"] = status
                    self._compile()
                    self._write()
                    return e, False
            entry = {"id": self._next_id(), "patterns": [pattern], "replacement": replacement, "kind": kind,
                     "scope": scope, "status": status, "context_rules": list(context_rules or []), "hits": 0,
                     "created": _now()}
            self.doc["entries"].append(entry)
            self._compile()
            self._write()
            return entry, True

    def find(self, id_or_pattern):
        q = (id_or_pattern or "").strip()
        if not q:
            return None
        qn = normalize(q)
        with self._lock:
            for e in self.doc.get("entries", []):
                if e.get("id") == q or qn in {normalize(p) for p in e.get("patterns", [])} \
                        or e.get("replacement", "").lower() == q.lower():
                    return e
        return None

    def remove(self, id_or_pattern):
        with self._lock:
            e = self.find(id_or_pattern)
            if e is None:
                return None
            self.doc["entries"] = [x for x in self.doc["entries"] if x is not e]
            self._compile()
            self._write()
            return e

    def confirm(self, id_or_pattern, replacement=None):
        """يؤكد مدخلاً معلَّقاً (مع تحديد المقصود إن أُعطي). يرجع None لو لا مقصود صالح."""
        with self._lock:
            e = self.find(id_or_pattern)
            if e is None:
                return None
            if replacement:
                e["replacement"] = replacement.strip()
            if e.get("replacement") in ("", "?"):
                return None
            e["status"] = "confirmed"
            e.pop("note", None)
            self._compile()
            self._write()
            return e

    def export_bytes(self):
        with self._lock:
            return json.dumps(self.doc, ensure_ascii=False, indent=2).encode("utf-8")

    @staticmethod
    def parse_add_args(text):
        """'شلبي = ChatGPT' أو 'شلبي -> ChatGPT' أو 'شلبي: ChatGPT' → (lhs, rhs) أو None."""
        m = re.match(r"^\s*(.+?)\s*(?:=|->|→|:)\s*(.+?)\s*$", text or "")
        return (m.group(1), m.group(2)) if m else None


_singleton = None
_singleton_lock = threading.Lock()


def get_glossary():
    """Singleton كسول (يُنشأ عند أول استخدام ويتبع Config.GLOSSARY_FILE — مهم للاختبارات)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None or _singleton.path != Config.GLOSSARY_FILE:
            _singleton = Glossary()
        return _singleton
