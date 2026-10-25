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
