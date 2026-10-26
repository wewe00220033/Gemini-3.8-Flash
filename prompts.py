# -*- coding: utf-8 -*-
"""
مُركِّب البرومبت والأدوار (مواصفة 06 — المرحلة 3b).

ثلاث طبقات:
  A. التفريغ (transcribe): قصيرة وثابتة قدر الإمكان (≤ TRANSCRIBE_LAYER_A_MAX_CHARS للإضافات) — كل الذكاء في B.
  B. التنظيم (polish): نص خام → نص منظم عبر المساعد النصي (flash-lite) — لا تفريغ ثانٍ للصوت.
  C. الدور (role): يُحقن في A **بسطر واحد** وفي B **بالكامل**.

- 7 أدوار افتراضية **محمية** من الحذف (general, devops, educational, smart_verbatim, quran_tutor, kids_tutor, medical).
- أدوار مخصصة في roles.json (ملف تالف → الافتراضية تعمل). الدور المخصص لا يغيّر طبقة A بأكثر من سطرين.
- `compose_prompt(layer, role, hints)` دالة نقية (لا شبكة، لا حالة) — مختبَرة.
"""

import re
import threading

from config import Config
import store

ROLES_SCHEMA = 1
LAYER_A_ROLE_MAX_LINES = 2       # 06 §5: الدور المولَّد لا يغيّر طبقة A بأكثر من سطرين

# ----------------------------------------------------------------------
# الأدوار الافتراضية (06 §2) — محمية
# ----------------------------------------------------------------------
_POLISH_COMMON = (
    "لا تضف معلومات غير موجودة في النص الخام، ولا تلخّص إلى درجة حذف تفاصيل. "
    "حافظ على المعنى والمصطلحات كما وردت، ولا تترجم المصطلحات الإنجليزية. "
    "أخرج النص المنظم فقط بلا مقدمات أو تعليقات."
)

DEFAULT_ROLES = {
    "general": {
        "name": "عام", "protected": True,
        "layer_a": "",
        "layer_b": "نظّم النص الخام: ترقيم سليم، فقرات منطقية، إزالة الحشو اللفظي (يعني/آآ/إمم) مع الحفاظ على المعنى، "
                   "وعنوان مقترح من سطر واحد في البداية.",
    },
    "devops": {
        "name": "مهندس برمجيات / DevOps", "protected": True,
        "layer_a": "المجال: برمجة/DevOps؛ المصطلحات التقنية والأدوات تُكتب بالإنجليزية (Docker, Nginx, Antigravity).",
        "layer_b": "نظّم النص في أقسام فقط لما ورد فعلاً منها: المشكلة / الخطوات / الأوامر / القرار. "
                   "ضع الأوامر وأسماء الملفات داخل كتل كود. المصطلحات التقنية بالإنجليزية.",
    },
    "educational": {
        "name": "معلم / شارح", "protected": True,
        "layer_a": "",
        "layer_b": "نظّم النص كنقاط تعليمية مرتبة، مع تعريفات موجزة للمفاهيم المذكورة وأمثلة إن وردت.",
    },
    "smart_verbatim": {
        "name": "حرفي ذكي", "protected": True,
        "layer_a": "لا تحذف شيئاً مما قيل.",
        "layer_b": "أضف الترقيم وتقسيم الفقرات فقط. لا إعادة صياغة، لا حذف، لا إضافة.",
    },
    "quran_tutor": {
        "name": "شيخ / قارئ", "protected": True,
        "layer_a": "قد يحوي التسجيل آيات قرآنية: اكتبها بالرسم العثماني إن أمكن مع ضبط اسم السورة ورقم الآية.",
        "layer_b": "ميّز الآيات بين ⟪ ⟫ عن الشرح، واذكر السورة ورقم الآية بعد كل آية. "
                   "لا تصحّح التلاوة ولا تعلّق عليها (مسؤولية بشرية).",
    },
    "kids_tutor": {
        "name": "معلم أطفال", "protected": True,
        "layer_a": "المتحدث قد يكون طفلاً: كن متسامحاً مع النطق واكتب الكلمة المقصودة.",
        "layer_b": "لغة مبسطة، نقاط قصيرة، إيموجي محدود (≤ 3).",
    },
    "medical": {
        "name": "طبيب", "protected": True,
        "layer_a": "المجال: طبي؛ المصطلحات الطبية وأسماء الأدوية تُكتب بالإنجليزية.",
        "layer_b": "نظّم النص طبياً (الشكوى / الملاحظات / الخطة إن وردت). المصطلحات الطبية بالإنجليزية. "
                   "أضف في النهاية سطراً: «⚠️ هذا تفريغ آلي وليس استشارة طبية».",
    },
}

# ----------------------------------------------------------------------
# طبقة A الأساسية (التفريغ) — Config.SYSTEM_PROMPT تبقى المصدر (ثابتة ومجرَّبة)
# ----------------------------------------------------------------------
_LAYER_B_BASE = (
    "أنت محرر نصوص عربي محترف. ستتلقى نصاً خاماً ناتجاً عن تفريغ صوتي آلي لكلام بالعامية المصرية. "
    "مهمتك تنظيمه ليكون مقروءاً."
)


def compose_prompt(layer, role_id="general", hints="", roles=None):
    """
    يركّب برومبت الطبقة المطلوبة (دالة نقية).
      layer="transcribe": Config.SYSTEM_PROMPT + سطر الدور (إن وُجد) + تلميح القاموس (إن وُجد).
      layer="polish":     القاعدة العامة + تعليمات الدور كاملة + القاعدة الذهبية (لا إضافة/لا حذف).
    """
    roles = roles if roles is not None else all_roles()
    role = roles.get(role_id) or roles.get("general") or DEFAULT_ROLES["general"]
    if layer == "transcribe":
        extra = []
        a = _clip_lines((role.get("layer_a") or "").strip(), LAYER_A_ROLE_MAX_LINES)
        if a:
            extra.append(a)
        if hints:
            extra.append(hints.strip())
        extra_txt = "\n".join(extra)
        if len(extra_txt) > Config.TRANSCRIBE_LAYER_A_MAX_CHARS:
            extra_txt = extra_txt[:Config.TRANSCRIBE_LAYER_A_MAX_CHARS].rsplit(" ", 1)[0]
        return Config.SYSTEM_PROMPT + ("\n" + extra_txt if extra_txt else "")
    if layer == "polish":
        b = (role.get("layer_b") or DEFAULT_ROLES["general"]["layer_b"]).strip()
        return f"{_LAYER_B_BASE}\n\nالدور: {role.get('name', role_id)}.\n{b}\n\n{_POLISH_COMMON}"
    raise ValueError(f"طبقة غير معروفة: {layer}")


def layer_a_extra_len(role_id="general", hints="", roles=None):
    """طول الإضافات فوق SYSTEM_PROMPT في طبقة A (للاختبار الحارس 06 §8)."""
    base = len(Config.SYSTEM_PROMPT)
    return max(0, len(compose_prompt("transcribe", role_id, hints, roles)) - base)


def _clip_lines(text, n):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[:n])


# ----------------------------------------------------------------------
# roles.json — الأدوار المخصصة
# ----------------------------------------------------------------------
_lock = threading.RLock()


def _audit(action, **fields):
    """09 §2 قناة audit: لا تكسر العملية أبداً لو فشل التسجيل."""
    try:
        import audit
        audit.record(action, **fields)
    except Exception:
        pass
_custom_cache = None
_custom_path = None


def _load_custom():
    global _custom_cache, _custom_path
    if _custom_cache is not None and _custom_path == Config.ROLES_FILE:
        return _custom_cache
    data = store.load_json_tolerant(Config.ROLES_FILE, default={})
    roles = data.get("roles") if isinstance(data, dict) else None
    out = {}
    if isinstance(roles, dict):
        for rid, r in roles.items():
            if isinstance(r, dict) and re.fullmatch(r"[a-z0-9_]{2,32}", str(rid)) and rid not in DEFAULT_ROLES:
                out[rid] = {"name": str(r.get("name") or rid), "protected": False,
                            "layer_a": _clip_lines(str(r.get("layer_a") or ""), LAYER_A_ROLE_MAX_LINES),
                            "layer_b": str(r.get("layer_b") or "")}
    _custom_cache, _custom_path = out, Config.ROLES_FILE
    return out


def _save_custom():
    try:
        store.atomic_write_json(Config.ROLES_FILE, {"schema_version": ROLES_SCHEMA, "roles": _custom_cache})
    except Exception as e:  # pragma: no cover
        print(f"⚠️ roles.json: تعذّر الحفظ ({e})")


def reset_cache():
    global _custom_cache, _custom_path
    with _lock:
        _custom_cache, _custom_path = None, None


def all_roles():
    """الافتراضية + المخصصة (المخصصة لا تستطيع تجاوز معرّف محمي)."""
    with _lock:
        merged = {k: dict(v) for k, v in DEFAULT_ROLES.items()}
        merged.update({k: dict(v) for k, v in _load_custom().items()})
        return merged


def get_role(role_id):
    return all_roles().get(role_id)


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return s[:32] if len(s) >= 2 else ""


def add_role(role_id, name, layer_a="", layer_b=""):
    """يضيف/يحدّث دوراً مخصصاً. يرفض المعرّفات المحمية. يرجع (role, created)."""
    role_id = (role_id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{2,32}", role_id):
        raise ValueError("معرّف الدور: حروف إنجليزية صغيرة/أرقام/_ (2–32)")
    if role_id in DEFAULT_ROLES:
        raise ValueError("هذا معرّف دور افتراضي محمي — انسخه باسم آخر")
    if not (layer_b or "").strip():
        raise ValueError("تعليمات التنظيم (layer_b) مطلوبة")
    with _lock:
        custom = _load_custom()
        created = role_id not in custom
        custom[role_id] = {"name": (name or role_id).strip()[:40], "protected": False,
                           "layer_a": _clip_lines(layer_a or "", LAYER_A_ROLE_MAX_LINES), "layer_b": layer_b.strip()}
        _save_custom()
    _audit("role.add" if created else "role.update", id=role_id, name=custom[role_id]["name"])
    return dict(custom[role_id]), created


def remove_role(role_id):
    """يحذف دوراً مخصصاً. الافتراضية محمية → None."""
    with _lock:
        if role_id in DEFAULT_ROLES:
            return None
        custom = _load_custom()
        r = custom.pop(role_id, None)
        if r is not None:
            _save_custom()
    if r is not None:
        _audit("role.remove", id=role_id, name=r.get("name"))
    return r


def roles_list_text(current):
    lines = ["🎭 الأدوار (الحالي بعلامة ✅):"]
    for rid, r in all_roles().items():
        mark = "✅" if rid == current else "•"
        prot = "" if r.get("protected") else " (مخصص)"
        lines.append(f"{mark} {rid} — {r['name']}{prot}")
    lines.append("\nتفعيل: /role <id>   ·   جديد: /new_role <id> | <الاسم> | <تعليمات التنظيم>")
    return "\n".join(lines)
