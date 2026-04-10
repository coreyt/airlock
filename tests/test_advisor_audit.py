"""Tests for airlock.advisor.audit."""

import json


class TestLogAction:
    """Tests for log_action()."""

    def test_writes_valid_jsonl(self, tmp_path):
        """log_action writes a parseable JSON line."""
        from airlock.advisor.audit import log_action

        log_action(
            action_type="query",
            description="Asked about model errors",
            outcome="success",
            model_used="local-llama",
            log_dir=tmp_path,
        )
        log_file = tmp_path / "advisor-audit.jsonl"
        assert log_file.exists()
        record = json.loads(log_file.read_text().strip())
        assert record["action_type"] == "query"
        assert record["description"] == "Asked about model errors"
        assert record["outcome"] == "success"
        assert record["model_used"] == "local-llama"
        assert "timestamp" in record

    def test_contains_required_fields(self, tmp_path):
        """Every record has timestamp, action_type, description, outcome, model_used."""
        from airlock.advisor.audit import log_action

        log_action(
            "config_apply",
            "Changed threshold",
            "success",
            "claude-sonnet",
            log_dir=tmp_path,
        )
        record = json.loads((tmp_path / "advisor-audit.jsonl").read_text().strip())
        for field in (
            "timestamp",
            "action_type",
            "description",
            "outcome",
            "model_used",
        ):
            assert field in record, f"Missing field: {field}"

    def test_appends_not_overwrites(self, tmp_path):
        """Multiple calls append lines, not overwrite."""
        from airlock.advisor.audit import log_action

        log_action("query", "First", "success", "model-a", log_dir=tmp_path)
        log_action("query", "Second", "success", "model-b", log_dir=tmp_path)
        lines = (tmp_path / "advisor-audit.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["description"] == "First"
        assert json.loads(lines[1])["description"] == "Second"

    def test_creates_missing_directory(self, tmp_path):
        """log_dir is created if it doesn't exist."""
        from airlock.advisor.audit import log_action

        nested = tmp_path / "deep" / "nested" / "logs"
        log_action("query", "test", "success", "model", log_dir=nested)
        assert (nested / "advisor-audit.jsonl").exists()

    def test_includes_details_when_provided(self, tmp_path):
        """Optional details dict is included in the record."""
        from airlock.advisor.audit import log_action

        log_action(
            "config_apply",
            "Applied change",
            "success",
            "local-llama",
            details={"diff": "+ new_line", "risk_level": "low"},
            log_dir=tmp_path,
        )
        record = json.loads((tmp_path / "advisor-audit.jsonl").read_text().strip())
        assert record["details"]["diff"] == "+ new_line"
        assert record["details"]["risk_level"] == "low"

    def test_omits_details_when_none(self, tmp_path):
        """details key is absent when not provided."""
        from airlock.advisor.audit import log_action

        log_action("query", "test", "success", "model", log_dir=tmp_path)
        record = json.loads((tmp_path / "advisor-audit.jsonl").read_text().strip())
        assert "details" not in record

    def test_uses_env_log_dir_by_default(self, tmp_path, monkeypatch):
        """Falls back to AIRLOCK_LOG_DIR env var when log_dir not passed."""
        from airlock.advisor.audit import log_action

        log_path = tmp_path / "envlogs"
        log_path.mkdir()
        monkeypatch.setenv("AIRLOCK_LOG_DIR", str(log_path))
        log_action("query", "test", "success", "model")
        assert (log_path / "advisor-audit.jsonl").exists()
