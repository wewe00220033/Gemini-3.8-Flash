# -*- coding: utf-8 -*-
"""اختبارات store.py — المرحلة 0 (13 §2 و §7)."""
import json
import os
import threading

import store


def test_atomic_write_json_roundtrip_and_schema(tmp_path):
    p = tmp_path / "s.json"
    store.atomic_write_json(str(p), {"a": 1}, schema_version=2)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"a": 1, "schema_version": 2}
    # لا بقايا ملفات مؤقتة في المجلد
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")] == []


def test_atomic_write_leaves_old_file_intact_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    store.atomic_write_json(str(p), {"ok": True})

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(store.os, "replace", boom)
    try:
        store.atomic_write_json(str(p), {"ok": False})
    except OSError:
        pass
    assert json.loads(p.read_text(encoding="utf-8")) == {"ok": True}
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")] == []


def test_load_tolerant_missing_returns_copy_of_default(tmp_path):
    default = {"x": [1]}
    got = store.load_json_tolerant(str(tmp_path / "none.json"), default)
    assert got == default and got is not default
    got["x"].append(2)
    assert default == {"x": [1]}


def test_load_tolerant_corrupt_is_quarantined_not_raised(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    seen = []
    got = store.load_json_tolerant(str(p), {"d": 1}, on_corrupt=lambda path, dest, err: seen.append(dest))
    assert got == {"d": 1}
    assert not p.exists()
    assert len(seen) == 1 and ".corrupt-" in seen[0] and os.path.exists(seen[0])


def test_load_tolerant_wrong_type_is_corrupt(tmp_path):
    p = tmp_path / "t.json"
    p.write_text("[1,2]", encoding="utf-8")
    assert store.load_json_tolerant(str(p), {"d": 1}) == {"d": 1}
    assert not p.exists()


def test_load_tolerant_migrates_old_schema(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"schema_version": 1, "old": 5}), encoding="utf-8")

    def migrate(data, from_v):
        assert from_v == 1
        data["new"] = data.pop("old")
        return data
    got = store.load_json_tolerant(str(p), {}, schema_version=2, migrate=migrate)
    assert got == {"schema_version": 2, "new": 5}


def test_append_json_line_and_read_back_skips_truncated_tail(tmp_path):
    p = str(tmp_path / "jobs.ndjson")
    store.append_json_line(p, {"id": 1})
    store.append_json_line(p, {"id": 2, "ar": "فويس"})
    # محاكاة انقطاع أثناء كتابة سطر ثالث
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"id": 3, "trunc')
    bad = []
    rows = store.read_json_lines(p, on_bad_line=lambda n, s, e: bad.append(n))
    assert rows == [{"id": 1}, {"id": 2, "ar": "فويس"}]
    assert bad == [3]


def test_append_is_thread_safe(tmp_path):
    p = str(tmp_path / "t.ndjson")

    def work(k):
        for i in range(50):
            store.append_json_line(p, {"k": k, "i": i}, fsync=False)
    ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    rows = store.read_json_lines(p)
    assert len(rows) == 200


def test_probe_writable(tmp_path):
    ok, detail = store.probe_writable(str(tmp_path / "data"))
    assert ok and detail == "rw"
    # مسار مستحيل: ملف موجود يُستخدم كمجلد
    f = tmp_path / "afile"
    f.write_text("x", encoding="utf-8")
    ok3, detail3 = store.probe_writable(str(f / "sub"))
    assert ok3 is False and detail3
