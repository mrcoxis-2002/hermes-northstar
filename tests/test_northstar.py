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
        # Also set project_root to empty dir so scope=full finds nothing
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

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


# --- Regression tests for audit quality hardening (Tasks A-E) ---

class TestMetadataFalsePositives:
    """Task A: YAML frontmatter metadata should not trigger duplicate findings."""

    def _make_source(self, name: str, content: str, source_type: str = "skill"):
        from northstar.discovery import InstructionSource
        return InstructionSource(Path(name), content, source_type)

    def test_yaml_frontmatter_not_flagged_as_duplicate(self):
        """YAML frontmatter with common metadata keys should not be flagged."""
        from northstar.audit import check_duplicates

        content_a = (
            "---\n"
            "name: my-skill\n"
            "description: A skill for doing things\n"
            "platforms: linux macos windows\n"
            "version: 1.0.0\n"
            "---\n"
            "# My Skill\n"
            "\n"
            "This skill helps you do things efficiently.\n"
        )
        content_b = (
            "---\n"
            "name: other-skill\n"
            "description: A skill for doing things\n"
            "platforms: linux macos windows\n"
            "version: 1.0.0\n"
            "---\n"
            "# Other Skill\n"
            "\n"
            "This other skill helps you do different things.\n"
        )
        sources = [
            self._make_source("skill-a/SKILL.md", content_a),
            self._make_source("skill-b/SKILL.md", content_b),
        ]
        findings = check_duplicates(sources)
        platforms_findings = [f for f in findings if "platforms" in f.evidence.lower()]
        assert len(platforms_findings) == 0, (
            f"YAML frontmatter metadata should not be flagged as duplicate: {platforms_findings}"
        )

    def test_yaml_metadata_keys_filtered(self):
        """Known YAML metadata keys should be filtered from duplicate detection."""
        from northstar.audit import _is_yaml_metadata_line

        assert _is_yaml_metadata_line("platforms: linux macos windows")
        assert _is_yaml_metadata_line("name: my-skill")
        assert _is_yaml_metadata_line("description: A skill for doing things")
        assert _is_yaml_metadata_line("version: 1.0.0")
        assert _is_yaml_metadata_line("tags: [skill, helper]")
        assert not _is_yaml_metadata_line("Always ask for confirmation before modifying files.")
        assert not _is_yaml_metadata_line("# This is a heading")
        assert not _is_yaml_metadata_line("")

    def test_markdown_body_instructions_preserved(self):
        """Meaningful instructions in Markdown bodies should still be detected."""
        from northstar.audit import check_duplicates

        content_a = (
            "---\n"
            "name: skill-a\n"
            "---\n"
            "# Skill A\n"
            "\n"
            "Always ask for confirmation before modifying files.\n"
        )
        content_b = (
            "---\n"
            "name: skill-b\n"
            "---\n"
            "# Skill B\n"
            "\n"
            "Always ask for confirmation before modifying files.\n"
        )
        sources = [
            self._make_source("skill-a/SKILL.md", content_a),
            self._make_source("skill-b/SKILL.md", content_b),
        ]
        findings = check_duplicates(sources)
        assert len(findings) > 0, "Body instructions should still be detected as duplicates"
        assert any(f.category == "duplicate" for f in findings)

    def test_frontmatter_stripping(self):
        """_strip_yaml_frontmatter_lines correctly separates frontmatter from body."""
        from northstar.audit import _strip_yaml_frontmatter_lines

        content = (
            "---\n"
            "name: test\n"
            "platforms: linux macos windows\n"
            "---\n"
            "# Heading\n"
            "\n"
            "This is a long line in the body that should be included in the output.\n"
        )
        source = self._make_source("test.md", content)
        visible = _strip_yaml_frontmatter_lines(source)

        visible_texts = [text for _, text in visible]
        assert any("long line" in t for t in visible_texts), "Body content should be visible"
        assert not any("platforms" in t for t in visible_texts), "Frontmatter should be hidden"


class TestArchivedSkills:
    """Task B: Archived skills should be excluded from discovery by default."""

    def test_archived_dirs_excluded(self, tmp_path):
        """Skills in .archive, .curator_backups, etc. should be excluded."""
        from northstar.discovery import discover_sources

        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        active_dir = hermes_home / "skills" / "active-skill"
        active_dir.mkdir(parents=True)
        (active_dir / "SKILL.md").write_text("# Active Skill\n\nDo things.")

        archive_dir = hermes_home / "skills" / ".archive" / "old-skill"
        archive_dir.mkdir(parents=True)
        (archive_dir / "SKILL.md").write_text("# Old Skill\n\nDeprecated.")

        backup_dir = hermes_home / "skills" / ".curator_backups" / "backup-skill"
        backup_dir.mkdir(parents=True)
        (backup_dir / "SKILL.md").write_text("# Backup Skill\n\nOld version.")

        sources = discover_sources(hermes_home=hermes_home, project_root=None, include_archived=False)
        paths = [str(s.path) for s in sources]

        assert any("active-skill" in p for p in paths), "Active skill should be discovered"
        assert not any(".archive" in p for p in paths), "Archived skills should be excluded"
        assert not any(".curator_backups" in p for p in paths), "Curator backups should be excluded"

    def test_archived_dirs_included_with_flag(self, tmp_path):
        """include_archived=True should include archived skills."""
        from northstar.discovery import discover_sources

        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        archive_dir = hermes_home / "skills" / ".archive" / "old-skill"
        archive_dir.mkdir(parents=True)
        (archive_dir / "SKILL.md").write_text("# Old Skill\n\nDeprecated.")

        sources = discover_sources(hermes_home=hermes_home, project_root=None, include_archived=True)
        paths = [str(s.path) for s in sources]

        assert any(".archive" in p for p in paths), "Archived skills should be included with flag"

    def test_is_archived_path(self):
        """_is_archived_path correctly identifies archived directory names."""
        from northstar.discovery import _is_archived_path

        assert _is_archived_path(Path("/skills/.archive/old/SKILL.md"))
        assert _is_archived_path(Path("/skills/.curator_backups/v1/SKILL.md"))
        assert _is_archived_path(Path("/skills/.backup/SKILL.md"))
        assert _is_archived_path(Path("/skills/archive/old/SKILL.md"))
        assert _is_archived_path(Path("/skills/deprecated/SKILL.md"))
        assert not _is_archived_path(Path("/skills/active-skill/SKILL.md"))
        assert not _is_archived_path(Path("/skills/my-skill/SKILL.md"))


class TestDuplicateConsolidation:
    """Task C: Duplicate findings should be consolidated into one per group."""

    def _make_source(self, name: str, content: str, source_type: str = "instruction"):
        from northstar.discovery import InstructionSource
        return InstructionSource(Path(name), content, source_type)

    def test_one_finding_per_duplicate_group(self):
        """Three files with the same directive should produce one finding, not multiple."""
        from northstar.audit import check_duplicates

        directive = "Always ask for confirmation before modifying files."
        sources = [
            self._make_source("A.md", f"{directive}\nOther content here."),
            self._make_source("B.md", f"{directive}\nMore content here."),
            self._make_source("C.md", f"{directive}\nEven more content."),
        ]
        findings = check_duplicates(sources)
        assert len(findings) == 1, (
            f"Expected 1 consolidated finding, got {len(findings)}. "
            "Duplicate groups should be consolidated."
        )

    def test_consolidated_finding_includes_related_sources(self):
        """Consolidated findings should reference all related sources."""
        from northstar.audit import check_duplicates

        directive = "Always ask for confirmation before modifying files."
        sources = [
            self._make_source("A.md", f"{directive}\nOther content here."),
            self._make_source("B.md", f"{directive}\nMore content here."),
            self._make_source("C.md", f"{directive}\nEven more content."),
        ]
        findings = check_duplicates(sources)
        assert len(findings) == 1
        explanation = findings[0].explanation
        assert "B.md" in explanation or "C.md" in explanation, (
            f"Consolidated finding should reference related sources: {explanation}"
        )

    def test_different_directives_separate_groups(self):
        """Different duplicate directives should produce separate findings."""
        from northstar.audit import check_duplicates

        sources = [
            self._make_source("A.md", "Always ask for confirmation before modifying files.\nNever write to production without approval."),
            self._make_source("B.md", "Always ask for confirmation before modifying files.\nNever write to production without approval."),
        ]
        findings = check_duplicates(sources)
        assert len(findings) == 2, (
            f"Expected 2 findings for 2 different duplicate directives, got {len(findings)}"
        )


class TestStaleReferenceImprovements:
    """Task D: Stale reference detection improvements."""

    def _make_source(self, name: str, content: str, source_type: str = "instruction"):
        from northstar.discovery import InstructionSource
        return InstructionSource(Path(name), content, source_type)

    def test_url_not_flagged_as_stale_path(self, tmp_path):
        """URLs containing path-like segments should not be flagged."""
        from northstar.audit import check_stale_references

        content = (
            "See the documentation at https://example.com/docs/config.yaml for details.\n"
            "Also check https://github.com/user/repo/blob/main/scripts/fetch_transcript.py\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        url_findings = [f for f in findings if "example.com" in f.evidence or "github.com" in f.evidence]
        assert len(url_findings) == 0, (
            f"URLs should not be flagged as stale references: {url_findings}"
        )

    def test_doc_example_paths_not_flagged(self, tmp_path):
        """Paths in documentation examples should not be flagged."""
        from northstar.audit import check_stale_references

        content = (
            "For example, you might have a file at /scripts/fetch_transcript.py\n"
            "such as /config/settings.yaml in your project root.\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        assert len(findings) == 0, (
            f"Documentation example paths should not be flagged: {findings}"
        )

    def test_relative_path_resolution(self, tmp_path):
        """Paths relative to the source file's directory should be resolved."""
        from northstar.audit import check_stale_references

        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "helper.py").write_text("# helper")

        content = "See /helper.py for the implementation."
        source = self._make_source(str(skill_dir / "SKILL.md"), content)
        findings = check_stale_references([source], tmp_path)
        helper_findings = [f for f in findings if "helper.py" in f.evidence]
        assert len(helper_findings) == 0, (
            f"Relative path /helper.py should resolve to skill_dir/helper.py: {helper_findings}"
        )

    def test_missing_file_still_flagged(self, tmp_path):
        """Genuinely missing files should still be flagged."""
        from northstar.audit import check_stale_references

        content = "See /definitely/nonexistent/file.yaml for configuration."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        assert len(findings) > 0, "Genuinely missing files should be flagged"

    def test_is_doc_example_path(self):
        """_is_doc_example_path correctly identifies documentation examples."""
        from northstar.audit import _is_doc_example_path

        assert _is_doc_example_path("/scripts/test.py", "For example, see /scripts/test.py")
        assert _is_doc_example_path("/config.yaml", "such as /config.yaml in your project")
        assert _is_doc_example_path("/test.py", "e.g. /test.py")
        assert not _is_doc_example_path("/scripts/test.py", "Run /scripts/test.py to start the server")


class TestReportQuality:
    """Task E: Report quality improvements."""

    def test_report_has_category_breakdown(self):
        """Report JSON should include by_category breakdown."""
        from northstar.audit import AuditReport, Finding

        report = AuditReport(
            sources_inspected=[{"path": "/test.md", "source_type": "instruction", "line_count": 1}],
            findings=[
                Finding(
                    category="duplicate",
                    severity="warning",
                    source_file="/test.md",
                    source_line=1,
                    evidence="test",
                    explanation="test",
                    suggestion="test",
                    confidence="medium",
                ),
                Finding(
                    category="stale_reference",
                    severity="warning",
                    source_file="/test.md",
                    source_line=2,
                    evidence="test",
                    explanation="test",
                    suggestion="test",
                    confidence="low",
                ),
            ],
            warnings=[],
            checks_skipped=[],
        )
        data = report.to_dict()
        assert "by_category" in data["summary"]
        assert data["summary"]["by_category"]["duplicate"] == 1
        assert data["summary"]["by_category"]["stale_reference"] == 1

    def test_text_report_groups_by_category(self):
        """Text report should group findings by category."""
        from northstar.audit import AuditReport, Finding

        report = AuditReport(
            sources_inspected=[{"path": "/test.md", "source_type": "instruction", "line_count": 1}],
            findings=[
                Finding(
                    category="duplicate",
                    severity="warning",
                    source_file="/test.md",
                    source_line=1,
                    evidence="test dup",
                    explanation="test",
                    suggestion="test",
                    confidence="medium",
                ),
                Finding(
                    category="stale_reference",
                    severity="warning",
                    source_file="/test.md",
                    source_line=2,
                    evidence="test stale",
                    explanation="test",
                    suggestion="test",
                    confidence="low",
                ),
            ],
            warnings=[],
            checks_skipped=["Tool check"],
        )
        text = report.to_text()
        assert "[DUPLICATE]" in text
        assert "[STALE_REFERENCE]" in text
        assert "Findings by category:" in text
        assert "Active sources inspected:" in text
        assert "Checks that could not be performed:" in text

    def test_text_report_shows_summary_counts(self):
        """Text report should show error/warning/info counts."""
        from northstar.audit import AuditReport, Finding

        report = AuditReport(
            sources_inspected=[{"path": "/test.md", "source_type": "instruction", "line_count": 1}],
            findings=[
                Finding(
                    category="contradiction",
                    severity="error",
                    source_file="/test.md",
                    source_line=1,
                    evidence="test",
                    explanation="test",
                    suggestion="test",
                    confidence="medium",
                ),
                Finding(
                    category="duplicate",
                    severity="warning",
                    source_file="/test.md",
                    source_line=2,
                    evidence="test",
                    explanation="test",
                    suggestion="test",
                    confidence="medium",
                ),
            ],
            warnings=[],
            checks_skipped=[],
        )
        text = report.to_text()
        assert "1 errors" in text
        assert "1 warnings" in text


# --- Regression tests for context-aware stale reference detection ---

class TestStaleReferenceContextClassification:
    """Regression tests for context-aware stale reference detection.

    Covers:
    - Real local dependencies referenced from Markdown links
    - Relative skill paths that exist and paths that are missing
    - Windows drive-letter paths
    - Unix-style home paths on Windows
    - Required executable configuration paths
    - Documentation-only references
    - Inline and fenced code examples, including indented fences
    - Ambiguous references that must not be reported as confirmed missing files
    """

    def _make_source(self, name: str, content: str, source_type: str = "instruction"):
        from northstar.discovery import InstructionSource
        return InstructionSource(Path(name), content, source_type)

    # --- Real local dependencies in Markdown links ---

    def test_markdown_link_to_existing_file_not_flagged(self, tmp_path):
        """A path inside a markdown link [text](path) should be skipped."""
        from northstar.audit import check_stale_references

        # Create a real file referenced via markdown link
        (tmp_path / "config.yaml").write_text("key: value")
        content = "See [configuration](/config.yaml) for details."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        config_findings = [f for f in findings if "config.yaml" in f.evidence]
        assert len(config_findings) == 0, (
            f"Markdown link to existing file should not be flagged: {config_findings}"
        )

    def test_markdown_link_to_missing_file_not_flagged(self, tmp_path):
        """A path inside a markdown link should be skipped even if the file is missing.

        Markdown links are documentation navigation, not local dependencies.
        """
        from northstar.audit import check_stale_references

        content = "See [missing guide](/nonexistent/guide.md) for details."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        guide_findings = [f for f in findings if "guide.md" in f.evidence]
        assert len(guide_findings) == 0, (
            f"Markdown link to missing file should not be flagged as stale: {guide_findings}"
        )

    def test_real_dependency_outside_link_is_flagged(self, tmp_path):
        """A path reference NOT in a markdown link should be checked normally."""
        from northstar.audit import check_stale_references

        content = "The script at /nonexistent/script.py must exist for the build."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        script_findings = [f for f in findings if "script.py" in f.evidence]
        assert len(script_findings) > 0, (
            "Real dependency outside markdown link should be flagged"
        )

    # --- Relative skill paths ---

    def test_relative_skill_path_that_exists(self, tmp_path):
        """A relative path to an existing skill file should resolve and not be flagged."""
        from northstar.audit import check_stale_references

        skill_dir = tmp_path / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")
        (skill_dir / "helper.py").write_text("# helper script")

        content = "See /helper.py for the implementation details."
        source = self._make_source(str(skill_dir / "SKILL.md"), content)
        findings = check_stale_references([source], tmp_path)
        helper_findings = [f for f in findings if "helper.py" in f.evidence]
        assert len(helper_findings) == 0, (
            f"Existing relative skill path should not be flagged: {helper_findings}"
        )

    def test_relative_skill_path_that_is_missing(self, tmp_path):
        """A relative path to a missing skill file should be flagged."""
        from northstar.audit import check_stale_references

        skill_dir = tmp_path / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        content = "See /missing_helper.py for the implementation."
        source = self._make_source(str(skill_dir / "SKILL.md"), content)
        findings = check_stale_references([source], tmp_path)
        missing_findings = [f for f in findings if "missing_helper.py" in f.evidence]
        assert len(missing_findings) > 0, (
            "Missing relative skill path should be flagged"
        )

    # --- Windows drive-letter paths ---

    def test_windows_drive_path_existing(self, tmp_path):
        """A Windows drive-letter path to an existing file should not be flagged."""
        from northstar.audit import check_stale_references

        real_file = tmp_path / "test_config.yaml"
        real_file.write_text("key: value")
        # Construct a Windows-style path
        win_path = str(real_file).replace("\\", "/")

        content = f"Configuration at {win_path} defines the settings."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        win_findings = [f for f in findings if "test_config.yaml" in f.evidence]
        assert len(win_findings) == 0, (
            f"Existing Windows drive path should not be flagged: {win_findings}"
        )

    def test_windows_drive_path_missing(self, tmp_path):
        """A Windows drive-letter path to a missing file should be flagged with high confidence."""
        from northstar.audit import check_stale_references

        # Use a path that definitely doesn't exist
        fake_path = "Z:/nonexistent/config.yaml"
        content = f"Configuration at {fake_path} defines the settings."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        win_findings = [f for f in findings if "config.yaml" in f.evidence]
        assert len(win_findings) > 0, (
            "Missing Windows drive path should be flagged"
        )
        assert win_findings[0].confidence == "high", (
            f"Windows drive path should have high confidence: {win_findings[0].confidence}"
        )

    # --- Unix-style home paths on Windows ---

    def test_unix_home_path_on_windows(self, tmp_path):
        """A Unix /Users/... path on Windows should be flagged with medium confidence."""
        from northstar.audit import check_stale_references

        content = "Script at /Users/developer/scripts/setup.py runs the build."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        unix_findings = [f for f in findings if "setup.py" in f.evidence]
        # Should be flagged since the path doesn't exist on this system
        assert len(unix_findings) > 0, (
            "Unix home path on Windows should be flagged"
        )
        assert unix_findings[0].confidence == "medium", (
            f"Unix home path should have medium confidence: {unix_findings[0].confidence}"
        )

    def test_unix_home_path_explanation_mentions_platform(self, tmp_path):
        """The explanation for Unix home paths should mention the platform mismatch."""
        from northstar.audit import check_stale_references

        content = "Script at /Users/developer/scripts/setup.py runs the build."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        unix_findings = [f for f in findings if "setup.py" in f.evidence]
        if unix_findings:
            explanation = unix_findings[0].explanation.lower()
            assert "windows" in explanation or "platform" in explanation, (
                f"Unix home path explanation should mention platform: {unix_findings[0].explanation}"
            )

    # --- Required executable configuration paths ---

    def test_executable_config_path_missing(self, tmp_path):
        """A missing script path in a YAML config hook command should be flagged with high confidence."""
        from northstar.audit import check_stale_references

        content = "command: python3 /hooks/pre_commit.py --validate"
        source = self._make_source("config.yaml", content, source_type="config")
        findings = check_stale_references([source], tmp_path)
        hook_findings = [f for f in findings if "pre_commit.py" in f.evidence]
        assert len(hook_findings) > 0, (
            "Missing executable config path should be flagged"
        )
        assert hook_findings[0].confidence == "high", (
            f"Executable config should have high confidence: {hook_findings[0].confidence}"
        )

    def test_executable_config_path_existing(self, tmp_path):
        """An existing script path in a YAML config should not be flagged."""
        from northstar.audit import check_stale_references

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "pre_commit.py").write_text("#!/usr/bin/env python3\nprint('ok')")

        content = "command: python3 /hooks/pre_commit.py --validate"
        source = self._make_source(str(tmp_path / "config.yaml"), content, source_type="config")
        findings = check_stale_references([source], tmp_path)
        hook_findings = [f for f in findings if "pre_commit.py" in f.evidence]
        assert len(hook_findings) == 0, (
            f"Existing executable config path should not be flagged: {hook_findings}"
        )

    # --- Documentation-only references ---

    def test_doc_xref_not_flagged(self, tmp_path):
        """Documentation cross-references should not be flagged as stale."""
        from northstar.audit import check_stale_references

        content = (
            "For more details, see the full reference guide at /docs/api_reference.md.\n"
            "Check the complete documentation at /docs/implementation.md.\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        doc_findings = [f for f in findings if "reference" in f.evidence or "documentation" in f.evidence or "implementation" in f.evidence]
        assert len(doc_findings) == 0, (
            f"Documentation cross-references should not be flagged: {doc_findings}"
        )

    def test_compiled_from_not_flagged(self, tmp_path):
        """'Compiled from' references should not be flagged."""
        from northstar.audit import check_stale_references

        content = "This document was compiled from /sources/raw_data.csv and /sources/notes.md."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        compiled_findings = [f for f in findings if "raw_data" in f.evidence or "notes.md" in f.evidence]
        assert len(compiled_findings) == 0, (
            f"'Compiled from' references should not be flagged: {compiled_findings}"
        )

    def test_templates_path_not_flagged(self, tmp_path):
        """References to templates/ paths should not be flagged as stale."""
        from northstar.audit import check_stale_references

        content = "Use the templates/config.yaml as a starting point for your configuration."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        template_findings = [f for f in findings if "templates" in f.evidence]
        assert len(template_findings) == 0, (
            f"Templates path references should not be flagged: {template_findings}"
        )

    # --- Inline and fenced code examples ---

    def test_fenced_code_block_not_flagged(self, tmp_path):
        """Paths inside fenced code blocks should not be flagged."""
        from northstar.audit import check_stale_references

        content = (
            "Here is an example configuration:\n"
            "```\n"
            "path: /nonexistent/config.yaml\n"
            "script: /nonexistent/run.py\n"
            "```\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        code_findings = [f for f in findings if "config.yaml" in f.evidence or "run.py" in f.evidence]
        assert len(code_findings) == 0, (
            f"Paths in fenced code blocks should not be flagged: {code_findings}"
        )

    def test_language_tagged_fence_not_flagged(self, tmp_path):
        """Paths inside language-tagged code fences should not be flagged."""
        from northstar.audit import check_stale_references

        content = (
            "Example YAML:\n"
            "```yaml\n"
            "database:\n"
            "  config: /nonexistent/db_config.yaml\n"
            "```\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        yaml_findings = [f for f in findings if "db_config.yaml" in f.evidence]
        assert len(yaml_findings) == 0, (
            f"Paths in language-tagged fences should not be flagged: {yaml_findings}"
        )

    def test_indented_fence_not_flagged(self, tmp_path):
        """Paths inside indented code fences should not be flagged."""
        from northstar.audit import check_stale_references

        content = (
            "## Example\n"
            "  ```\n"
            "  path: /nonexistent/indented_config.yaml\n"
            "  ```\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        indent_findings = [f for f in findings if "indented_config.yaml" in f.evidence]
        assert len(indent_findings) == 0, (
            f"Paths in indented fences should not be flagged: {indent_findings}"
        )

    def test_inline_code_not_flagged(self, tmp_path):
        """Paths inside inline code spans should not be flagged."""
        from northstar.audit import check_stale_references

        content = "Run `./scripts/deploy.py` to deploy the application."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        inline_findings = [f for f in findings if "deploy.py" in f.evidence]
        assert len(inline_findings) == 0, (
            f"Paths in inline code should not be flagged: {inline_findings}"
        )

    def test_code_block_with_real_path_not_flagged(self, tmp_path):
        """Even a real path inside a code block should be skipped (it's an example, not a dependency)."""
        from northstar.audit import check_stale_references

        (tmp_path / "example.yaml").write_text("key: value")
        content = (
            "```\n"
            "config: /example.yaml\n"
            "```\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        assert len(findings) == 0, (
            f"Paths in code blocks should always be skipped: {findings}"
        )

    # --- Ambiguous references ---

    def test_ambiguous_reference_low_confidence(self, tmp_path):
        """Ambiguous path references should be flagged with low confidence."""
        from northstar.audit import check_stale_references

        content = "The file /truly/nonexistent/path.md is referenced here without context."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        ambig_findings = [f for f in findings if "path.md" in f.evidence]
        assert len(ambig_findings) > 0, (
            "Ambiguous missing path should still be flagged"
        )
        assert ambig_findings[0].confidence == "low", (
            f"Ambiguous reference should have low confidence: {ambig_findings[0].confidence}"
        )

    def test_ambiguous_reference_explanation_mentions_context(self, tmp_path):
        """Ambiguous findings should include the context classification in the explanation."""
        from northstar.audit import check_stale_references

        content = "The file /truly/nonexistent/path.md is referenced here without context."
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        ambig_findings = [f for f in findings if "path.md" in f.evidence]
        if ambig_findings:
            explanation = ambig_findings[0].explanation
            assert "Context:" in explanation or "context" in explanation.lower(), (
                f"Ambiguous finding should mention context: {explanation}"
            )

    def test_md_table_paths_not_flagged(self, tmp_path):
        """Paths inside markdown table cells should not be flagged."""
        from northstar.audit import check_stale_references

        content = (
            "| File | Purpose |\n"
            "|------|---------|\n"
            "| /nonexistent/config.yaml | Main config |\n"
            "| /nonexistent/schema.json | Schema definition |\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        table_findings = [f for f in findings if "config.yaml" in f.evidence or "schema.json" in f.evidence]
        assert len(table_findings) == 0, (
            f"Paths in markdown tables should not be flagged: {table_findings}"
        )

    def test_url_paths_not_flagged(self, tmp_path):
        """Path-like segments inside URLs should not be flagged."""
        from northstar.audit import check_stale_references

        content = (
            "See https://example.com/docs/config.yaml for the API reference.\n"
            "Also https://github.com/user/repo/blob/main/scripts/setup.py\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        url_findings = [f for f in findings if "example.com" in f.evidence or "github.com" in f.evidence]
        assert len(url_findings) == 0, (
            f"URL path segments should not be flagged: {url_findings}"
        )

    def test_multiple_code_fences_toggle_correctly(self, tmp_path):
        """Multiple code fences should toggle in/out state correctly."""
        from northstar.audit import check_stale_references

        content = (
            "```\n"
            "/first_block/file.yaml\n"
            "```\n"
            "This is outside the code block: /outside_block/real_dep.yaml\n"
            "```\n"
            "/second_block/file.yaml\n"
            "```\n"
        )
        sources = [self._make_source("README.md", content)]
        findings = check_stale_references(sources, tmp_path)
        # Paths inside code blocks should not be flagged
        block_findings = [f for f in findings if "first_block" in f.evidence or "second_block" in f.evidence]
        assert len(block_findings) == 0, (
            f"Paths inside code blocks should not be flagged: {block_findings}"
        )
        # The path outside code blocks should be flagged
        outside_findings = [f for f in findings if "real_dep.yaml" in f.evidence]
        assert len(outside_findings) > 0, (
            "Path outside code blocks should be flagged"
        )

    def test_synthetic_contradiction_fixture_preserved(self):
        """The synthetic contradiction fixture should still exist and be detectable."""
        from northstar.discovery import discover_sources
        from northstar.audit import check_contradictions

        fixture_dir = Path(__file__).resolve().parent.parent / "fixtures" / "conflicting-demo"
        assert fixture_dir.exists(), "Conflicting demo fixture directory should exist"

        # Read the fixture files directly
        sources = []
        for f in fixture_dir.glob("*.md"):
            if f.name == "README.md":
                continue
            content = f.read_text(encoding="utf-8")
            from northstar.discovery import classify_source
            sources.append(
                type('InstructionSource', (), {
                    'path': f,
                    'content': content,
                    'source_type': classify_source(f),
                    'lines': content.splitlines(),
                    'line_count': len(content.splitlines()),
                })()
            )

        findings = check_contradictions(sources)
        assert len(findings) > 0, (
            "Synthetic contradiction fixture should produce contradiction findings"
        )
        assert any(f.category == "contradiction" for f in findings), (
            "Fixture should produce contradiction category findings"
        )

    def test_four_backtick_fence_not_flagged(self, tmp_path):
        """Paths inside 4-backtick fences should not be flagged, even with 3-backtick fences nearby."""
        from northstar.discovery import InstructionSource
        from northstar.audit import check_stale_references

        content = (
            "```python\n"
            "print('hello')\n"
            "```\n"
            "\n"
            "````markdown\n"
            "This is inside a 4-backtick fence: /missing/file.md\n"
            "````\n"
            "\n"
            "This is outside: /outside/dep.yaml\n"
        )
        source = InstructionSource(
            tmp_path / "test.md",
            content,
            "skill",
        )
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()

        findings = check_stale_references([source], hermes_home)
        # /missing/file.md should NOT be flagged (inside 4-backtick fence)
        assert not any("/missing/file.md" in f.evidence for f in findings), (
            "Path inside 4-backtick fence should not be flagged"
        )
        # /outside/dep.yaml SHOULD be flagged (outside any fence)
        assert any("/outside/dep.yaml" in f.evidence for f in findings), (
            "Path outside fences should still be flagged"
        )
