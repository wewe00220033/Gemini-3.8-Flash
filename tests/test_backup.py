# -*- coding: utf-8 -*-
import io
import zipfile

import pytest


@pytest.fixture
def bk(fresh_env, tmp_path, monkeypatch):
    import importlib
    import backup
    importlib.reload(backup)
    monkeypatch.setattr(backup.Config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(backup.Config, "KEYS_JSON_FILE", str(tmp_path / "elsewhere" / "keys.json"))
    (tmp_path / "data" / "sessions").mkdir(parents=True)
    (tmp_path / "data" / "sessions" / "s1.md").write_text("x", encoding="utf-8")
    (tmp_path / "data" / "settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / ".env").write_text("TOKEN=1", encoding="utf-8")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "keys.json").write_text('{"keys":[]}', encoding="utf-8")
    return backup


def test_public_backup_excludes_secrets_and_has_forward_slash_paths(bk):
    blob, included = bk.build_public_backup()
    assert sorted(included) == ["sessions/s1.md", "settings.json"]
    assert not bk.contains_secret(blob)
    assert all("\\" not in n and not n.startswith("..") for n in zipfile.ZipFile(io.BytesIO(blob)).namelist())


def test_secrets_backup_outside_data_dir_uses_basename_no_zip_slip(bk):
    blob, included = bk.build_secrets_backup()
    assert included == ["keys.json"]
    assert bk.contains_secret(blob)


def test_empty_when_nothing_exists(fresh_env, tmp_path, monkeypatch):
    import importlib
    import backup
    importlib.reload(backup)
    monkeypatch.setattr(backup.Config, "DATA_DIR", str(tmp_path / "nothing"))
    blob, included = backup.build_public_backup()
    assert included == [] and zipfile.ZipFile(io.BytesIO(blob)).namelist() == []



def test_public_backup_phase2_paths_and_inline_api_key_guard(bk, tmp_path):
    """T1 (DEC-P2-09): memory/ وdrafts/ وpipeline.json السليم يدخلون النسخة العامة؛ وpipeline.json بمفتاح خام يُعدّ سراً."""
    data_dir = tmp_path / "data"
    (data_dir / "memory").mkdir(parents=True)
    (data_dir / "memory" / "MEMORY.md").write_text("# owner\n", encoding="utf-8")
    (data_dir / "drafts" / "2026-11-18").mkdir(parents=True)
    (data_dir / "drafts" / "2026-11-18" / "e1.md").write_text("draft\n", encoding="utf-8")
    pipe_path = data_dir / "pipeline.json"

    # 1) مفتاح خام (>=16 حرفاً بدون ${...}) -> يُعدّ سراً فلا يدخل النسخة العامة ولا يرفع contains_secret
    pipe_path.write_text('{"providers": {"g": {"api_key": "' + ("gsk_" + "x" * 40) + '"}}}', encoding="utf-8")
    blob_dirty, inc_dirty = bk.build_public_backup()
    assert "pipeline.json" not in inc_dirty
    assert "memory/MEMORY.md" in inc_dirty and "drafts/2026-11-18/e1.md" in inc_dirty
    assert not bk.contains_secret(blob_dirty)

    # فحص دفاعي إضافي: لو أُقحم pipeline.json بمفتاح خام داخل ZIP يدوياً تكتشفه contains_secret
    manual_buf = io.BytesIO()
    with zipfile.ZipFile(manual_buf, "w") as z:
        z.writestr("pipeline.json", '{"api_key": "' + ("sk-" + "y" * 30) + '"}')
    assert bk.contains_secret(manual_buf.getvalue())

    # 2) صيغة ${GROQ_API_KEY} السليمة (أو "ollama" القصيرة) -> تدخل النسخة العامة بأمان
    pipe_path.write_text('{"providers": {"g": {"api_key": "${GROQ_API_KEY}"}, "o": {"api_key": "ollama"}}}', encoding="utf-8")
    blob_clean, inc_clean = bk.build_public_backup()
    assert "pipeline.json" in inc_clean
    assert "memory/MEMORY.md" in inc_clean and "drafts/2026-11-18/e1.md" in inc_clean
    assert not bk.contains_secret(blob_clean)
