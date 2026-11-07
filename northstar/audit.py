"""Deterministic coherence audit — detects conflicts in instruction sources."""

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .discovery import InstructionSource


@dataclass
class Finding:
    """A single audit finding."""
    category: str          # "duplicate", "contradiction", "stale_reference", "missing_file", "unavailable_tool"
    severity: str          # "error", "warning", "info"
    source_file: str
    source_line: Optional[int]
    evidence: str
    explanation: str
    suggestion: str
    confidence: str        # "high", "medium", "low"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditReport:
    """Complete audit report."""
    sources_inspected: list[dict]
    findings: list[Finding]
    warnings: list[str]
    checks_skipped: list[str]

    def to_dict(self) -> dict:
        return {
            "sources_inspected": self.sources_inspected,
            "findings": [f.to_dict() for f in self.findings],
            "warnings": self.warnings,
            "checks_skipped": self.checks_skipped,
            "summary": {
                "total_findings": len(self.findings),
                "errors": sum(1 for f in self.findings if f.severity == "error"),
                "warnings": sum(1 for f in self.findings if f.severity == "warning"),
                "info": sum(1 for f in self.findings if f.severity == "info"),
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_text(self) -> str:
        """Human-readable report."""
        lines = []
        lines.append("=" * 60)
        lines.append("NORTHSTAR COHERENCE AUDIT REPORT")
        lines.append("=" * 60)
        lines.append("")

        lines.append(f"Sources inspected: {len(self.sources_inspected)}")
        for s in self.sources_inspected:
            lines.append(f"  - {s.get('path', '?')} ({s.get('source_type', '?')})")
        lines.append("")

        if self.findings:
            lines.append(f"Findings: {len(self.findings)}")
            lines.append("-" * 40)
            for i, f in enumerate(self.findings, 1):
                loc = f"{f.source_file}"
                if f.source_line:
                    loc += f":{f.source_line}"
                lines.append(f"[{i}] [{f.severity.upper()}] {f.category}")
                lines.append(f"    Location: {loc}")
                lines.append(f"    Evidence: {f.evidence[:120]}")
                lines.append(f"    {f.explanation}")
                lines.append(f"    Suggestion: {f.suggestion}")
                lines.append(f"    Confidence: {f.confidence}")
                lines.append("")
        else:
            lines.append("No findings — configuration appears coherent.")
            lines.append("")

        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  ! {w}")
            lines.append("")

        if self.checks_skipped:
            lines.append("Checks that could not be performed:")
            for c in self.checks_skipped:
                lines.append(f"  - {c}")
            lines.append("")

        summary = self.to_dict()["summary"]
        lines.append(f"Summary: {summary['errors']} errors, {summary['warnings']} warnings, {summary['info']} info")
        return "\n".join(lines)


# --- Detection algorithms ---

# Known Hermes tool names (subset for reference checking)
KNOWN_TOOLS = {
    "browser_back", "browser_click", "browser_console", "browser_get_images",
    "browser_navigate", "browser_press", "browser_scroll", "browser_snapshot",
    "browser_type", "browser_vault_enter_code", "browser_vault_fill",
    "browser_vault_list", "browser_vault_save_login", "browser_vault_unlock",
    "browser_vision", "clarify", "delegate_task", "execute_code", "memory",
    "patch", "read_file", "search_files", "skill_manage", "skill_view",
    "skills_list", "terminal", "vision_analyze", "web_extract", "web_search",
    "write_file", "tool_search", "tool_describe", "tool_call",
}

# Pattern for tool references in text: mentions of tool names
_TOOL_REF_RE = re.compile(r'\b(' + '|'.join(re.escape(t) for t in KNOWN_TOOLS) + r')\b', re.IGNORECASE)

# Pattern for file path references
_PATH_REF_RE = re.compile(r'(?:[/\\][\w\-./\\]+(?:\.(?:md|yaml|yml|json|toml|py|txt|csv)))')

# Pattern for model references
_MODEL_REF_RE = re.compile(r'(?:model["\s:]+["\']?)([\w\-\.]+)', re.IGNORECASE)

# Common contradictory directive patterns
_CONTRADICTION_PAIRS = [
    (r'(?:ask|request|require)\s+(?:for\s+)?(?:confirmation|approval|permission)',
     r'(?:without\s+(?:asking|interrup|confirmation|approval|permission))'),
    (r'(?:never|do\s+not|don\'t)\s+(?:modify|edit|change|write)',
     r'(?:automatically\s+(?:modify|edit|change|write))'),
    (r'(?:always|must)\s+(?:confirm|verify|check)',
     r'(?:skip\s+(?:confirm|verify|check))'),
    (r'(?:require|need)\s+(?:human|user)\s+(?:approval|confirmation)',
     r'(?:autonomous|without\s+human|without\s+user)'),
]

# Scope qualifiers that indicate a directive applies to a specific context.
# When both sides of a contradiction carry different scope qualifiers, the
# directives govern different scopes and are NOT contradictory.
_SCOPE_QUALIFIERS = [
    r'(?:production|prod|live|main|release)\b',
    r'(?:test|testing|spec|fixture)\b',
    r'(?:staging|stage|pre[-\s]?prod)\b',
    r'(?:development|dev|local)\b',
    r'(?:configuration\s+file|config\s+file)\b',
    r'(?:new\s+file|generated\s+file)\b',
    r'(?:routine\s+task|autonom)',
    r'(?:CI/CD|pipeline|build|deploy)\b',
]


def _extract_scope_qualifiers(text: str) -> list[str]:
    """Extract scope qualifier patterns found in a text snippet."""
    qualifiers = []
    for pattern in _SCOPE_QUALIFIERS:
        if re.search(pattern, text, re.IGNORECASE):
            qualifiers.append(pattern)
    return qualifiers


def _scopes_differ(qualifiers_a: list[str], qualifiers_b: list[str]) -> bool:
    """Return True if two sets of scope qualifiers are clearly different."""
    if not qualifiers_a or not qualifiers_b:
        return False
    # If they share no qualifiers, they likely govern different scopes
    return set(qualifiers_a).isdisjoint(set(qualifiers_b))


def _get_line_context(content: str, match_start: int, window: int = 200) -> str:
    """Get surrounding context for a regex match to check scope qualifiers.

    Uses a line-level window: extracts the line containing the match plus
    the immediately preceding and following lines. This prevents a single
    short file from having both qualifiers in both contexts.
    """
    # Find the line boundaries around the match
    before = content[:match_start]
    after = content[match_start:]

    # Go back to start of current line
    line_start = before.rfind('\n')
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1  # Skip the newline char

    # Find end of current line
    line_end = after.find('\n')
    if line_end == -1:
        line_end = len(after)
    else:
        line_end += len(before)

    # Include one line before and after for context
    prev_line_start = content[:line_start].rfind('\n')
    if prev_line_start == -1:
        prev_line_start = 0
    else:
        prev_line_start += 1

    next_line_end = content[line_end:].find('\n')
    if next_line_end == -1:
        next_line_end = len(content)
    else:
        next_line_end = line_end + next_line_end

    return content[prev_line_start:next_line_end]


def check_duplicates(sources: list[InstructionSource]) -> list[Finding]:
    """Detect near-duplicate directives across sources."""
    findings = []
    # Normalize lines: strip, lowercase, remove punctuation for comparison
    line_map: dict[str, list[tuple[str, int]]] = {}

    for source in sources:
        for i, line in enumerate(source.lines, 1):
            stripped = line.strip()
            if not stripped or len(stripped) < 20:
                continue
            # Normalize for comparison
            normalized = re.sub(r'[^\w\s]', '', stripped).lower()
            if len(normalized) < 20:
                continue
            key = normalized[:100]  # Truncate for grouping
            if key not in line_map:
                line_map[key] = []
            line_map[key].append((str(source.path), i))

    # Find lines appearing in multiple sources
    for key, locations in line_map.items():
        if len(locations) < 2:
            continue
        # Check if from different files
        files = set(loc[0] for loc in locations)
        if len(files) < 2:
            continue
        for loc in locations[1:]:
            findings.append(Finding(
                category="duplicate",
                severity="warning",
                source_file=loc[0],
                source_line=loc[1],
                evidence=key[:80],
                explanation=f"Near-duplicate directive found in {len(files)} files.",
                suggestion="Review and consolidate duplicate directives into a single source.",
                confidence="medium",
            ))

    return findings


def check_contradictions(sources: list[InstructionSource]) -> list[Finding]:
    """Detect contradictory directives across sources.

    Scope-aware: when both sides of a contradiction carry different scope
    qualifiers (e.g., 'production files' vs 'test files'), the finding is
    downgraded to a warning with 'possible' confidence, indicating it may
    require human review rather than being a definite error.
    """
    findings = []

    for source in sources:
        if source.source_type != "instruction":
            continue
        content = source.content.lower()

        for allow_pattern, deny_pattern in _CONTRADICTION_PAIRS:
            allow_match = re.search(allow_pattern, content)
            deny_match = re.search(deny_pattern, content)

            if allow_match and deny_match:
                # Find line numbers
                allow_line = content[:content.lower().index(allow_match.group())].count('\n') + 1
                deny_line = content[:content.lower().index(deny_match.group())].count('\n') + 1

                # Scope analysis: check context around each match
                allow_context = _get_line_context(content, allow_match.start())
                deny_context = _get_line_context(content, deny_match.start())

                qualifiers_a = _extract_scope_qualifiers(allow_context)
                qualifiers_b = _extract_scope_qualifiers(deny_context)

                if _scopes_differ(qualifiers_a, qualifiers_b):
                    # Different scopes — likely not a real contradiction
                    # Report as possible conflict requiring human review
                    findings.append(Finding(
                        category="contradiction",
                        severity="warning",
                        source_file=str(source.path),
                        source_line=allow_line,
                        evidence=f"'{allow_match.group()}' vs '{deny_match.group()}' (different scopes)",
                        explanation=(
                            "Possible contradiction with different scope qualifiers. "
                            "The directives may govern different contexts and not actually conflict. "
                            "Human review recommended."
                        ),
                        suggestion="Verify whether the directives apply to the same scope. If they govern different contexts, no action needed.",
                        confidence="low",
                    ))
                else:
                    # Same scope or no scope qualifiers — definite contradiction
                    findings.append(Finding(
                        category="contradiction",
                        severity="error",
                        source_file=str(source.path),
                        source_line=allow_line,
                        evidence=f"'{allow_match.group()}' vs '{deny_match.group()}'",
                        explanation="Contradictory directives found in the same file.",
                        suggestion="Resolve the contradiction by choosing one directive and removing the other.",
                        confidence="medium",
                    ))

    # Cross-file contradiction check
    source_contents = [(s, s.content.lower()) for s in sources if s.source_type == "instruction"]
    for i, (s1, c1) in enumerate(source_contents):
        for s2, c2 in source_contents[i+1:]:
            for allow_pattern, deny_pattern in _CONTRADICTION_PAIRS:
                allow_m1 = re.search(allow_pattern, c1)
                deny_m2 = re.search(deny_pattern, c2)

                if allow_m1 and deny_m2:
                    # Check scope qualifiers
                    ctx1 = _get_line_context(c1, allow_m1.start())
                    ctx2 = _get_line_context(c2, deny_m2.start())
                    qa = _extract_scope_qualifiers(ctx1)
                    qb = _extract_scope_qualifiers(ctx2)

                    if _scopes_differ(qa, qb):
                        severity = "warning"
                        confidence = "low"
                        explanation = (
                            "Possible cross-file contradiction with different scope qualifiers. "
                            "The directives may govern different contexts and not actually conflict. "
                            "Human review recommended."
                        )
                        suggestion = "Verify whether the directives apply to the same scope. If they govern different contexts, no action needed."
                    else:
                        severity = "error"
                        confidence = "medium"
                        explanation = "Cross-file contradiction: one file allows what another denies."
                        suggestion = "Align directives across files or consolidate into a single authoritative source."

                    findings.append(Finding(
                        category="contradiction",
                        severity=severity,
                        source_file=str(s1.path),
                        source_line=None,
                        evidence=f"Allow directive in {s1.path.name} conflicts with deny directive in {s2.path.name}",
                        explanation=explanation,
                        suggestion=suggestion,
                        confidence=confidence,
                    ))

                deny_m1 = re.search(deny_pattern, c1)
                allow_m2 = re.search(allow_pattern, c2)

                if deny_m1 and allow_m2:
                    # Check scope qualifiers
                    ctx1 = _get_line_context(c1, deny_m1.start())
                    ctx2 = _get_line_context(c2, allow_m2.start())
                    qa = _extract_scope_qualifiers(ctx1)
                    qb = _extract_scope_qualifiers(ctx2)

                    if _scopes_differ(qa, qb):
                        severity = "warning"
                        confidence = "low"
                        explanation = (
                            "Possible cross-file contradiction with different scope qualifiers. "
                            "The directives may govern different contexts and not actually conflict. "
                            "Human review recommended."
                        )
                        suggestion = "Verify whether the directives apply to the same scope. If they govern different contexts, no action needed."
                    else:
                        severity = "error"
                        confidence = "medium"
                        explanation = "Cross-file contradiction: one file denies what another allows."
                        suggestion = "Align directives across files or consolidate into a single authoritative source."

                    findings.append(Finding(
                        category="contradiction",
                        severity=severity,
                        source_file=str(s2.path),
                        source_line=None,
                        evidence=f"Deny directive in {s1.path.name} conflicts with allow directive in {s2.path.name}",
                        explanation=explanation,
                        suggestion=suggestion,
                        confidence=confidence,
                    ))

    return findings


def check_stale_references(sources: list[InstructionSource], hermes_home: Path) -> list[Finding]:
    """Check for references to non-existent files and unavailable tools."""
    findings = []
    warnings = []

    for source in sources:
        # Check file path references
        for match in _PATH_REF_RE.finditer(source.content):
            ref_path = match.group()
            # Try to resolve relative to hermes_home and source's directory
            candidates = [
                hermes_home / ref_path,
                source.path.parent / ref_path,
                Path(ref_path),
            ]
            found = any(c.exists() for c in candidates if c.is_absolute() or c.anchor)
            if not found:
                line_num = source.content[:match.start()].count('\n') + 1
                findings.append(Finding(
                    category="stale_reference",
                    severity="warning",
                    source_file=str(source.path),
                    source_line=line_num,
                    evidence=f"Reference to '{ref_path}'",
                    explanation="Referenced file path does not exist in scanned directories.",
                    suggestion="Verify the path is correct or remove the stale reference.",
                    confidence="low",
                ))

    return findings


def check_unavailable_tools(sources: list[InstructionSource]) -> list[Finding]:
    """Check for references to tools that may not be available."""
    findings = []

    for source in sources:
        if source.source_type != "instruction":
            continue
        # Look for explicit "use the X tool" or "call X" patterns
        tool_mentions = _TOOL_REF_RE.findall(source.content)
        # This is informational only — we can't know the actual toolset at audit time
        # without access to the Hermes runtime, so skip this check and note it
        pass

    return findings


def run_audit(
    sources: list[InstructionSource],
    hermes_home: Path,
) -> AuditReport:
    """Run all audit checks and return a report."""
    findings = []
    warnings = []
    checks_skipped = []

    try:
        findings.extend(check_duplicates(sources))
    except Exception as e:
        warnings.append(f"Duplicate check failed: {e}")

    try:
        findings.extend(check_contradictions(sources))
    except Exception as e:
        warnings.append(f"Contradiction check failed: {e}")

    try:
        findings.extend(check_stale_references(sources, hermes_home))
    except Exception as e:
        warnings.append(f"Stale reference check failed: {e}")

    # Tool availability check requires runtime access — skip and note
    checks_skipped.append("Tool availability check (requires Hermes runtime access)")
    checks_skipped.append("Model reference validation (requires provider API access)")

    sources_inspected = [s.to_dict() for s in sources]

    return AuditReport(
        sources_inspected=sources_inspected,
        findings=findings,
        warnings=warnings,
        checks_skipped=checks_skipped,
    )
