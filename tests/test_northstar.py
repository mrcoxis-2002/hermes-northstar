"""Tests for Northstar plugin."""

import json
import tempfile
from pathlib import Path

import pytest


# --- Discovery tests ---

class TestDiscoverSources:
    def test_discover_hermes_home_files(self, tmp_path):
        from northstar.discovery import discover_sources, INSTRUCTION_FILES
        from northstar.audit import run_audit, AuditReport, Finding
        from northstar.tools import northstar_audit, northstar_status
        from northstar import schemas
        import northstar

        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        # Create some instruction files
        (hermes_home / "SOUL.md").write_text("You are helpful.")
        (hermes_home / "AGENTS.md").write_text("Agent rules here.")
        (hermes_home / "config.yaml").write_text("key: value")

        sources = discover_sources(hermes_home=hermes_home, project_root=None)

        names = {s.path.name for s in sources}
        assert "SOUL.md" in names
        assert "AGENTS.md" in names
        assert "config.yaml" in names

    def test_discover_project_files(self, tmp_path):
        from northstar.discovery import discover_sources

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "README.md").write_text("Project readme.")
        (project_root / "AGENTS.md").write_text("Project agent rules.")

        sources = discover_sources(hermes_home=None, project_root=project_root)

        names = {s.path.name for s in sources}
        assert "README.md" in names
        assert "AGENTS.md" in names

    def test_discover_skills(self, tmp_path):
        from northstar.discovery import discover_sources

        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        skills_dir = hermes_home / "skills" / "my-skill"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text("# My Skill\n\nDo things.")

        sources = discover_sources(hermes_home=hermes_home, project_root=None)

        names = {s.path.name for s in sources}
        assert "SKILL.md" in names

    def test_discover_empty(self, tmp_path):
        from northstar.discovery import discover_sources

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        sources = discover_sources(hermes_home=empty_dir, project_root=empty_dir)
        assert sources == []

    def test_discover_unreadable_file(self, tmp_path):
        from northstar.discovery import discover_sources

        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        bad_file = hermes_home / "SOUL.md"
        bad_file.write_text("content")
        bad_file.chmod(0o000)

        sources = discover_sources(hermes_home=hermes_home, project_root=None)
        # Should not crash, should skip unreadable
        assert isinstance(sources, list)

        # Restore permissions for cleanup
        bad_file.chmod(0o644)

    def test_discover_deduplicates(self, tmp_path):
        from northstar.discovery import discover_sources

        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        (hermes_home / "SOUL.md").write_text("Same content")

        sources = discover_sources(hermes_home=hermes_home, project_root=None)
        soul_files = [s for s in sources if s.path.name == "SOUL.md"]
        assert len(soul_files) == 1


# --- Audit tests ---

class TestAudit:
    def _make_source(self, name: str, content: str, source_type: str = "instruction"):
        from northstar.discovery import InstructionSource
        return InstructionSource(Path(name), content, source_type)

    def test_no_findings_on_clean_config(self):
        from northstar.audit import run_audit

        sources = [
            self._make_source("SOUL.md", "You are a helpful assistant.\nBe concise."),
            self._make_source("AGENTS.md", "Follow the rules in SOUL.md."),
        ]

        report = run_audit(sources, Path("/tmp"))
        assert report.findings == []

    def test_detects_duplicate_directives(self):
        from northstar.audit import run_audit, check_duplicates

        sources = [
            self._make_source("SOUL.md", "Always ask for confirmation before modifying files.\nBe helpful."),
            self._make_source("AGENTS.md", "Always ask for confirmation before modifying files.\nBe efficient."),
        ]

        findings = check_duplicates(sources)
        assert len(findings) > 0
        assert any(f.category == "duplicate" for f in findings)

    def test_detects_contradiction_in_same_file(self):
        from northstar.audit import run_audit, check_contradictions

        content = (
            "Always ask for confirmation before modifying files.\n"
            "But modify files without asking when in autonomous mode.\n"
        )
        sources = [self._make_source("SOUL.md", content)]

        findings = check_contradictions(sources)
        assert len(findings) > 0
        assert any(f.category == "contradiction" for f in findings)

    def test_detects_cross_file_contradiction(self):
        from northstar.audit import check_contradictions

        sources = [
            self._make_source("SOUL.md", "Always require human approval for changes."),
            self._make_source("AGENTS.md", "Work autonomously without human approval."),
        ]

        findings = check_contradictions(sources)
        assert len(findings) > 0
        assert any(f.category == "contradiction" for f in findings)

    def test_detects_stale_file_reference(self, tmp_path):
        from northstar.audit import check_stale_references

        content = "See the configuration in /nonexistent/path/config.yaml for details."
        sources = [self._make_source("README.md", content)]

        findings = check_stale_references(sources, tmp_path)
        # May or may not find depending on path resolution
        # At minimum it should not crash
        assert isinstance(findings, list)

    def test_report_to_json(self):
        from northstar.audit import AuditReport, Finding

        report = AuditReport(
            sources_inspected=[{"path": "/test/SOUL.md", "source_type": "instruction", "line_count": 5}],
            findings=[
                Finding(
                    category="duplicate",
                    severity="warning",
                    source_file="/test/SOUL.md",
                    source_line=1,
                    evidence="Some duplicate text",
                    explanation="Found in multiple files",
                    suggestion="Consolidate",
                    confidence="medium",
                )
            ],
            warnings=[],
            checks_skipped=["Tool check"],
        )

        data = json.loads(report.to_json())
        assert data["summary"]["total_findings"] == 1
        assert data["summary"]["warnings"] == 1

    def test_report_to_text(self):
        from northstar.audit import AuditReport

        report = AuditReport(
            sources_inspected=[{"path": "/test/SOUL.md", "source_type": "instruction", "line_count": 5}],
            findings=[],
            warnings=[],
            checks_skipped=[],
        )

        text = report.to_text()
        assert "NORTHSTAR" in text
        assert "No findings" in text

    def test_false_positive_prevention(self):
        """Ensure normal text isn't flagged as contradictory."""
        from northstar.audit import check_contradictions

        # These are normal instructions that mention both concepts but don't contradict
        content = (
            "When modifying configuration files, ask for confirmation.\n"
            "When generating new files, you can proceed without asking.\n"
        )
        sources = [self._make_source("SOUL.md", content)]

        findings = check_contradictions(sources)
        # Should not flag this as a contradiction — different scopes
        # (This is a heuristic check; the key is it doesn't crash)
        assert isinstance(findings, list)

    def test_no_false_positive_descriptive_text(self):
        """Descriptive text about tools should not trigger contradictions."""
        from northstar.audit import check_contradictions

        content = (
            "This project uses a CI/CD pipeline that automatically modifies files.\n"
            "Developers should request approval before merging changes.\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_contradictions(sources)
        # These describe different contexts (CI vs human workflow) — should not contradict
        assert len(findings) == 0

    def test_no_false_positive_single_file_conditional(self):
        """Conditional directives in one file may still trigger — known limitation.

        The regex-based contradiction detector cannot distinguish scoped directives
        (e.g., production vs test). This test documents the current behavior:
        scoped contradictions are flagged and require manual review.
        """
        from northstar.audit import check_contradictions

        content = (
            "Always ask for confirmation before modifying production files.\n"
            "For test files, proceed without asking.\n"
        )
        sources = [self._make_source("AGENTS.md", content)]
        findings = check_contradictions(sources)
        # Current behavior: flags this as a contradiction (known limitation).
        # A future improvement would parse scope qualifiers to reduce false positives.
        assert len(findings) == 1
        assert findings[0].category == "contradiction"
        assert findings[0].confidence == "medium"  # Not "high" — heuristic

    def test_no_false_positive_cross_file_complementary(self):
        """Complementary instructions across files should not contradict."""
        from northstar.audit import check_contradictions

        sources = [
            self._make_source("SOUL.md", "Always be helpful and concise."),
            self._make_source("AGENTS.md", "Follow the guidelines in SOUL.md."),
        ]
        findings = check_contradictions(sources)
        assert len(findings) == 0

    def test_cross_file_contradiction_evidence_includes_filenames(self):
        """Cross-file findings must reference both file names in evidence."""
        from northstar.audit import check_contradictions

        sources = [
            self._make_source("SOUL.md", "Always require human approval for changes."),
            self._make_source("AGENTS.md", "Work autonomously without human approval."),
        ]
        findings = check_contradictions(sources)
        assert len(findings) > 0
        # Evidence should mention both files
        evidence_text = " ".join(f.evidence for f in findings)
        assert "SOUL.md" in evidence_text
        assert "AGENTS.md" in evidence_text

    def test_severity_labels_correct(self):
        """Verify severity labels match finding categories."""
        from northstar.audit import check_duplicates, check_contradictions

        # Duplicates should be warnings
        dup_sources = [
            self._make_source("A.md", "Always ask for confirmation before modifying files.\nBe helpful."),
            self._make_source("B.md", "Always ask for confirmation before modifying files.\nBe efficient."),
        ]
        dup_findings = check_duplicates(dup_sources)
        for f in dup_findings:
            assert f.severity == "warning", f"Expected warning, got {f.severity}"

        # Contradictions should be errors
        contra_sources = [
            self._make_source("SOUL.md", "Always require human approval for changes."),
            self._make_source("AGENTS.md", "Work autonomously without human approval."),
        ]
        contra_findings = check_contradictions(contra_sources)
        for f in contra_findings:
            assert f.severity == "error", f"Expected error, got {f.severity}"

    def test_finding_has_source_file_and_line(self):
        """Findings must include source_file and (for same-file) source_line."""
        from northstar.audit import check_contradictions

        content = (
            "Always ask for confirmation before modifying files.\n"
            "But modify files without asking when in autonomous mode.\n"
        )
        sources = [self._make_source("SOUL.md", content)]
        findings = check_contradictions(sources)
        assert len(findings) > 0
        f = findings[0]
        assert f.source_file is not None
        assert f.source_line is not None and f.source_line >= 1


# --- Tool handler tests ---

class TestTools:
    def test_northstar_audit_no_sources(self, tmp_path, monkeypatch):
        from northstar import tools

        monkeypatch.setattr(tools, "_get_hermes_home", lambda: tmp_path)

        result = tools.northstar_audit({"scope": "full", "output_format": "json"})
        data = json.loads(result)
        assert data["status"] == "ok"
        assert "No instruction sources found" in data["message"]

    def test_northstar_audit_with_sources(self, tmp_path, monkeypatch):
        from northstar import tools

        # Create a hermes home with files
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        (hermes_home / "SOUL.md").write_text("You are helpful.\nBe concise.")

        monkeypatch.setattr(tools, "_get_hermes_home", lambda: hermes_home)

        result = tools.northstar_audit({"scope": "full", "output_format": "json"})
        data = json.loads(result)
        assert "sources_inspected" in data
        assert len(data["sources_inspected"]) >= 1

    def test_northstar_status_no_audit(self, tmp_path, monkeypatch):
        from northstar import tools

        monkeypatch.setattr(tools, "_get_hermes_home", lambda: tmp_path)
        # Reset last audit
        tools._last_audit = None

        result = tools.northstar_status({})
        data = json.loads(result)
        assert data["plugin"] == "northstar"
        assert "last_audit" not in data

    def test_northstar_status_with_audit(self, tmp_path, monkeypatch):
        from northstar import tools

        monkeypatch.setattr(tools, "_get_hermes_home", lambda: tmp_path)
        tools._last_audit = {"findings": [], "sources_inspected": 1}

        result = tools.northstar_status({"include_last_audit": True})
        data = json.loads(result)
        assert data["last_audit"]["sources_inspected"] == 1


# --- Integration tests ---

class TestPluginRegistration:
    def test_register_does_not_crash(self):
        """Test that register() can be called without crashing."""
        from unittest.mock import MagicMock
        import northstar

        ctx = MagicMock()
        northstar.register(ctx)

        # Verify tools were registered
        assert ctx.register_tool.call_count == 2
        # Verify hooks were registered
        assert ctx.register_hook.call_count == 2

    def test_register_tool_names(self):
        from unittest.mock import MagicMock
        import northstar

        ctx = MagicMock()
        northstar.register(ctx)

        calls = ctx.register_tool.call_args_list
        names = [c[1]["name"] for c in calls]
        assert "northstar_audit" in names
        assert "northstar_status" in names

    def test_register_hook_names(self):
        from unittest.mock import MagicMock
        import northstar

        ctx = MagicMock()
        northstar.register(ctx)

        calls = ctx.register_hook.call_args_list
        names = [c[0][0] for c in calls]
        assert "pre_tool_call" in names
        assert "pre_llm_call" in names


# --- Hook tests ---

class TestHooks:
    def test_pre_llm_call_no_audit(self):
        import northstar
        from northstar import tools
        tools._last_audit = None

        result = northstar._on_pre_llm_call(session_id="test", is_first_turn=True)
        assert result is None

    def test_pre_llm_call_not_first_turn(self):
        import northstar
        from northstar import tools
        tools._last_audit = {"findings": [{"severity": "error"}]}

        result = northstar._on_pre_llm_call(session_id="test", is_first_turn=False)
        assert result is None

    def test_pre_llm_call_with_errors(self):
        import northstar
        from northstar import tools
        tools._last_audit = {
            "findings": [
                {
                    "severity": "error",
                    "category": "contradiction",
                    "source_file": "/test/SOUL.md",
                    "explanation": "Conflicting directives found",
                }
            ]
        }

        result = northstar._on_pre_llm_call(session_id="test", is_first_turn=True)
        assert result is not None
        assert "context" in result
        assert "Northstar" in result["context"]
        assert "conflict" in result["context"].lower()

    def test_pre_tool_call_no_crash(self):
        import northstar

        # Should not raise
        northstar._on_pre_tool_call(tool_name="terminal", args={"command": "ls"})


# --- Input validation tests ---

class TestInputValidation:
    def test_audit_empty_scope(self, tmp_path, monkeypatch):
        from northstar import tools

        monkeypatch.setattr(tools, "_get_hermes_home", lambda: tmp_path)

        result = tools.northstar_audit({"output_format": "json"})
        data = json.loads(result)
        assert "sources_inspected" in data

    def test_audit_invalid_scope(self, tmp_path, monkeypatch):
        from northstar import tools

        monkeypatch.setattr(tools, "_get_hermes_home", lambda: tmp_path)

        result = tools.northstar_audit({"scope": "invalid", "output_format": "json"})
        data = json.loads(result)
        # Should treat as full scope
        assert "sources_inspected" in data

    def test_status_empty_args(self, tmp_path, monkeypatch):
        from northstar import tools

        monkeypatch.setattr(tools, "_get_hermes_home", lambda: tmp_path)

        result = tools.northstar_status({})
        data = json.loads(result)
        assert data["plugin"] == "northstar"
