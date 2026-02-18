"""Unit tests for helper functions in notebooklm_mcp_server.

Tests cover: _resolve_profile_dir, _read_active_profile,
_write_active_profile, _current_profile_name, _find_cli.
"""

import json
from pathlib import Path
from unittest.mock import patch

from notebooklm_mcp_server import (
    _resolve_profile_dir,
    _read_active_profile,
    _write_active_profile,
    _current_profile_name,
    _find_cli,
    ACTIVE_PROFILE_PATH,
)


class TestResolveProfileDir:
    def test_absolute_path_returned_unchanged(self):
        if Path("/tmp").exists():
            result = _resolve_profile_dir("/tmp/custom-profile")
            assert result == Path("/tmp/custom-profile")
        else:
            result = _resolve_profile_dir("C:\\Users\\test\\custom")
            assert result == Path("C:\\Users\\test\\custom")

    def test_known_profile_names(self):
        assert _resolve_profile_dir("personal") == Path.home() / ".notebooklm"
        assert _resolve_profile_dir("work") == Path.home() / ".notebooklm-work"
        assert _resolve_profile_dir("design") == Path.home() / ".notebooklm-design"

    def test_case_insensitive(self):
        assert _resolve_profile_dir("Work") == Path.home() / ".notebooklm-work"
        assert _resolve_profile_dir("PERSONAL") == Path.home() / ".notebooklm"

    def test_unknown_profile_uses_suffix(self):
        result = _resolve_profile_dir("testing")
        assert result == Path.home() / ".notebooklm-testing"


class TestReadActiveProfile:
    def test_returns_none_when_file_missing(self, tmp_path):
        with patch("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", tmp_path / "nonexistent.json"):
            assert _read_active_profile() is None

    def test_returns_profile_name(self, tmp_path):
        path = tmp_path / "active.json"
        path.write_text(json.dumps({"active": "work"}), encoding="utf-8")
        with patch("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", path):
            assert _read_active_profile() == "work"

    def test_returns_none_on_bad_json(self, tmp_path):
        path = tmp_path / "active.json"
        path.write_text("not json", encoding="utf-8")
        with patch("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", path):
            assert _read_active_profile() is None


class TestWriteActiveProfile:
    def test_writes_and_reads_back(self, tmp_path):
        path = tmp_path / "active.json"
        with patch("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", path):
            _write_active_profile("design")
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["active"] == "design"


class TestCurrentProfileName:
    def test_returns_saved_profile(self, tmp_path):
        path = tmp_path / "active.json"
        path.write_text(json.dumps({"active": "work"}), encoding="utf-8")
        with patch("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", path):
            assert _current_profile_name() == "work"

    def test_falls_back_to_env(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        with (
            patch("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", path),
            patch.dict("os.environ", {"NOTEBOOKLM_HOME": str(Path.home() / ".notebooklm-work")}),
        ):
            assert _current_profile_name() == "work"

    def test_defaults_to_personal(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        with (
            patch("notebooklm_mcp_server.ACTIVE_PROFILE_PATH", path),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = _current_profile_name()
            assert result == "personal"


class TestFindCli:
    def test_returns_path_when_exists(self, tmp_path):
        cli = tmp_path / "notebooklm"
        cli.touch()
        with patch("notebooklm_mcp_server.sys") as mock_sys:
            mock_sys.executable = str(tmp_path / "python")
            result = _find_cli()
            assert result is not None
            assert result.name == "notebooklm"

    def test_returns_none_when_missing(self, tmp_path):
        with patch("notebooklm_mcp_server.sys") as mock_sys:
            mock_sys.executable = str(tmp_path / "python")
            result = _find_cli()
            assert result is None
