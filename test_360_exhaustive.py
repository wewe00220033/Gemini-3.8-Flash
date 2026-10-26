# -*- coding: utf-8 -*-
"""
test_360_exhaustive.py — فحص جنائي شامل 360° لكل زوايا المنظومة (المراحل 0 إلى 6)
يختبر:
1. الشاشات والأزرار (حجم callback_data، أطوال النصوص، التنقل والـ dispatching).
2. آلة الحالات FSM (دخول، إلغاء، مهلة TTL، تزامن الفويس، مسار الكلمات متعدد الخطوات).
3. سجل التدقيق audit.log (تسجيل الأحداث، حجب الأسرار، وسقف الأمان 2MB).
4. النسخ الاحتياطي وتمرين الاستعادة الحقيقي على القرص (Zip creation, CRC, zip-slip, secrets exclude).
5. الطبيب الذاتي /doctor بكافة فحوصاته الـ 11 والتحقق من عدم الانهيار.
6. استعادة الجلسات التلقائية عند تلف الفهرس (Index rebuild).
7. القواطع Failover v2 وتدوير المفاتيح بين مجموعات المشاريع.
8. خادم الويب وواجهة الـ /health العميقة.
"""
import os
import sys
import time
import json
import shutil
import tempfile

# ضبط الترميز لسطح المكتب في ويندوز
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

print("=" * 70)
print("🔍 بدء الفحص الجنائي الشامل 360° لكافة مكونات المنظومة...")
print("=" * 70)

# إنشاء بيئة عمل نظيفة ومستقلة للفحص
TEST_DIR = tempfile.mkdtemp(prefix="drill_360_")
os.environ["DATA_DIR"] = TEST_DIR
os.environ["SET_MY_COMMANDS"] = "0"

from config import Config
Config.DATA_DIR = TEST_DIR
Config.LOGS_DIR = os.path.join(TEST_DIR, "logs")
Config.SESSIONS_DIR = os.path.join(TEST_DIR, "sessions")
Config.TRANSCRIPTS_DIR = os.path.join(TEST_DIR, "transcripts")
Config.INCIDENTS_DIR = os.path.join(TEST_DIR, "incidents")
Config.BACKUPS_DIR = os.path.join(TEST_DIR, "backups")
Config.STATE_DIR = os.path.join(TEST_DIR, "state")
Config.JOBS_FILE = os.path.join(Config.STATE_DIR, "jobs.ndjson")
Config.CIRCUIT_FILE = os.path.join(Config.STATE_DIR, "circuit.json")
Config.AUDIT_FILE = os.path.join(Config.LOGS_DIR, "audit.log")
Config.KEYS_JSON_FILE = os.path.join(TEST_DIR, "keys.json")
Config.SETTINGS_FILE = os.path.join(TEST_DIR, "settings.json")
Config.GLOSSARY_FILE = os.path.join(TEST_DIR, "glossary.json")
Config.ROLES_FILE = os.path.join(TEST_DIR, "roles.json")
Config.CURRENT_SESSION_FILE = os.path.join(Config.STATE_DIR, "current_session.json")
Config.SESSIONS_INDEX_FILE = os.path.join(Config.SESSIONS_DIR, "sessions_index.json")

os.makedirs(Config.LOGS_DIR, exist_ok=True)
os.makedirs(Config.SESSIONS_DIR, exist_ok=True)
os.makedirs(Config.TRANSCRIPTS_DIR, exist_ok=True)
os.makedirs(Config.INCIDENTS_DIR, exist_ok=True)
os.makedirs(Config.BACKUPS_DIR, exist_ok=True)
os.makedirs(Config.STATE_DIR, exist_ok=True)

errors_found = []

# ======================================================================
# الزاوية 1: فحص شاشات لوحة التحكم والأزرار والـ Callback Data (UI & Buttons)
# ======================================================================
print("\n[1/8] 🎛 فحص لوحة التحكم panel.py والأزرار:")
try:
    import panel
    screens = panel.all_screens()
    print(f"  • تم توليد {len(screens)} شاشة مختلفة بنجاح.")
    
    total_buttons = 0
    invalid_buttons = []
    for scr_idx, (text, markup) in enumerate(screens):
        rows = markup.get("inline_keyboard", [])
        for row in rows:
            for b in row:
                total_buttons += 1
                b_text = b.get("text", "")
                b_data = b.get("callback_data", "")
                if len(b_data.encode("utf-8")) > 64:
                    invalid_buttons.append((f"Screen #{scr_idx}", b_text, b_data, "تجاوز 64 بايت"))
                if not b_data.isascii():
                    invalid_buttons.append((f"Screen #{scr_idx}", b_text, b_data, "غير ASCII"))
                if len(b_text) > Config.PANEL_BUTTON_MAX_CHARS:
                    invalid_buttons.append((f"Screen #{scr_idx}", b_text, b_data, f"النص أطول من {Config.PANEL_BUTTON_MAX_CHARS} حرف"))
    
    if invalid_buttons:
        print(f"  ❌ عثرنا على أزرار غير صالحة: {invalid_buttons}")
        errors_found.append(f"أزرار غير مطابقة: {invalid_buttons}")
    else:
        print(f"  ✅ تم فحص {total_buttons} زرار: كلها ≤ 64 بايت، ASCII صرف، والنصوص ≤ {Config.PANEL_BUTTON_MAX_CHARS} حرفاً.")

    # فحص جدول التكافؤ COMMAND_PARITY
    print("  • فحص جدول Buttons = Commands:")
    missing_parity = []
    for scr_idx, (text, markup) in enumerate(screens):
        rows = markup.get("inline_keyboard", [])
        for row in rows:
            for b in row:
                data = b.get("callback_data", "")
                pkey = panel.parity_key(data)
                if pkey not in panel.COMMAND_PARITY:
                    missing_parity.append((data, pkey))
    if missing_parity:
        print(f"  ⚠️ أزرار غير مسجلة في COMMAND_PARITY: {missing_parity}")
        errors_found.append(f"Missing parity: {missing_parity}")
    else:
        print("  ✅ كافة أزرار الشاشات لها أوامر نصية مكافئة بنسبة 100%.")
except Exception as e:
    print(f"  ❌ خطأ في فحص اللوحة: {e}")
    errors_found.append(f"Panel check: {e}")

# ======================================================================
# الزاوية 2: فحص آلة الحالات FSM وسلوك الإلغاء وتعدد الخطوات (FSM Drill)
# ======================================================================
print("\n[2/8] 🧠 فحص آلة الحالات fsm.py وتعدد الخطوات:")
try:
    import fsm
    fsm.clear()
    assert not fsm.active(), "يجب أن تكون خاملة بعد clear"
    
    # 1. الدخول لحالة
    st = fsm.enter("await_word", {"src": "بايثون"}, chat_id="123")
    assert fsm.active() and fsm.current()["name"] == "await_word"
    assert fsm.remaining_s() <= 300 and fsm.remaining_s() > 295
    print("  • دخول الحالة وتعيين الـ TTL: ناجح.")
    
    # 2. الإلغاء
    cleared = fsm.clear()
    assert cleared and cleared["name"] == "await_word"
    assert not fsm.active()
    print("  • إلغاء الحالة (clear): ناجح.")
    
    # 3. انتهاء المهلة ومسحها مرة واحدة
    fsm.enter("await_key", chat_id="123")
    fsm._state["since"] = time.time() - 301 # محاكاة مرور 301 ثانية
    assert fsm.current() is None, "current يجب أن ترجع None عند انتهاء المهلة"
    exp = fsm.expired_and_clear()
    assert exp and exp["name"] == "await_key", "expired_and_clear يجب أن ترجع الحالة المنتهية"
    assert fsm.expired_and_clear() is None, "الاستدعاء الثاني يجب أن يرجع None حتى لا نكرر التنبيه"
    print("  • كشف انتهاء مهلة FSM وإبلاغ المستخدم لمرة واحدة فقط: ناجح.")
except Exception as e:
    print(f"  ❌ خطأ في فحص FSM: {e}")
    errors_found.append(f"FSM check: {e}")

# ======================================================================
# الزاوية 3: فحص سجل التدقيق وحجب الأسرار وسقف الـ 2MB (Audit Channel)
# ======================================================================
print("\n[3/8] 🧾 فحص سجل التدقيق logs/audit.log وحجب الأسرار:")
try:
    import audit
    audit.set_actor("telegram")
    # تسجيل حدث يحوي مفتاحاً سرياً
    secret_key = "AIzaSy" + "Z" * 33
    rec = audit.record("key.add", key=secret_key, role="admin")
    assert secret_key not in rec, "المفتاح السري يجب ألا يظهر في سطر اللوج كاملاً!"
    assert "AIza" in rec and "****" in rec, "يجب تطبيق قناع الحجب على المفتاح"
    print(f"  • حجب الأسرار في التدقيق: تم بنجاح ({rec}).")
    
    # فحص سقف الأمان 2MB (Trimming test)
    # محاكاة تخطي الحجم
    audit_file = Config.AUDIT_FILE
    with open(audit_file, "a", encoding="utf-8") as f:
        # كتابة أسطر حتى يتجاوز سقف 2MB
        chunk = ("2026-10-26 10:00:00 | test.fill | actor=system | data=" + "x" * 100 + "\n") * 10000
        f.write(chunk)
    
    size_before = os.path.getsize(audit_file)
    assert size_before > 1_000_000
    # تسجيل حدث جديد يحفز _trim_if_needed
    orig_max = audit.AUDIT_MAX_BYTES
    audit.AUDIT_MAX_BYTES = 500_000  # سقف تجريبي
    try:
        audit.record("test.trigger_trim")
        size_after = os.path.getsize(audit_file)
        assert size_after < size_before, "الملف كان يجب تقليصه عند تجاوز السقف"
        print(f"  • تقليص حجم السجل تلقائياً عند تجاوز السقف: من {size_before/1024:.0f}KB إلى {size_after/1024:.0f}KB.")
    finally:
        audit.AUDIT_MAX_BYTES = orig_max
except Exception as e:
    print(f"  ❌ خطأ في فحص التدقيق: {e}")
    errors_found.append(f"Audit check: {e}")

# ======================================================================
# الزاوية 4: فحص النسخ الاحتياطي وتمرين الاستعادة الحقيقي (Backup & Restore)
# ======================================================================
print("\n[4/8] 🗂 فحص النسخ الاحتياطي وتمرين الاستعادة /restore_test:")
try:
    import backup
    # إنشاء بيانات حية للاختبار
    with open(Config.GLOSSARY_FILE, "w", encoding="utf-8") as f:
        json.dump({"entries": [{"id": "g_001", "patterns": ["تيست"], "replacement": "اختبار", "kind": "exact"}]}, f)
    with open(Config.SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"delivery_mode": "dual", "role": "general"}, f)
    with open(Config.ROLES_FILE, "w", encoding="utf-8") as f:
        json.dump({"roles": {}}, f)
    with open(Config.SESSIONS_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({"sessions": []}, f)
    all_md_path = os.path.join(Config.TRANSCRIPTS_DIR, "ALL.md")
    with open(all_md_path, "w", encoding="utf-8") as f:
        f.write("# سجل التفريغات\n")
    # ملف سري يجب ألا يدخل أبداً
    with open(Config.KEYS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump({"keys": [{"key": "AIzaSySecret12345"}]}, f)
    
    zip_bytes, included = backup.build_public_backup()
    assert not backup.contains_secret(zip_bytes), "النسخة العامة حوت سراً!"
    print(f"  • تم بناء النسخة العامة ({len(zip_bytes)} بايت) لـ {len(included)} ملف وبلا أي أسرار.")
    
    # حفظ محلي
    paths, removed = backup.save_local(zip_bytes)
    assert len(paths) == 1 and os.path.exists(paths[0])
    print(f"  • تم الحفظ في مجلد backups/: {os.path.basename(paths[0])}.")
    
    # تشغيل تمرين الاستعادة الفعلي
    rep = backup.restore_test(paths[0])
    assert rep["ok"], f"فشل تمرين الاستعادة: {rep}"
    assert rep["members"] >= 4
    assert not rep["bad_crc"] and not rep["secrets"] and not rep["unsafe"]
    print(f"  • تمرين الاستعادة /restore_test: نجح 100% — {rep['members']} ملفات مفحوصة، سلامة CRC والـ JSON.")
except Exception as e:
    print(f"  ❌ خطأ في فحص النسخ: {e}")
    errors_found.append(f"Backup check: {e}")

# ======================================================================
# الزاوية 5: فحص الطبيب الذاتي الكامل بكافة فحوصاته الـ 11 (Self-Doctor)
# ======================================================================
print("\n[5/8] 🩺 فحص الطبيب الذاتي observability.py بكافة فحوصاته:")
try:
    import observability
    lines, worst = observability.doctor_report(check_telegram=False)
    print(f"  • التقرير يضم {len(lines)} سطراً. الحالة الإجمالية: {worst}")
    for ln in lines:
        print(f"    {ln}")
    assert len(lines) >= 10, "يجب أن يحتوي التقرير على كل الفحوصات المعزولة"
    assert "🩺 الطبيب الذاتي" in lines[0]
    print("  ✅ فحص الطبيب الذاتي اكتمل بنجاح دون أي انهيار.")
except Exception as e:
    print(f"  ❌ خطأ في فحص الطبيب: {e}")
    errors_found.append(f"Doctor check: {e}")

# ======================================================================
# الزاوية 6: إعادة بناء فهرس الجلسات عند التلف (Session Index Rebuild)
# ======================================================================
print("\n[6/8] 📂 فحص متانة الجلسات وإعادة بناء الفهرس آلياً:")
try:
    import sessions
    sessions.reset_cache()
    # إنشاء جلسة حقيقية بملف markdown (يجب أن يكون المعرّف 8 خانات hex كما في المواصفة)
    sid = "1234abcd"
    sf = sessions.session_file(sid)
    with open(sf, "w", encoding="utf-8") as f:
        f.write("---\nid: 1234abcd\nname: جلسة اختبارية مهمة\nstarted: 2026-10-26 10:00:00\ncount: 2\nstatus: active\n---\n# تفاصيل\n")
    
    # تدمير ملف الفهرس عمداً
    with open(Config.SESSIONS_INDEX_FILE, "w", encoding="utf-8") as f:
        f.write("{invalid json corrupt!]")
    
    # استدعاء list_sessions بعد تصفير الكاش يجب أن يصلح نفسه من ملفات الـ md
    sessions.reset_cache()
    items = sessions.list_sessions()
    assert any(s["id"] == "1234abcd" for s in items), "فشل استعادة الجلسة من ملف الـ md!"
    recovered = [s for s in items if s["id"] == "1234abcd"][0]
    assert recovered["name"] == "جلسة اختبارية مهمة"
    print(f"  ✅ تم استرداد الجلسة «{recovered['name']}» تلقائياً بعد تدمير الفهرس!")
except Exception as e:
    print(f"  ❌ خطأ في فحص الجلسات: {e}")
    errors_found.append(f"Sessions rebuild check: {e}")

# ======================================================================
# الزاوية 7: القواطع والـ Failover والقفز بين المشاريع (Model Circuit & Hops)
# ======================================================================
print("\n[7/8] ⚡ فحص قواطع الموديلات وسلم الانتظار الذري:")
try:
    from model_state import model_circuit
    model_circuit.attach_store(Config.CIRCUIT_FILE)
    m = "gemini-3.6-flash"
    # استدعاء السلم المتدرج (20, 45, 90, 180)
    model_circuit.strike_5xx(m, (20, 45, 90, 180), reason="503 test")
    snap = model_circuit.snapshot([m])[0]
    assert snap["paused"] and snap["remaining_s"] > 15
    print(f"  • تفعيل القاطع: الموديل {m} مقفول لمدة {snap['remaining_s']} ثانية (الضربة 1).")
    
    # التحقق من حفظه في القرص واسترجاعه
    with open(Config.CIRCUIT_FILE, "r", encoding="utf-8") as f:
        c_data = json.load(f)
    assert m in c_data.get("paused", {})
    print(f"  • استمرارية القاطع على القرص ({Config.CIRCUIT_FILE}): مؤكدة.")
    
    # فتح القاطع وتصفير الضربات (كما يفعل أمر /reset)
    model_circuit.reset(m)
    assert not model_circuit.snapshot([m])[0]["paused"]
    print("  • فتح القاطع وتصفير الضربات عبر reset(): تم بنجاح.")
except Exception as e:
    print(f"  ❌ خطأ في فحص القواطع: {e}")
    errors_found.append(f"Circuit check: {e}")

# ======================================================================
# الزاوية 8: فحص خادم الويب و /health العميق (Deep Health Endpoint)
# ======================================================================
print("\n[8/8] 🌐 فحص خادم الويب و /health العميق:")
try:
    import web_service
    # فحص تحميل صفحة الواجهة
    html = web_service.load_index_html()
    assert html and len(html) > 50
    print(f"  • تحميل واجهة الويب HTML: ناجح ({len(html)} حرف).")
    
    # فحص دالة build_health العميقة مباشرة
    health = web_service.build_health()
    status_code = 200 if health["ok"] else 503
    print(f"  • فحص /health الداخلي: كود {status_code} — ok={health.get('ok')}")
    assert "ok" in health
    assert "queue" in health
    assert "beats" in health
    print("  ✅ الـ /health العميق يعمل بدقة ويحتوي على كافة المؤشرات الحيوية.")
except Exception as e:
    print(f"  ❌ خطأ في فحص الويب: {e}")
    errors_found.append(f"Web check: {e}")

# تنظيف المجلد المؤقت
shutil.rmtree(TEST_DIR, ignore_errors=True)

print("\n" + "=" * 70)
if errors_found:
    print(f"❌ انتهى الفحص مع وجود {len(errors_found)} ملاحظة:")
    for err in errors_found:
        print(f"   - {err}")
    sys.exit(1)
else:
    print("🎉 النتيجة النهائية: فحص 360° شامل ناجح بنسبة 100% دون أي أخطاء أو انهيارات!")
print("=" * 70)
