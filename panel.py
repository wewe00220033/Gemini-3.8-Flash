# -*- coding: utf-8 -*-
"""
panel.py — لوحة التحكم التفاعلية (11 §1–2، §6): بناء الشاشات (نص + inline_keyboard) + القائمة البيضاء لـ /set (11 §4).

مبدأ ثابت: **Buttons = Commands** — كل زر يستدعي نفس منطق الأمر النصي (COMMAND_PARITY يوثّق الربط ويُختبر).
هذه الوحدة **لا تتصل بتليجرام**؛ telegram_bot يرسل/يعدّل الرسالة ويرد answerCallbackQuery.

ترميز callback_data (≤ 64 بايت، ASCII فقط): `<قسم>:<فعل>[:<وسيط>]`
    p:home | p:st | p:dr                الرئيسية / الحالة / الطبيب
    k:l:<page> k:v:<i> k:d:<i> k:D:<i> k:n:<i> k:a     المفاتيح: قائمة/عرض/حذف→تأكيد/متأكد/تسمية(FSM)/إضافة(FSM)
    m:<mode> | h:<target>               وضع الإرسال / البانر
    r:l r:s:<id> r:n r:d:<id> r:D:<id>  الأدوار: قائمة/تفعيل/جديد(FSM)/حذف→تأكيد/متأكد
    s:l:<page> s:v:<id8> s:r:<id8> s:e s:x:<id8> s:n s:N   الجلسات
    g:l:<page> g:v:<id> g:c:<id> g:d:<id> g:D:<id> g:a      القاموس
    c:auto | c:simple                   السياق
    L:t | L:e | L:r | L:a               السجلات: اليوم / الأخطاء / تتبع آخر فويس / التدقيق
    b:n | b:s | b:S | b:t               النسخ: الآن / الأسرار→تأكيد / متأكد / اختبار استعادة
    S:l | S:v:<key> | S:s:<key>=<val> | S:e:<key>   الإعدادات: قائمة/عرض/اختيار/إدخال رقم(FSM)
"""
import time

from config import Config

# القائمة البيضاء للإعدادات (11 §4): المفتاح → (النوع, المدى/الخيارات, الوصف)
SET_WHITELIST = {
    "delivery_mode":            ("choice", ("dual", "raw_only", "polished_only"), "وضع الإرسال"),
    "role":                     ("role", None, "الدور العام"),
    "context_mode":             ("choice", ("auto", "simple"), "السياق داخل الجلسة"),
    "transcript_header_target": ("choice", ("both", "raw_only", "polished_only", "off"), "بانر «تفريغ آلي»"),
    "log_retention_days":       ("int", (1, 365), "أيام الاحتفاظ بالسجلات"),
    "panel_page_size":          ("int", (3, 10), "عناصر الصفحة في اللوحة"),
    "retry_budget_seconds":     ("int", (10, 300), "ميزانية إعادة المحاولة (ث)"),
    "model_503_key_hops":       ("int", (0, 10), "قفزات المفاتيح عند 503"),
    "backup_weekly_day":        ("choice", ("mon", "tue", "wed", "thu", "fri", "sat", "sun"), "يوم النسخة الأسبوعية"),
    "notify_on_start":          ("bool", None, "إشعار عند الإقلاع"),
    "progress_after_seconds":   ("int", (0, 120), "رسالة «جارٍ» بعد (ث)"),
}
# ممنوعة نهائياً (11 §4)
SET_FORBIDDEN = ("keys", "token", "telegram_bot_token", "web_access_token", "data_dir", "models", "transcribe_workers",
                 "bot_workers", "api_key", "gemini_api_key")
# مفاتيح /set التي تُطبَّق كتجاوز فوري على Config (الطبقتان: Config افتراضي، settings.json تجاوز)
SET_CONFIG_ATTR = {
    "log_retention_days": "LOG_RETENTION_DAYS", "panel_page_size": "PANEL_PAGE_SIZE",
    "retry_budget_seconds": "RETRY_BUDGET_SECONDS", "model_503_key_hops": "MODEL_503_KEY_HOPS",
    "backup_weekly_day": "BACKUP_WEEKLY_DAY", "notify_on_start": "NOTIFY_ON_START",
    "progress_after_seconds": "PROGRESS_AFTER_SECONDS",
}


# ======================================================================
# أدوات عامة
# ======================================================================
def btn(text, data):
    """زر inline مع فرض الحدود (11 §1 و §6): data ≤ 64 بايت ASCII، النص ≤ 20 حرفاً."""
    if not isinstance(data, str) or not data or len(data.encode("utf-8")) > Config.CALLBACK_DATA_MAX or not data.isascii():
        raise ValueError(f"callback_data غير صالح: {data!r}")
    t = str(text)
    if len(t) > Config.PANEL_BUTTON_MAX_CHARS:
        t = t[:Config.PANEL_BUTTON_MAX_CHARS - 1] + "…"
    return {"text": t, "callback_data": data}


def kb(rows):
    return {"inline_keyboard": [[b for b in row if b] for row in rows if row]}


def page_size():
    return max(1, int(getattr(Config, "PANEL_PAGE_SIZE", 5) or 5))


def paginate(items, page):
    """(slice, page, pages) — الصفحة تُقصّ إلى المدى الصالح."""
    size = page_size()
    pages = max(1, (len(items) + size - 1) // size)
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        page = 1
    page = min(max(1, page), pages)
    return items[(page - 1) * size: page * size], page, pages


def nav_row(prefix, page, pages, home=True):
    row = []
    if page > 1:
        row.append(btn("⬅️", f"{prefix}:{page - 1}"))
    if pages > 1:
        row.append(btn(f"{page}/{pages}", f"{prefix}:{page}"))
    if page < pages:
        row.append(btn("➡️", f"{prefix}:{page + 1}"))
    if home:
        row.append(btn("🏠", "p:home"))
    return row


def parse(data):
    """'k:d:3' → ('k', 'd', '3'). 'x' → ('x', '', '')."""
    parts = (data or "").split(":", 2)
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], parts[2]


def _mask(k):
    key = k.get("key", "")
    return key[:8] + "…" + key[-4:] if len(key) > 12 else "…"


def _mark(v, cur):
    return "◉ " if v == cur else "○ "


# ======================================================================
# الشاشات — كل دالة ترجع (text, reply_markup)
# ======================================================================
def home():
    import settings
    import sessions
    cur = sessions.current()
    text = ("🎛 لوحة التحكم\n"
            f"• وضع الإرسال: {settings.delivery_mode()} · الدور: {settings.effective_role()}\n"
            f"• الجلسة: {cur['name'] if cur else 'لا جلسة نشطة'}\n"
            "اختَر قسماً (كل زر له أمر نصي مكافئ — /help):")
    rows = [
        [btn("📊 الحالة", "p:st"), btn("🔑 المفاتيح", "k:l:1")],
        [btn("🎛 وضع الإرسال", "m:menu"), btn("🎭 الدور", "r:l")],
        [btn("🗂 الجلسات", "s:l:1"), btn("📖 القاموس", "g:l:1")],
        [btn("🧠 السياق", "c:menu"), btn("🧾 السجلات", "L:menu")],
        [btn("💾 النسخ", "b:menu"), btn("🩺 الطبيب", "p:dr")],
        [btn("⚙️ إعدادات", "S:l")],
    ]
    return text, kb(rows)


def keys_list(page=1):
    from key_manager import key_manager as km
    keys = km.data.get("keys", [])
    now = time.time()
    order = sorted(range(len(keys)), key=lambda i: (0 if any(km.is_model_available(keys[i], m, now) for m in Config.MODELS) else 1,
                                                    keys[i].get("name", "")))
    sl, page, pages = paginate(order, page)
    lines = [f"🔑 المفاتيح ({len(keys)}) — صفحة {page}/{pages}:" if keys else "🔑 لا مفاتيح بعد. اضغط ➕ أو الصق المفتاح هنا."]
    rows = []
    for i in sl:
        k = keys[i]
        avail = any(km.is_model_available(k, m, now) for m in Config.MODELS)
        rem = sum(km.remaining_today(k).values())
        lines.append(f"{'🟢' if avail else '⏳'} {k.get('name')} [{_mask(k)}] متبقي {rem}")
        rows.append([btn(f"{'🟢' if avail else '⏳'} {k.get('name')}", f"k:v:{i}")])
    rows.append([btn("➕ إضافة مفتاح", "k:a")])
    rows.append(nav_row("k:l", page, pages))
    return "\n".join(lines) + "\n(/keys)", kb(rows)


def key_view(idx, confirm_delete=False):
    from key_manager import key_manager as km
    keys = km.data.get("keys", [])
    try:
        k = keys[int(idx)]
    except (IndexError, ValueError):
        return "المفتاح لم يعد موجوداً.", kb([[btn("⬅️ المفاتيح", "k:l:1")]])
    rem = km.remaining_today(k)
    text = (f"🔑 {k.get('name')}  [{_mask(k)}]\n"
            f"• الحالة: {k.get('status')} · المجموعة: {k.get('project_group') or '-'}\n"
            f"• نجاحات: {k.get('success_count', 0)} · متبقي اليوم: {sum(rem.values())}\n"
            f"• أُضيف: {k.get('added_at', '?')}")
    if confirm_delete:
        text += "\n\n⚠️ حذف هذا المفتاح نهائياً؟"
        rows = [[btn("✅ متأكد من الحذف", f"k:D:{idx}"), btn("❌ إلغاء", f"k:v:{idx}")]]
    else:
        rows = [[btn("✏️ تسمية", f"k:n:{idx}"), btn("🗑 حذف", f"k:d:{idx}")],
                [btn("⬅️ المفاتيح", "k:l:1"), btn("🏠", "p:home")]]
    return text, kb(rows)


def mode_menu():
    import settings
    cur, hdr = settings.delivery_mode(), settings.header_target()
    text = (f"🎛 وضع الإرسال الحالي: {cur}\n"
            "• مزدوج: خام + منظم · خام: الخام فقط · منظم: المنظم فقط\n"
            f"البانر «تفريغ آلي»: {hdr}  (/mode · /header)")
    rows = [
        [btn(_mark("dual", cur) + "مزدوج", "m:dual"), btn(_mark("raw_only", cur) + "خام", "m:raw_only"), btn(_mark("polished_only", cur) + "منظم", "m:polished_only")],
        [btn(_mark("both", hdr) + "بانر: الكل", "h:both"), btn(_mark("raw_only", hdr) + "خام", "h:raw_only"),
         btn(_mark("polished_only", hdr) + "منظم", "h:polished_only"), btn(_mark("off", hdr) + "إيقاف", "h:off")],
        [btn("🏠", "p:home")],
    ]
    return text, kb(rows)


def roles_list(confirm_delete=None):
    import prompts
    import settings
    cur = settings.role()
    roles = prompts.all_roles()
    ids = [rid for rid in roles if isinstance(rid, str) and rid.isascii()]
    lines = [f"🎭 الدور الحالي: {cur} — {(prompts.get_role(cur) or {}).get('name', '?')}", "اضغط دوراً لتفعيله (يسري على الفويس القادم):"]
    rows = [[btn(("✓ " if rid == cur else "") + roles[rid].get("name", rid), f"r:s:{rid}")] for rid in ids]
    custom = [rid for rid in ids if not roles[rid].get("protected", True)]
    if confirm_delete and confirm_delete in custom:
        lines.append(f"\n⚠️ حذف الدور المخصص [{confirm_delete}]؟")
        rows.append([btn("✅ متأكد من الحذف", f"r:D:{confirm_delete}"), btn("❌ إلغاء", "r:l")])
    else:
        row = [btn("➕ دور جديد", "r:n")] + [btn(f"🗑 {rid}", f"r:d:{rid}") for rid in custom[:3]]
        rows.append(row)
    rows.append([btn("🏠", "p:home")])
    return "\n".join(lines) + "\n(/roles · /role <id> · /new_role · /role_del)", kb(rows)


def sessions_list(page=1):
    import sessions
    cur = sessions.current()
    items = sessions.list_sessions()
    items.sort(key=lambda s: 0 if s.get("status") == "active" else 1)   # النشطة أولاً (ترتيب مستقر يُبقي الأحدث أولاً)
    sl, page, pages = paginate(items, page)
    lines = [f"🗂 الجلسات ({len(items)}) — صفحة {page}/{pages}:" if items else "🗂 لا جلسات بعد."]
    rows = []
    for s in sl:
        mark = "▶️" if s.get("status") == "active" else "⏹"
        lines.append(f"{mark} {s.get('name')} — {s.get('count', 0)} فويس · {s.get('id')}")
        rows.append([btn(f"{mark} {s.get('name')} ({s.get('count', 0)})", f"s:v:{s.get('id')}")])
    act = [btn("➕ جديدة", "s:n")]
    if cur:
        act += [btn("⏹ إنهاء", "s:e"), btn("✏️ تسمية", "s:N"), btn("📥 تصدير", f"s:x:{cur['id']}")]
    rows.append(act)
    rows.append(nav_row("s:l", page, pages))
    return "\n".join(lines) + "\n(/sessions · /new_session · /resume · /end · /export)", kb(rows)


def session_view(sid):
    import sessions
    s = sessions.get(sid)
    if not s:
        return "الجلسة غير موجودة.", kb([[btn("⬅️ الجلسات", "s:l:1")]])
    text = (f"🗂 {s.get('name')}  ({s.get('id')})\n"
            f"• الحالة: {s.get('status')} · الفويسات: {s.get('count', 0)}\n"
            f"• بدأت: {s.get('started')} · آخر نشاط: {s.get('last_ts')}\n"
            f"• دور الجلسة: {s.get('role_override') or 'الافتراضي'}")
    row = [btn("⏹ إنهاء", "s:e") if s.get("status") == "active" else btn("▶️ استئناف", f"s:r:{s['id']}"), btn("📥 تصدير", f"s:x:{s['id']}")]
    return text, kb([row, [btn("⬅️ الجلسات", "s:l:1"), btn("🏠", "p:home")]])


def glossary_list(page=1):
    import glossary
    ents = glossary.get_glossary().entries()
    sl, page, pages = paginate(ents, page)
    lines = [f"📖 القاموس ({len(ents)}) — صفحة {page}/{pages}:" if ents else "📖 القاموس فارغ."]
    rows = []
    for e in sl:
        pend = "⏳ " if e.get("status") == "pending" else ""
        lines.append(f"{pend}[{e['id']}] {'/'.join(e.get('patterns', []))} → {e.get('replacement')} ({e.get('kind')})")
        rows.append([btn(f"{pend}{(e.get('patterns') or ['?'])[0]} → {e.get('replacement')}", f"g:v:{e['id']}")])
    rows.append([btn("➕ إضافة كلمة", "g:a")])
    rows.append(nav_row("g:l", page, pages))
    return "\n".join(lines) + "\n(/glossary · /add_word · /del_word · /confirm_word)", kb(rows)


def glossary_view(gid, confirm_delete=False):
    import glossary
    e = glossary.get_glossary().find(gid)
    if not e:
        return "المدخل غير موجود.", kb([[btn("⬅️ القاموس", "g:l:1")]])
    text = (f"📖 [{e['id']}] {'/'.join(e.get('patterns', []))} → {e.get('replacement')}\n"
            f"• النوع: {e.get('kind')} · النطاق: {e.get('scope')} · الحالة: {e.get('status')} · إصابات: {e.get('hits', 0)}")
    if confirm_delete:
        text += "\n\n⚠️ حذف هذا المدخل؟"
        rows = [[btn("✅ متأكد من الحذف", f"g:D:{e['id']}"), btn("❌ إلغاء", f"g:v:{e['id']}")]]
    else:
        row = [btn("🗑 حذف", f"g:d:{e['id']}")]
        if e.get("status") == "pending" and e.get("replacement") not in ("", "?"):
            row.insert(0, btn("✅ تأكيد", f"g:c:{e['id']}"))
        rows = [row, [btn("⬅️ القاموس", "g:l:1"), btn("🏠", "p:home")]]
    return text, kb(rows)


def context_menu():
    import sessions
    mode = sessions.context_mode()
    cur = sessions.current()
    text = (f"🧠 السياق داخل الجلسة: {mode}\n"
            "• تلقائي: آخر فويسين كسياق ويسقط وحده بعد ضربتين · بسيط: بلا سياق\n"
            + ("" if cur else "⚠️ لا جلسة نشطة — الإعداد يسري على الجلسة النشطة فقط.\n") + "(/context on|off)")
    rows = [[btn(_mark("full", mode) + "تلقائي", "c:auto"), btn(("◉ " if mode in ("simple", "off") else "○ ") + "بسيط دائماً", "c:simple")],
            [btn("🏠", "p:home")]]
    return text, kb(rows)


def logs_menu():
    text = "🧾 السجلات — الأسرار محجوبة دائماً.\n(/logs · /logs errors · /trace · /audit)"
    rows = [[btn("📄 اليوم", "L:t"), btn("⚠️ الأخطاء", "L:e"), btn("🔍 تتبع آخر فويس", "L:r")], [btn("🧾 التدقيق", "L:a"), btn("🏠", "p:home")]]
    return text, kb(rows)


def backup_menu(confirm_secrets=False):
    import backup
    local = backup.list_local()
    st = backup.load_state()
    last = f"آخر نسخة تلقائية منذ {(time.time() - st['last_ts']) / 86400:.1f} يوم" if st.get("last_ts") else "لا نسخة تلقائية بعد"
    text = (f"💾 النسخ الاحتياطي\n• {last} · محلي {len(local)}/{Config.BACKUPS_KEEP}\n"
            f"• الجدولة: {backup.schedule_text()}\n(/backup · /backups · /backup_secrets · /restore_test)")
    if confirm_secrets:
        text += "\n\n🔐 إرسال keys.json إلى هذا الشات؟ (احذف الرسالة بعد الحفظ)"
        rows = [[btn("✅ متأكد", "b:S"), btn("❌ إلغاء", "b:menu")]]
    else:
        rows = [[btn("📦 نسخة الآن", "b:n"), btn("🔐 نسخة الأسرار", "b:s"), btn("🧪 اختبار استعادة", "b:t")],
                [btn("🗃️ النسخ المحفوظة", "b:l"), btn("🏠", "p:home")]]
    return text, kb(rows)


def settings_list():
    lines = ["⚙️ الإعدادات الآمنة (القائمة البيضاء):"]
    rows = []
    for key, (_typ, _rng, desc) in SET_WHITELIST.items():
        val = current_setting(key)
        lines.append(f"• {key} = {val}  — {desc}")
        rows.append([btn(f"{desc}: {val}", f"S:v:{key}")])
    rows.append([btn("🏠", "p:home")])
    lines.append("اضغط إعداداً لتغييره، أو: /set <key> <value>")
    return "\n".join(lines), kb(rows)


def setting_view(key):
    if key not in SET_WHITELIST:
        return "إعداد غير مسموح.", kb([[btn("⬅️ الإعدادات", "S:l")]])
    typ, rng, desc = SET_WHITELIST[key]
    val = current_setting(key)
    text = f"⚙️ {desc}\n• المفتاح: {key}\n• الحالي: {val}\n"
    rows = []
    if typ == "choice":
        text += "اختَر قيمة:"
        row = []
        for opt in rng:
            row.append(btn(_mark(opt, str(val)) + opt, f"S:s:{key}={opt}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    elif typ == "bool":
        rows.append([btn(("◉ " if val else "○ ") + "تشغيل", f"S:s:{key}=1"), btn(("◉ " if not val else "○ ") + "إيقاف", f"S:s:{key}=0")])
    elif typ == "int":
        text += f"المدى: {rng[0]}–{rng[1]}. اضغط «✏️ قيمة جديدة» ثم اكتب الرقم."
        rows.append([btn("✏️ قيمة جديدة", f"S:e:{key}")])
    elif typ == "role":
        text += "اختَر الدور من قسم 🎭 الدور."
        rows.append([btn("🎭 الأدوار", "r:l")])
    rows.append([btn("⬅️ الإعدادات", "S:l"), btn("🏠", "p:home")])
    return text, kb(rows)


# ======================================================================
# /set — القائمة البيضاء + التحقق + التطبيق (11 §4)
# ======================================================================
def current_setting(key):
    import settings
    if key == "delivery_mode":
        return settings.delivery_mode()
    if key == "role":
        return settings.role()
    if key == "transcript_header_target":
        return settings.header_target()
    if key == "context_mode":
        import sessions
        return "simple" if sessions.context_mode() in ("simple", "off") else "auto"
    attr = SET_CONFIG_ATTR.get(key)
    return getattr(Config, attr, None) if attr else None


def validate_setting(key, raw):
    """يرجع (ok, value_or_error). يرفض الممنوع، النوع الخاطئ، والقيم خارج المدى (DoD 4)."""
    k = (key or "").strip().lower()
    if k in SET_FORBIDDEN or any(f in k for f in ("key", "token", "secret", "password")):
        return False, "ممنوع: هذا الإعداد لا يُغيَّر من البوت (من البيئة/Config فقط)."
    if k not in SET_WHITELIST:
        return False, f"غير معروف. المسموح: {', '.join(SET_WHITELIST)}"
    typ, rng, _desc = SET_WHITELIST[k]
    v = (raw or "").strip()
    if typ == "choice":
        v = v.lower()
        if k == "delivery_mode":
            import settings
            v = settings._MODE_ALIASES.get(v, v)
        elif k == "transcript_header_target":
            v = {"raw": "raw_only", "polished": "polished_only"}.get(v, v)
        elif k == "backup_weekly_day" and v not in rng:
            import backup
            if v in backup._DAY_ALIASES or v.isdigit():
                v = rng[backup.weekday_index(v)]
        if v not in rng:
            return False, f"القيمة غير صالحة. المتاح: {' | '.join(rng)}"
        return True, v
    if typ == "bool":
        if v.lower() in ("1", "on", "true", "yes", "تشغيل", "نعم"):
            return True, True
        if v.lower() in ("0", "off", "false", "no", "إيقاف", "لا"):
            return True, False
        return False, "القيمة: on | off"
    if typ == "int":
        try:
            n = int(v)
        except ValueError:
            return False, "القيمة يجب أن تكون رقماً صحيحاً."
        if not (rng[0] <= n <= rng[1]):
            return False, f"خارج المدى: {rng[0]}–{rng[1]}"
        return True, n
    if typ == "role":
        import prompts
        if prompts.get_role(v.lower()) is None:
            return False, "دور غير معروف. /roles للقائمة."
        return True, v.lower()
    return False, "نوع غير مدعوم."  # pragma: no cover


def apply_setting(key, value):
    """يطبّق قيمة مُتحقَّقاً منها. يرجع (old, new). كل تغيير → audit عبر settings.set_value."""
    import settings
    k = key.strip().lower()
    old = current_setting(k)
    if k == "delivery_mode":
        settings.set_delivery_mode(value)
    elif k == "role":
        settings.set_role(value)
    elif k == "transcript_header_target":
        settings.set_header_target(value)
    elif k == "context_mode":
        import sessions
        sessions.set_context_enabled(value == "auto")
        settings.set_value("context_mode", value)
    else:
        settings.set_value(k, value)
        setattr(Config, SET_CONFIG_ATTR[k], value)
    return old, current_setting(k)


def apply_persisted_overrides():
    """عند الإقلاع: تجاوزات settings.json → Config (الطبقتان — 11 §4). يرجع عدد المطبَّق."""
    import settings
    n = 0
    for k, attr in SET_CONFIG_ATTR.items():
        v = settings.get(k)
        if v is None:
            continue
        ok, val = validate_setting(k, ("1" if v else "0") if isinstance(v, bool) else str(v))
        if ok:
            setattr(Config, attr, val)
            n += 1
    return n


# ======================================================================
# Buttons = Commands (11 DoD 2) — جدول الربط الذي يُختبر
# ======================================================================
COMMAND_PARITY = {
    "p:st": "/status", "p:dr": "/doctor", "p:home": "/panel",
    "k:l": "/keys", "k:v": "/keys", "k:d": "/remove", "k:D": "/remove", "k:n": "/rename", "k:a": "(لصق المفتاح)",
    "m": "/mode", "h": "/header",
    "r:l": "/roles", "r:s": "/role", "r:n": "/new_role", "r:d": "/role_del", "r:D": "/role_del",
    "s:l": "/sessions", "s:v": "/sessions", "s:r": "/resume", "s:e": "/end", "s:x": "/export", "s:n": "/new_session", "s:N": "/rename_session",
    "g:l": "/glossary", "g:v": "/glossary", "g:c": "/confirm_word", "g:d": "/del_word", "g:D": "/del_word", "g:a": "/add_word",
    "c": "/context", "L:t": "/logs", "L:e": "/logs errors", "L:r": "/trace", "L:a": "/audit", "L:menu": "/panel",
    "b:n": "/backup", "b:s": "/backup_secrets", "b:S": "/backup_secrets confirm", "b:t": "/restore_test", "b:l": "/backups", "b:menu": "/panel",
    "S:l": "/set", "S:v": "/set", "S:s": "/set", "S:e": "/set", "m:menu": "/mode", "c:menu": "/context",
}


def parity_key(data):
    """مفتاح جدول COMMAND_PARITY لـ callback_data معيّن."""
    sec, act, _arg = parse(data)
    if f"{sec}:{act}" in COMMAND_PARITY:
        return f"{sec}:{act}"
    return sec


def all_screens():
    """كل الشاشات القابلة للتوليد — لاختبار DoD 1 (كل callback_data ≤ 64 بايت) وDoD 2 (parity)."""
    return [home(), keys_list(1), mode_menu(), roles_list(), sessions_list(1), glossary_list(1), context_menu(),
            logs_menu(), backup_menu(), backup_menu(True), settings_list()] + [setting_view(k) for k in SET_WHITELIST]
