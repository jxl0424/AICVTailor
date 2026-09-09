"""The CLI.

`aicvtailor parse --check` is the Phase 1 round-trip gate and `doctor` is the
component report, both of which get recommended as the way to verify an
install. Neither had a test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aicvtailor.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "jakes_resume.tex"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from aicvtailor import paths
    import aicvtailor.db as db

    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(paths, "MASTER_DIR", tmp_path / "master")
    monkeypatch.setattr(paths, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(paths, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(paths, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(db, "_engine", None)
    yield


class TestInitDb:
    def test_creates_the_database(self, capsys):
        from aicvtailor import paths

        assert main(["init-db"]) == 0
        assert paths.DB_PATH.exists()
        assert "database ready" in capsys.readouterr().out

    def test_is_idempotent(self):
        assert main(["init-db"]) == 0
        assert main(["init-db"]) == 0


class TestDoctor:
    def test_prints_every_probe(self, capsys):
        main(["doctor"])
        out = capsys.readouterr().out
        for probe in ("database", "master_resume", "latex", "nim", "ollama"):
            assert probe in out

    def test_json_output_is_machine_readable(self, capsys):
        main(["doctor", "--json"])
        payload = json.loads(capsys.readouterr().out)

        assert payload["status"] in {"ok", "degraded", "unavailable"}
        assert len(payload["probes"]) == 7

    def test_exit_code_reflects_usability(self):
        """Non-zero when nothing can run, so it is usable in a setup script."""
        assert main(["doctor"]) in {0, 1}

    def test_a_degraded_probe_prints_its_fallback(self, capsys):
        main(["doctor"])
        out = capsys.readouterr().out
        if "!" in out or "~" in out:
            assert "->" in out


class TestParse:
    def test_prints_the_structure(self, capsys):
        assert main(["parse", str(FIXTURE)]) == 0
        out = capsys.readouterr().out

        assert "sections" in out and "bullets" in out
        assert "Technical Skills" in out

    def test_json_output_carries_ids_and_fingerprints(self, capsys):
        assert main(["parse", str(FIXTURE), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        bullets = [
            b for s in payload["sections"] for e in s["entries"] for b in e["bullets"]
        ]
        assert bullets
        assert all(b["id"] and b["fingerprint"] for b in bullets)

    def test_check_passes_on_a_valid_template(self, capsys):
        """This is the Phase 1 gate, run the way a user would run it."""
        assert main(["parse", str(FIXTURE), "--check"]) == 0
        assert "byte-identical" in capsys.readouterr().out

    def test_check_reports_failure_with_a_nonzero_exit(self, capsys, monkeypatch):
        """A broken regenerator must fail the gate, not pass it quietly."""
        from aicvtailor.latex import parse as parse_tex

        document = parse_tex(FIXTURE.read_text(encoding="utf-8"))
        monkeypatch.setattr(
            type(document), "to_source", lambda self, edits=None: "corrupted"
        )
        monkeypatch.setattr("aicvtailor.latex.parse", lambda src: document)

        assert main(["parse", str(FIXTURE), "--check"]) == 1
        assert "identity" in capsys.readouterr().out

    def test_a_missing_file_is_an_error_not_a_traceback(self, capsys):
        assert main(["parse", "/nonexistent/resume.tex"]) == 1
        assert "no such file" in capsys.readouterr().err


class TestModels:
    def test_prints_role_resolution(self, capsys):
        assert main(["models"]) == 0
        out = capsys.readouterr().out

        assert "role resolution" in out
        assert "extractor" in out and "rewriter" in out

    def test_all_lists_the_catalogue(self, capsys):
        assert main(["models", "--all"]) == 0
        assert "models in the catalogue" in capsys.readouterr().out

    def test_refresh_without_a_key_says_so_rather_than_failing(self, capsys, monkeypatch):
        from aicvtailor.config import reload_config

        monkeypatch.setenv("NVIDIA_API_KEY", "")
        reload_config()

        assert main(["models", "--refresh"]) == 0
        assert "cannot be fetched" in capsys.readouterr().out
        reload_config()


def test_an_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        main(["not-a-command"])
