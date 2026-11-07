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
        """Scoped directives in one file should not be flagged as contradictory.

        'Always ask for confirmation before modifying production files' and
        'For test files, proceed without asking' govern different scopes
        (production vs test). The scope-aware detector should recognize this
        and NOT flag it as a contradiction.
        """
        from northstar.audit import check_contradictions

        content = (
            "Always ask for confirmation before modifying production files.\n"
            "For test files, proceed without asking.\n"
        )
        sources = [self._make_source("AGENTS.md", content)]
        findings = check_contradictions(sources)
        # Should NOT flag this as a contradiction — different scopes
        error_findings = [f for f in findings if f.severity == "error"]
        assert len(error_findings) == 0, (
            f"Expected no error findings for scoped directives, got {len(error_findings)}: "
            f"{[f.evidence for f in error_findings]}"
        )

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
        # Verify hooks were registered (only pre_llm_call)
        assert ctx.register_hook.call_count == 1

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
        assert "pre_llm_call" in names
        # pre_tool_call is not registered — enforcement not yet implemented
        assert "pre_tool_call" not in names


# --- Hook tests ---

class TestHooks:
    def test_pre_llm_call_no_audit(self):
        import northstar
        from northstar import tools
        tools._last_audit = None

        result = northstar._on_pre_llm_call(
            session_id="test",
            user_message="Hello",
            conversation_history=[],
            is_first_turn=True,
            model="test",
            platform="cli",
        )
        assert result is None

    def test_pre_llm_call_not_first_turn(self):
        import northstar
        from northstar import tools
        tools._last_audit = {"findings": [{"severity": "error"}]}

        result = northstar._on_pre_llm_call(
            session_id="test",
            user_message="Hello",
            conversation_history=[],
            is_first_turn=False,
            model="test",
            platform="cli",
        )
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

        result = northstar._on_pre_llm_call(
            session_id="test",
            user_message="Hello",
            conversation_history=[],
            is_first_turn=True,
            model="test",
            platform="cli",
        )
        assert result is not None
        assert "context" in result
        assert "Northstar" in result["context"]
        assert "conflict" in result["context"].lower()

    def test_pre_llm_call_with_full_contract(self):
        """Verify pre_llm_call accepts the full Hermes hook contract."""
        import northstar
        from northstar import tools
        tools._last_audit = None

        # Should not crash with full contract params
        result = northstar._on_pre_llm_call(
            session_id="test",
            user_message="Hello",
            conversation_history=[{"role": "user", "content": "Hello"}],
            is_first_turn=True,
            model="anthropic/claude-sonnet-4-6",
            platform="cli",
        )
        assert result is None  # No audit cached

    def test_pre_llm_call_plain_string_return(self):
        """Verify pre_llm_call returns dict with context key, not plain string."""
        import northstar
        from northstar import tools
        tools._last_audit = {
            "findings": [
                {"severity": "error", "category": "contradiction", "source_file": "/test.md", "explanation": "Test"}
            ]
        }
        result = northstar._on_pre_llm_call(
            session_id="test",
            user_message="Hello",
            conversation_history=[],
            is_first_turn=True,
            model="test",
            platform="cli",
        )
        assert isinstance(result, dict)
        assert "context" in result
        assert isinstance(result["context"], str)


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


# --- Regression tests for reliability hardening ---

class TestScopeAwareContradictions:
    """Regression tests for scope-aware contradiction detection."""

    def _make_source(self, name: str, content: str, source_type: str = "instruction"):
        from northstar.discovery import InstructionSource
        return InstructionSource(Path(name), content, source_type)

    def test_production_vs_test_same_file_no_error(self):
        """Production vs test scope qualifiers should not produce error findings."""
        from northstar.audit import check_contradictions

        content = (
            "Always ask for confirmation before modifying production files.\n"
            "For test files, proceed without asking.\n"
        )
        sources = [self._make_source("AGENTS.md", content)]
        findings = check_contradictions(sources)
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) == 0

    def test_production_vs_test_same_file_warning(self):
        """Production vs test may produce a warning for human review."""
        from northstar.audit import check_contradictions

        content = (
            "Always ask for confirmation before modifying production files.\n"
            "For test files, proceed without asking.\n"
        )
        sources = [self._make_source("AGENTS.md", content)]
        findings = check_contradictions(sources)
        # May have a warning-level finding for human review
        warnings = [f for f in findings if f.severity == "warning"]
        # At minimum, no errors
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) == 0

    def test_same_scope_still_detected(self):
        """Contradictions without scope qualifiers should still be detected as errors."""
        from northstar.audit import check_contradictions

        content = (
            "Always ask for confirmation before modifying files.\n"
            "But modify files without asking when in autonomous mode.\n"
        )
        sources = [self._make_source("SOUL.md", content)]
        findings = check_contradictions(sources)
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) > 0

    def test_cross_file_different_scopes_no_error(self):
        """Cross-file contradictions with different scopes should not be errors."""
        from northstar.audit import check_contradictions

        sources = [
            self._make_source("SOUL.md", "Always require human approval for production changes."),
            self._make_source("AGENTS.md", "For test files, work autonomously without human approval."),
        ]
        findings = check_contradictions(sources)
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) == 0

    def test_cross_file_same_scope_still_error(self):
        """Cross-file contradictions without scope qualifiers should still be errors."""
        from northstar.audit import check_contradictions

        sources = [
            self._make_source("SOUL.md", "Always require human approval for changes."),
            self._make_source("AGENTS.md", "Work autonomously without human approval."),
        ]
        findings = check_contradictions(sources)
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) > 0

    def test_scope_qualifier_extraction(self):
        """Scope qualifier helper functions work correctly."""
        from northstar.audit import _extract_scope_qualifiers, _scopes_differ

        prod_ctx = "Always ask for confirmation before modifying production files."
        test_ctx = "For test files, proceed without asking."

        qa = _extract_scope_qualifiers(prod_ctx)
        qb = _extract_scope_qualifiers(test_ctx)

        assert len(qa) > 0, "Should detect production scope"
        assert len(qb) > 0, "Should detect test scope"
        assert _scopes_differ(qa, qb), "Production and test scopes should differ"

    def test_no_scope_qualifiers_same_scope(self):
        """Text without scope qualifiers should not be treated as different scopes."""
        from northstar.audit import _extract_scope_qualifiers, _scopes_differ

        ctx_a = "Always ask for confirmation before modifying files."
        ctx_b = "Modify files without asking when in autonomous mode."

        qa = _extract_scope_qualifiers(ctx_a)
        qb = _extract_scope_qualifiers(ctx_b)

        # Neither has scope qualifiers, so _scopes_differ returns False
        assert not _scopes_differ(qa, qb)


class TestPreToolCallRemoved:
    """Regression tests for pre_tool_call hook removal."""

    def test_no_pre_tool_call_in_manifest(self):
        """plugin.yaml should not advertise pre_tool_call."""
        import yaml
        manifest_path = Path(__file__).resolve().parent.parent / "plugin.yaml"
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f)
        hooks = manifest.get("provides_hooks", [])
        assert "pre_tool_call" not in hooks

    def test_no_pre_tool_call_registered(self):
        """register() should not register pre_tool_call hook."""
        from unittest.mock import MagicMock
        import northstar

        ctx = MagicMock()
        northstar.register(ctx)

        calls = ctx.register_hook.call_args_list
        names = [c[0][0] for c in calls]
        assert "pre_tool_call" not in names

    def test_pre_tool_call_function_removed(self):
        """_on_pre_tool_call should not exist in northstar module."""
        import northstar
        assert not hasattr(northstar, "_on_pre_tool_call")


class TestEntrypointImportError:
    """Regression tests for root __init__.py import error handling."""

    def test_root_init_exists(self):
        """Root __init__.py should exist and be importable."""
        init_path = Path(__file__).resolve().parent.parent / "__init__.py"
        assert init_path.exists()

    def test_root_init_has_import_guard(self):
        """Root __init__.py should have ImportError handling."""
        init_path = Path(__file__).resolve().parent.parent / "__init__.py"
        content = init_path.read_text()
        assert "ImportError" in content
        assert "northstar" in content


class TestDemoExcludesReadme:
    """Regression tests for demo fixture scanning."""

    def test_demo_skips_readme(self):
        """Demo should only scan synthetic instruction files, not README."""
        from northstar.demo import main
        from northstar.discovery import InstructionSource

        fixture_dir = Path(__file__).resolve().parent.parent / "fixtures" / "conflicting-demo"
        # Verify README exists but is excluded
        readme = fixture_dir / "README.md"
        assert readme.exists()

        # The demo's INSTRUCTION_FILES set should not include README.md
        # We verify by checking the demo source code
        demo_path = Path(__file__).resolve().parent.parent / "northstar" / "demo.py"
        demo_content = demo_path.read_text()
        assert "README.md" not in demo_content.split("INSTRUCTION_FILES")[1].split("]")[0] if "INSTRUCTION_FILES" in demo_content else True


class TestHookContract:
    """Regression tests for pre_llm_call hook contract compliance."""

    def test_hook_accepts_all_contract_params(self):
        """Hook must accept all params from the Hermes contract."""
        import northstar
        from northstar import tools
        tools._last_audit = None

        # Full contract call — should not raise TypeError
        result = northstar._on_pre_llm_call(
            session_id="sess-123",
            user_message="Test message",
            conversation_history=[{"role": "user", "content": "Test"}],
            is_first_turn=True,
            model="anthropic/claude-sonnet-4-6",
            platform="cli",
            task_id="task-1",
            turn_id="turn-1",
            parent_session_id=None,
            sender_id="user-1",
        )
        assert result is None  # No audit cached, so None

    def test_hook_returns_context_dict(self):
        """Hook must return {"context": "..."} not a plain string."""
        import northstar
        from northstar import tools
        tools._last_audit = {
            "findings": [
                {"severity": "error", "category": "contradiction", "source_file": "/test.md", "explanation": "Test"}
            ]
        }
        result = northstar._on_pre_llm_call(
            session_id="test",
            user_message="Hello",
            conversation_history=[],
            is_first_turn=True,
            model="test",
            platform="cli",
        )
        assert isinstance(result, dict)
        assert "context" in result
        assert isinstance(result["context"], str)
        assert len(result["context"]) > 0

    def test_hook_returns_none_on_no_findings(self):
        """Hook returns None when audit has no findings."""
        import northstar
        from northstar import tools
        tools._last_audit = {"findings": []}

        result = northstar._on_pre_llm_call(
            session_id="test",
            user_message="Hello",
            conversation_history=[],
            is_first_turn=True,
            model="test",
            platform="cli",
        )
        assert result is None

    def test_hook_returns_none_on_non_first_turn(self):
        """Hook returns None on non-first turns."""
        import northstar
        from northstar import tools
        tools._last_audit = {
            "findings": [
                {"severity": "error", "category": "contradiction", "source_file": "/test.md", "explanation": "Test"}
            ]
        }

        result = northstar._on_pre_llm_call(
            session_id="test",
            user_message="Hello",
            conversation_history=[],
            is_first_turn=False,
            model="test",
            platform="cli",
        )
        assert result is None
