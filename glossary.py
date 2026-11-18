# -*- coding: utf-8 -*-
"""
القاموس المخصص (Glossary) — مواصفة 05 (المرحلة 3a).

الهدف: تحويل ألقاب المالك وكلماته المنطوقة إلى المصطلح الصحيح **بأمان**، بلا "بحث واستبدال أعمى".

- نوعان: `spelling` (تصحيح إملائي — يُطبَّق على الخام والمنظم) و`alias` (لقب/كناية — المنظم فقط افتراضياً؛
  الخام يبقى كما نُطق = أمانة النص).
- `scope` لكل مدخل: both / polished_only / raw_only.
- مطابقة بحدود الكلمة مع السوابق العربية (و/ف/ب/ل/ال/وال/بال/لل)، متصلة أو بمسافة واحدة؛ قبل بديل لاتيني: وصلة «ـ » للسوابق المتصلة ومسافة لـ و/ف، والعربية بلا تغيير.
- تطبيع الهمزات/الياء/التاء المربوطة في **المطابقة فقط** لا في النص الناتج.
- `context_rules`: {"if_near_any": [...], "then": "skip"} ضمن ±5 كلمات (أوبس قرب "ستوديو" = OBS لا Opus).
- الأنماط الأطول أولاً (منع "فلاش" من أكل "فلاش لايت").
- `hits` يزداد عند كل استبدال؛ `status`: confirmed / pending (المعلَّق لا يُطبَّق حتى يؤكده المالك).
- ملف تالف → الافتراضي يُحمَّل ويُبلَّغ (on_corrupt) ولا انهيار. Thread-safe (RLock) وكتابة ذرية.
"""

import copy
import logging
import re
import json
import threading
from datetime import datetime, timezone

from config import Config
from hot_reload import FileStampWatcher
import store

logger = logging.getLogger("alsami.glossary")


def _audit(action, **fields):
    """09 §2 قناة audit: لا تكسر العملية أبداً لو فشل التسجيل."""
    try:
        import audit
        audit.record(action, **fields)
    except Exception:
        pass

SCHEMA_VERSION = 1
PREFIXES = ("وال", "بال", "لل", "ال", "و", "ف", "ب", "ل")
_PREFIX_RE = "(?:" + "|".join(sorted(PREFIXES, key=len, reverse=True)) + ")"
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
    {"id": "g_007", "patterns": ["أسترا", "استرا", "أسترال", "استرال"], "replacement": "?", "kind": "alias", "scope": "polished_only",
     "status": "pending", "context_rules": [], "note": "المالك: «تبع ChatGPT». المقصود غير مؤكد"},
    {"id": "g_008", "patterns": ["موديل"], "replacement": "Model", "kind": "spelling",
     "scope": "both", "status": "confirmed",
     "context_rules": [{"if_near_any": ["عربية", "عربيه", "سيارة", "سياره", "موتوسيكل", "فستان", "ماركة"], "then": "skip"}]},
    {"id": "g_009", "patterns": ["موديلين", "موديلات", "موديلز"], "replacement": "Models", "kind": "spelling",
     "scope": "both", "status": "confirmed", "context_rules": []},
    {"id": "g_010", "patterns": ["أوتبوت", "اوتبوت", "أوت بوت", "اوت بوت"], "replacement": "Output", "kind": "spelling",
     "scope": "both", "status": "confirmed", "context_rules": []},
    {"id": "g_011", "patterns": ["اي بي كي", "ايه بي كي", "إي بي كي", "اي بي كيه"], "replacement": "API Key", "kind": "spelling",
     "scope": "both", "status": "confirmed",
     "context_rules": [{"if_near_any": ["تطبيق", "أندرويد", "تثبيت", "apk"], "then": "skip"}]},
    {"id": "g_012", "patterns": ["توكن", "توكين"], "replacement": "Token", "kind": "spelling",
     "scope": "both", "status": "confirmed", "context_rules": []},
    {"id": "g_013", "patterns": ["توكنات", "توكينات", "توكنز", "توكينز"], "replacement": "Tokens", "kind": "spelling",
     "scope": "both", "status": "confirmed", "context_rules": []},
    {"id": "g_014", "patterns": ["سكريبت", "اسكربت", "سكربت", "اسكريبت"], "replacement": "Script", "kind": "spelling",
     "scope": "both", "status": "confirmed", "context_rules": []},
    {"id": "g_015", "patterns": ["بروكسي", "بروكسى"], "replacement": "Proxy", "kind": "spelling",
     "scope": "both", "status": "confirmed", "context_rules": []},
    {"id": "g_016", "patterns": ["تيمب", "تمب"], "replacement": "Temp", "kind": "spelling",
     "scope": "both", "status": "confirmed", "context_rules": []},
    {"id": "g_017", "patterns": ["جيسون", "جايسون", "چيسون"], "replacement": "JSON", "kind": "spelling",
     "scope": "both", "status": "confirmed", "context_rules": []},
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
    now = _now()
    entries = []
    for e in DEFAULT_ENTRIES:
        ne = copy.deepcopy(e)
        ne["hits"] = 0
        ne["created"] = now
        entries.append(ne)
    return {"schema_version": SCHEMA_VERSION, "entries": entries, "tombstones": []}


def _matched_default_id(entry):
    """يرجع معرّف المدخل الافتراضي الأصلي (مثل g_008) إن كان entry يمثّله، أو None للمدخلات المخصصة."""
    if not isinstance(entry, dict):
        return None
    rep_low = str(entry.get("replacement") or "").strip().lower()
    kind = entry.get("kind")
    e_pats = {normalize(p) for p in (entry.get("patterns") or []) if p}
    for def_e in DEFAULT_ENTRIES:
        def_rep = str(def_e.get("replacement") or "").strip().lower()
        if rep_low and rep_low != "?" and rep_low == def_rep and kind == def_e.get("kind"):
            return def_e["id"]
        def_pats = {normalize(p) for p in (def_e.get("patterns") or []) if p}
        if e_pats and (e_pats & def_pats) and (def_rep == "?" or rep_low == def_rep):
            return def_e["id"]
    return None


def _present_default_ids_in(doc):
    return {d_id for e in (doc.get("entries") or []) if (d_id := _matched_default_id(e))}


def _prune_resurrected_tombstones(doc):
    """يزيل تلقائياً أي tombstone لمدخل افتراضي عاد موجوداً في entries (سواء بـ Ctrl+Z يدوياً أو بـ add)."""
    raw_tombs = doc.get("tombstones")
    if not isinstance(raw_tombs, list) or not raw_tombs:
        return False
    present_ids = {d_id.lower() for d_id in _present_default_ids_in(doc)}
    present_reps = {
        str(e.get("replacement") or "").strip().lower()
        for e in (doc.get("entries") or [])
        if str(e.get("replacement") or "").strip() not in ("", "?")
    }
    cleaned = [t for t in raw_tombs if str(t).lower() not in present_ids and str(t).lower() not in present_reps]
    if len(cleaned) != len(raw_tombs):
        doc["tombstones"] = cleaned
        return True
    return False


def _sync_defaults_into_doc(doc):
    """مزامنة المداخل الافتراضية المؤكدة مع تنظيف tombstones العائدة واحترام tombstones النشطة (بالـ id أو الاسم القديم)."""
    updated = _prune_resurrected_tombstones(doc)
    tombstones = {str(t).lower() for t in (doc.get("tombstones") or [])}
    existing_patterns = {normalize(p) for e in doc.get("entries", []) for p in e.get("patterns", [])}
    existing_ids = {e.get("id") for e in doc.get("entries", [])}
    nums = [int(e["id"][2:]) for e in doc.get("entries", []) if re.fullmatch(r"g_\d+", str(e.get("id", "")))]
    next_num = (max(nums) + 1) if nums else 1
    for def_e in DEFAULT_ENTRIES:
        if def_e.get("status") != "confirmed":
            continue
        rep_low = str(def_e.get("replacement", "")).lower()
        if str(def_e.get("id", "")).lower() in tombstones or rep_low in tombstones:
            continue
        if not any(normalize(p) in existing_patterns for p in def_e.get("patterns", [])):
            new_e = copy.deepcopy(def_e)
            new_e["hits"] = 0
            new_e["created"] = _now()
            if new_e.get("id") in existing_ids:
                new_e["id"] = f"g_{next_num:03d}"
                next_num += 1
            existing_ids.add(new_e["id"])
            doc["entries"].append(new_e)
            for p in def_e.get("patterns", []):
                existing_patterns.add(normalize(p))
            updated = True
        elif def_e.get("context_rules"):
            for cur_e in doc.get("entries", []):
                if str(cur_e.get("replacement", "")).lower() == rep_low and not cur_e.get("context_rules"):
                    cur_e["context_rules"] = copy.deepcopy(def_e["context_rules"])
                    updated = True
    return updated


class Glossary:
    def __init__(self, path=None, on_corrupt=None):
        self._lock = threading.RLock()
        self.path = path or Config.GLOSSARY_FILE
        self._on_corrupt = on_corrupt
        self.corrupt_notice = None
        self.doc = {"schema_version": SCHEMA_VERSION, "entries": []}
        self._compiled = []     # [(entry, regex)] الأطول أولاً
        self._present_default_ids = set()
        self._watcher = FileStampWatcher()
        self.reload()

    # ------------------------------------------------------------------
    # التحميل / الحفظ / التحديث الحي الكسول
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
            else:
                if _sync_defaults_into_doc(doc):
                    self._write(doc)
                else:
                    self._watcher.mark_loaded(self.path)
            self.doc = doc
            self._present_default_ids = _present_default_ids_in(doc)
            self._compile()

    def maybe_reload(self, force=False):
        """
        فحص كسول لـ `glossary.json` داخل `self._lock` دائماً:
        - لو الملف تالف أثناء التحديث الحي: يُبقي النسخة الصالحة في الذاكرة دون المساس بملف المالك على القرص.
        - لو اختفى مدخل افتراضي بين تحميلين: يسجّل معرّفه الافتراضي في `tombstones` تلقائياً.
        - لو عاد مدخل محذوف للملف (Ctrl+Z): يُزال معرّفه من `tombstones` تلقائياً عبر `_sync_defaults_into_doc`.
        """
        with self._lock:
            state, sig = self._watcher.check(self.path, force=force)
            if state in ("throttled", "unchanged"):
                return False
            if state == "missing":
                doc = _default_doc()
                self.doc = doc
                self._present_default_ids = _present_default_ids_in(doc)
                self._compile()
                self._write(doc)
                return True
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
                    raise ValueError("بنية glossary.json تفتقد قائمة entries")
            except Exception as e:
                if self._watcher.mark_bad(sig):
                    logger.warning("⚠️ تعذر التحديث الحي لـ glossary.json (%s: %s) — الإبقاء على النسخة الصالحة في الذاكرة",
                                   type(e).__name__, e)
                return False

            now_def_ids = _present_default_ids_in(doc)
            manually_deleted = sorted(self._present_default_ids - now_def_ids)
            updated = False
            if manually_deleted:
                tombs = doc.setdefault("tombstones", [])
                existing_tombs = {str(t).lower() for t in tombs}
                for d_id in manually_deleted:
                    if d_id.lower() not in existing_tombs:
                        tombs.append(d_id)
                        existing_tombs.add(d_id.lower())
                        updated = True

            if _sync_defaults_into_doc(doc):
                updated = True

            self.doc = doc
            self._present_default_ids = _present_default_ids_in(doc)
            self._compile()
            if updated:
                self._write(doc)
            else:
                self._watcher.mark_loaded(self.path, sig=sig)
            return True

    def _write(self, doc=None):
        try:
            store.atomic_write_json(self.path, doc if doc is not None else self.doc)
            self._watcher.mark_loaded(self.path)
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
            rx = re.compile(_WB_L + "(?:(" + _PREFIX_RE + r")(\s?))?(" + "|".join(re.escape(a) for a in alts) + ")" + _WB_R,
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
            self.maybe_reload()
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
        replacement = entry.get("replacement") or ""
        rep_norm = normalize(replacement)
        rep_parts = replacement.split()
        prev_word_norm = normalize(rep_parts[0]) if len(rep_parts) > 1 else None

        out, last, n = [], 0, 0
        for m in rx.finditer(norm):
            if rules and self._skip_by_context(norm, m.start(), m.end(), rules):
                continue
            # صمام 1: اللفظ متبوع أصلاً بالمصطلح بين قوسين (قاعدة الأقواس): شلبي (ChatGPT) أو سانتا (Claude Sonnet)
            after = text[m.end():].lstrip()
            if after.startswith("(") and ")" in after:
                bracket_content = normalize(after[1:after.find(")")]).strip()
                if bracket_content and (rep_norm in bracket_content or bracket_content in rep_norm):
                    continue
            # صمام 2: اللفظ مسبوق بالبادئة الإنجليزية من المصطلح المركب: Gemini Carbon أو Claude Sonnet
            if prev_word_norm:
                before = text[:m.start()].rstrip()
                if normalize(before).endswith(prev_word_norm):
                    continue
            prefix = text[m.start(1):m.end(1)] if m.group(1) is not None else ""
            gap = text[m.start(2):m.end(2)] if m.group(2) is not None else ""
            if prefix and re.match(r"[A-Za-z]", replacement):
                separator = " " if prefix in ("و", "ف") else "ـ "
            else:
                separator = gap
            out.append(text[last:m.start()])
            out.append(prefix + separator + replacement)
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

    def hints_for_prompt(self, limit=20):
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
            rep_low = replacement.lower()
            for e in self.doc["entries"]:
                if e.get("replacement", "").lower() == rep_low and e.get("kind") == kind:
                    if normalize(pattern) not in {normalize(p) for p in e.get("patterns", [])}:
                        e.setdefault("patterns", []).append(pattern)
                    e["status"] = status
                    _prune_resurrected_tombstones(self.doc)
                    self._present_default_ids = _present_default_ids_in(self.doc)
                    self._compile()
                    self._write()
                    _audit("glossary.add_pattern", id=e["id"], pattern=pattern, replacement=replacement)
                    return e, False
            entry = {"id": self._next_id(), "patterns": [pattern], "replacement": replacement, "kind": kind,
                     "scope": scope, "status": status, "context_rules": copy.deepcopy(list(context_rules or [])), "hits": 0,
                     "created": _now()}
            self.doc["entries"].append(entry)
            _prune_resurrected_tombstones(self.doc)
            self._present_default_ids = _present_default_ids_in(self.doc)
            self._compile()
            self._write()
            _audit("glossary.add", id=entry["id"], pattern=pattern, replacement=replacement, kind=kind, status=status)
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
            def_id = _matched_default_id(e)
            if def_id:
                tombs = self.doc.setdefault("tombstones", [])
                if def_id.lower() not in {str(t).lower() for t in tombs}:
                    tombs.append(def_id)
            self._present_default_ids = _present_default_ids_in(self.doc)
            self._compile()
            self._write()
            _audit("glossary.remove", id=e.get("id"), replacement=e.get("replacement"))
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
            _audit("glossary.confirm", id=e.get("id"), replacement=e.get("replacement"))
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
