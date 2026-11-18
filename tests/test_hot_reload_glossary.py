# -*- coding: utf-8 -*-
"""
اختبارات T3b (DEC-P2-09): FileStampWatcher والتحديث الكسول لمعجم glossary.py وبصمة الملفات.
- خلو تام من time.sleep() وخيوط daemon الخلفية.
- تغطية حالات المراقب: missing -> changed -> mark_loaded -> throttled -> force -> unchanged.
- حماية ملف المالك من الكتابة فوقه أو حجبه عند التلف في التحديث الحي (مع الحفاظ على حجر الإقلاع الأولي).
- رصد الحذف اليدوي للمداخل الافتراضية وتسجيل tombstones بالمعرف ID (مثل g_008).
- إزالة tombstone تلقائياً عند عودة المدخل الافتراضي (سواء يدوياً بـ Ctrl+Z أو برمجياً عبر add).
- منع حلقات إعادة التحميل الذاتية الناتجة عن زيادة عداد hits.
"""

import copy
import json
import logging
import os

import config
from glossary import Glossary, DEFAULT_ENTRIES
from hot_reload import FileStampWatcher, MIN_POLL_INTERVAL_S

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def test_file_stamp_watcher_lifecycle_and_force(tmp_path):
    """دورة حياة FileStampWatcher بالكامل: الغياب، التغيير، التهدئة، التجاوز force=True، والبصمة التالفة."""
    target = os.path.join(str(tmp_path), "sample.txt")
    watcher = FileStampWatcher(poll_interval_s=5.0)

    # 1) الملف غير موجود
    state, sig = watcher.check(target)
    assert state == "missing" and sig is None

    # 2) إنشاء الملف
    with open(target, "w", encoding="utf-8") as f:
        f.write("content 1")
    state, sig1 = watcher.check(target)
    assert state == "changed" and sig1 is not None

    # 3) تسجيل التحميل
    watcher.mark_loaded(target, sig=sig1)

    # 4) فحص فوري بدون force يرجع throttled
    state, sig_th = watcher.check(target, force=False)
    assert state == "throttled" and sig_th == sig1

    # 5) فحص بـ force=True يرجع unchanged
    state, sig_unc = watcher.check(target, force=True)
    assert state == "unchanged" and sig_unc == sig1

    # 6) تعديل بحجم مختلف مع force=True يرجع changed
    with open(target, "w", encoding="utf-8") as f:
        f.write("content version two with longer body")
    state, sig2 = watcher.check(target, force=True)
    assert state == "changed" and sig2 != sig1

    # 7) رصد البصمة التالفة: تسجل تحذيراً مرة واحدة فقط
    assert watcher.mark_bad(sig2) is True
    assert watcher.mark_bad(sig2) is False

    # 8) حدود فاصل التهدئة والـ reset
    watcher.set_poll_interval(2.0)
    assert watcher.poll_interval_s == MIN_POLL_INTERVAL_S  # سقف أدنى >= 5.0
    watcher.set_poll_interval(10.0)
    assert watcher.poll_interval_s == 10.0
    watcher.set_poll_interval(None)
    assert watcher.poll_interval_s == float("inf")

    watcher.reset()
    assert watcher._loaded_sig is None and watcher._path is None


def test_glossary_lazy_reload_picks_up_external_edit(fresh_env):
    """تعديل glossary.json خارجياً يلتقطه apply() كسولاً دون الحاجة لإعادة تشغيل البوت."""
    g = Glossary(config.Config.GLOSSARY_FILE)
    text_in = "هذا نص مع كلمة_اختبارية_جديدة هنا"
    out1, hits1 = g.apply(text_in)
    assert hits1 == 0

    # تعديل خارجي يضيف مدخلاً جديداً بحجم مختلف
    with open(g.path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    doc["entries"].append({
        "id": "g_custom_999",
        "patterns": ["كلمة_اختبارية_جديدة"],
        "replacement": "تحديث_ناجح",
        "kind": "spelling",
        "status": "confirmed",
        "scope": "both",
        "hits": 0,
        "created": "2026-11-18T10:00:00Z"
    })
    with open(g.path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)

    # فرض تجاوز فاصل التهدئة لمحاكاة مرور الـ 5 ثوانٍ
    g.maybe_reload(force=True)
    out2, hits2 = g.apply(text_in)
    assert hits2 == 1
    assert "تحديث_ناجح" in out2


def test_glossary_hits_update_does_not_trigger_redundant_reload(fresh_env):
    """زيادة عداد hits بعد apply(count_hits=True) تسجل البصمة فوراً وتمنع إعادة التحميل الوهمية."""
    g = Glossary(config.Config.GLOSSARY_FILE)
    g.add("كلمة_قابلة_للتكرار", "بديل_معتمد", kind="spelling")

    # apply مع عدّ الزيارات يكتب في الملف
    out, hits = g.apply("جملة فيها كلمة_قابلة_للتكرار", count_hits=True)
    assert hits == 1

    # البصمة المحفوظة في _watcher مطابقة تماماً للملف على القرص
    st_sig = g._watcher._stat_sig(g.path)
    assert g._watcher._loaded_sig == st_sig

    # فحص صريح بـ force=True يجب أن يؤكد أن الملف unchanged
    state, sig = g._watcher.check(g.path, force=True)
    assert state == "unchanged"


def test_glossary_manual_delete_records_tombstone_by_id_and_restores_cleanly(fresh_env):
    """حذف مدخل افتراضي يدوياً من glossary.json يسجل معرّفه (مثل g_008) في tombstones وإعادته تطهرها."""
    g = Glossary(config.Config.GLOSSARY_FILE)
    assert "g_008" in g._present_default_ids

    # المالك يفتح glossary.json ويحذف مدخل عربيتي (g_008)
    with open(g.path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    doc["entries"] = [e for e in doc["entries"] if e.get("id") != "g_008"]
    with open(g.path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)

    # التحديث الكسول يرصد الحذف اليدوي ويسجل g_008 في tombstones
    g.maybe_reload(force=True)
    assert "g_008" in g.doc.get("tombstones", [])
    assert "g_008" not in g._present_default_ids

    # إعادة فتح/مزامنة لا تعيد إنعاش g_008 طالما هو في tombstones
    g.maybe_reload(force=True)
    assert not any(e.get("id") == "g_008" for e in g.doc["entries"])

    # المالك يتراجع عن الحذف يدوياً (Ctrl+Z) ويعيد مدخل g_008 للملف
    for def_e in DEFAULT_ENTRIES:
        if def_e["id"] == "g_008":
            doc["entries"].append(copy.deepcopy(def_e))
            break
    with open(g.path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)

    # التحديث يرصد عودة g_008 ويطهره تلقائياً من tombstones
    g.maybe_reload(force=True)
    assert "g_008" in g._present_default_ids
    assert "g_008" not in g.doc.get("tombstones", [])


def test_glossary_remove_and_add_records_tombstone_by_id(fresh_env):
    """remove لمدخل افتراضي يسجل ID المدخل (g_008) في tombstones، وadd يطهره فوراً."""
    g = Glossary(config.Config.GLOSSARY_FILE)
    assert any(e.get("id") == "g_008" for e in g.doc["entries"])

    # حذف g_008 عبر API
    res = g.remove("g_008")
    assert res is not None and res.get("id") == "g_008"
    assert "g_008" in g.doc.get("tombstones", [])

    # إعادة إضافته عبر add تطهر الـ tombstone
    g.add("موديل", "Model", kind="spelling")
    assert "g_008" not in g.doc.get("tombstones", [])


def test_glossary_live_reload_corrupt_file_keeps_memory_doc_and_warns_once(fresh_env, caplog):
    """الملف التالف أثناء التحديث الحي لا يُحجر ولا يُستبدل، وتبقى النسخة السليمة في الذاكرة بتحذير واحد."""
    g = Glossary(config.Config.GLOSSARY_FILE)
    g.add("نمط_صالح_موجود", "بديل_صالح", kind="spelling")
    assert g.apply("نمط_صالح_موجود")[0] == "بديل_صالح"

    corrupt_content = "{ corrupt json payload without closing brace ..."
    with open(g.path, "w", encoding="utf-8") as f:
        f.write(corrupt_content)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="alsami.glossary"):
        # استدعاءان متتاليان بـ force=True
        assert g.maybe_reload(force=True) is False
        assert g.maybe_reload(force=True) is False

    # 1) تحذير واحد فقط سُجل للبصمة التالفة
    warns = [r for r in caplog.records if "تعذر التحديث الحي لـ glossary.json" in r.getMessage()]
    assert len(warns) == 1

    # 2) ملف المالك على القرص سليم كما تركه لم يُمس
    with open(g.path, "r", encoding="utf-8") as f:
        assert f.read() == corrupt_content

    # 3) النسخة الصالحة في الذاكرة استمرت بالعمل
    assert g.apply("نمط_صالح_موجود")[0] == "بديل_صالح"


def test_glossary_initial_boot_quarantines_corrupt_legacy_contract(fresh_env):
    """عقد الإقلاع الأولي القديم: ملف تالف قبل إنشاء كائن Glossary يُحجر إلى .corrupt-* ويُعاد إنشاء الافتراضي."""
    p = config.Config.GLOSSARY_FILE
    with open(p, "w", encoding="utf-8") as f:
        f.write("{ invalid json on fresh boot ...")

    g = Glossary(p)
    # المعجم يعمل بالافتراضي
    assert len(g.doc["entries"]) > 0

    # تم إنشاء ملف حجر .corrupt-* في المجلد
    parent = os.path.dirname(p)
    corrupt_backups = [fn for fn in os.listdir(parent) if fn.startswith("glossary.json.corrupt-")]
    assert len(corrupt_backups) >= 1


def test_hot_reload_no_unauthorized_imports(fresh_env):
    """شرط قبول T3b: لا استيراد لـ hot_reload إلا في glossary.py و pipeline_config.py فقط."""
    import re
    import_re = re.compile(r"^\s*(?:import\s+hot_reload|from\s+hot_reload\s+import)", re.MULTILINE)
    for fn in os.listdir(ROOT):
        if fn.endswith(".py") and fn not in ("glossary.py", "pipeline_config.py", "hot_reload.py"):
            src = open(os.path.join(ROOT, fn), encoding="utf-8").read()
            assert not import_re.search(src), f"{fn} يستورد hot_reload بشكل غير مصرح"
