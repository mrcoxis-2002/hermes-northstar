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
                "by_category": _count_by_category(self.findings),
            },
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_text(self) -> str:
        """Human-readable report with summary and grouped findings."""
        lines = []
        lines.append("=" * 60)
        lines.append("NORTHSTAR COHERENCE AUDIT REPORT")
        lines.append("=" * 60)
        lines.append("")

        summary = self.to_dict()["summary"]
        lines.append(f"Active sources inspected: {len(self.sources_inspected)}")
        lines.append(f"Findings: {summary['errors']} errors, {summary['warnings']} warnings, {summary['info']} info")
        lines.append("")

        # Category breakdown
        by_cat = _count_by_category(self.findings)
        if by_cat:
            lines.append("Findings by category:")
            for cat, count in sorted(by_cat.items()):
                lines.append(f"  {cat}: {count}")
            lines.append("")

        # Source list (compact)
        if self.sources_inspected:
            lines.append("Sources:")
            for s in self.sources_inspected:
                lines.append(f"  - {s.get('path', '?')} ({s.get('source_type', '?')})")
            lines.append("")

        # Grouped findings
        if self.findings:
            # Group by category
            by_category: dict[str, list[Finding]] = {}
            for f in self.findings:
                by_category.setdefault(f.category, []).append(f)

            for cat in sorted(by_category):
                cat_findings = by_category[cat]
                lines.append(f"[{cat.upper()}] ({len(cat_findings)} finding(s))")
                lines.append("-" * 40)
                for i, f in enumerate(cat_findings, 1):
                    loc = f"{f.source_file}"
                    if f.source_line:
                        loc += f":{f.source_line}"
                    lines.append(f"  [{i}] [{f.severity.upper()}] {loc}")
                    lines.append(f"      Evidence: {f.evidence[:120]}")
                    lines.append(f"      {f.explanation}")
                    lines.append(f"      Suggestion: {f.suggestion}")
                    lines.append(f"      Confidence: {f.confidence}")
                    lines.append("")
        else:
            lines.append("No findings — configuration appears coherent.")
            lines.append("")

        if self.warnings:
            lines.append("Runtime warnings:")
            for w in self.warnings:
                lines.append(f"  ! {w}")
            lines.append("")

        if self.checks_skipped:
            lines.append("Checks that could not be performed:")
            for c in self.checks_skipped:
                lines.append(f"  - {c}")
            lines.append("")

        lines.append(f"Summary: {summary['errors']} errors, {summary['warnings']} warnings, {summary['info']} info")
        return "\n".join(lines)


def _count_by_category(findings: list[Finding]) -> dict[str, int]:
    """Count findings grouped by category."""
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.category] = counts.get(f.category, 0) + 1
    return counts


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

# Pattern for file path references (includes optional Windows drive letter prefix)
_PATH_REF_RE = re.compile(
    r'(?:[A-Za-z]:[/\\][\w\-./\\]+(?:\.(?:md|yaml|yml|json|toml|py|txt|csv)))'
    r'|'
    r'(?:[/\\][\w\-./\\]+(?:\.(?:md|yaml|yml|json|toml|py|txt|csv)))',
)

# Pattern for URLs — used to exclude URL strings from path reference checks
_URL_RE = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+', re.IGNORECASE)

# Pattern for YAML frontmatter boundaries
_YAML_FENCE_RE = re.compile(r'^---\s*$')

# Pattern for common YAML frontmatter keys that are descriptive metadata,
# not actionable instructions
_YAML_METADATA_KEYS = {
    "name", "version", "description", "author", "license", "homepage",
    "tags", "category", "platforms", "platform", "os",
    "date", "created", "modified", "updated",
    "status", "type", "kind", "id", "slug",
    "icon", "logo", "image", "cover",
    "weight", "order", "priority", "sort",
    "enabled", "active", "visible", "hidden",
    "dependencies", "requires", "provides",
    "aliases", "synonyms",
}

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


def _is_yaml_metadata_line(line: str) -> bool:
    """Return True if the line looks like a YAML metadata key-value pair.

    Matches lines like 'key: value' where the key is a known metadata field.
    This filters out descriptive frontmatter from duplicate detection.
    """
    stripped = line.strip()
    if not stripped or ':' not in stripped:
        return False
    key = stripped.split(':')[0].strip().lower()
    return key in _YAML_METADATA_KEYS


def _strip_yaml_frontmatter_lines(source: InstructionSource) -> list[tuple[int, str]]:
    """Return list of (line_number_1based, stripped_text) for lines NOT in YAML frontmatter.

    This is used by duplicate detection to skip metadata-only lines.
    """
    result = []
    in_frontmatter = False
    fence_count = 0  # 0 = outside, 1 = opened, 2 = closed

    for i, line in enumerate(source.lines):
        stripped = line.strip()
        if fence_count == 0:
            # Outside frontmatter
            if _YAML_FENCE_RE.match(stripped):
                fence_count = 1  # Opening fence
                continue
            else:
                # Regular line outside frontmatter
                if stripped and len(stripped) >= 20:
                    result.append((i + 1, stripped))
        elif fence_count == 1:
            # Inside frontmatter
            if _YAML_FENCE_RE.match(stripped):
                fence_count = 2  # Closing fence
                continue
            # Skip all lines inside frontmatter
        else:
            # After frontmatter closed
            if stripped and len(stripped) >= 20:
                result.append((i + 1, stripped))

    return result


# --- Reference context classification ---

# Patterns that indicate a path is inside a markdown link
_MD_LINK_RE = re.compile(r'\[[^\]]*\]\([^)]*\)')

# Patterns for fenced code blocks (track open/close)
# Matches lines that are fence markers (3+ backticks, optionally with language tag)
_CODE_FENCE_RE = re.compile(r'^(\s*)(`{3,})(.*)?$')

# Pattern for inline code spans
_INLINE_CODE_RE = re.compile(r'`[^`]+`')

# Pattern for markdown table cells (pipe-delimited rows)
_MD_TABLE_RE = re.compile(r'^\s*\|')

# Pattern for lines that are clearly documentation cross-references
_DOC_XREF_PHRASES = [
    r'see\s+.*?(?:reference|guide|doc)',
    r'read\s+.*?(?:reference|guide|doc)',
    r'check\s+.*?(?:reference|guide|doc)',
    r'references?/',
    r'templates/',
    r'\[.*?\]\(.*?\)',  # Markdown link syntax
    r'full\s+(?:guide|reference|documentation)',
    r'complete\s+(?:guide|reference|documentation|implementation)',
    r'for\s+(?:more|detailed|complete|full)',
    r'compiled\s+from',
    r'compilation\s+setup',
    r'compilation\s+instructions',
]
_DOC_XREF_RE = re.compile('|'.join(_DOC_XREF_PHRASES), re.IGNORECASE)

# Pattern for Unix home-directory paths
_UNIX_HOME_RE = re.compile(r'^/Users/[^/]+/', re.IGNORECASE)

# Pattern for Windows drive-letter paths
_WIN_DRIVE_RE = re.compile(r'^[A-Za-z]:[\\/]', re.IGNORECASE)

# Pattern for lines in YAML config that look like executable commands
# (e.g., hook commands with python.exe, node, etc.)
_EXEC_CMD_RE = re.compile(
    r'(?:python[^\s]*\.exe|node|python3?\b|bash|sh|cmd\.exe|pwsh)\s+.*?(?:\.py|\.js|\.sh|\.ps1)',
    re.IGNORECASE,
)

# Pattern for YAML config value lines (key: value where value contains a path)
_YAML_CONFIG_VALUE_RE = re.compile(r'^\s*\w+\s*:\s*.+')


def _classify_reference_context(source: InstructionSource, match_start: int, match_end: int, ref_path: str) -> str:
    """Classify the context of a file path reference.

    Returns one of:
    - "executable_config": Path in an executable configuration (hook command, script path)
    - "markdown_link": Path inside a markdown link [text](url)
    - "code_block": Path inside a fenced code block
    - "inline_code": Path inside an inline code span
    - "doc_xref": Documentation cross-reference (see X, read Y, etc.)
    - "md_table": Path inside a markdown table cell
    - "unix_home_path": Unix-style /Users/... path on any platform
    - "win_drive_path": Windows drive-letter path
    - "ambiguous": Cannot determine context reliably
    """
    lines = source.lines
    line_num = source.content[:match_start].count('\n') + 1
    current_line = lines[line_num - 1] if 0 <= line_num - 1 < len(lines) else ""
    current_line_stripped = current_line.strip()

    # Get extended context (5 lines before and after)
    ctx_start = max(0, line_num - 6)
    ctx_end = min(len(lines), line_num + 5)
    context_lines = lines[ctx_start:ctx_end]
    context_block = '\n'.join(context_lines)

    # 1. Check if inside a markdown link
    if _MD_LINK_RE.search(current_line):
        return "markdown_link"

    # 2. Check if inside a fenced code block
    # Track fence state by backtick count - a fence with N backticks is only closed
    # by another fence with exactly N backticks (Markdown spec)
    open_fences: list[int] = []  # Stack of backtick counts for open fences
    for i in range(max(0, line_num - 1)):
        m = _CODE_FENCE_RE.match(lines[i])
        if m:
            bt_count = len(m.group(2))  # Number of backticks
            if open_fences and open_fences[-1] == bt_count:
                # Closing fence matches the most recent opening fence
                open_fences.pop()
            else:
                # Opening a new fence
                open_fences.append(bt_count)
    if open_fences:
        return "code_block"

    # 3. Check if inside inline code
    line_before_match = current_line[:match_start - (source.content.count('\n', 0, match_start) * 0)]
    # Simpler: check if the match is between backticks on the same line
    line_text = current_line
    backtick_positions = [i for i, c in enumerate(line_text) if c == '`']
    # Count backticks before the match
    bt_before = sum(1 for p in backtick_positions if p < match_start - (source.content.find(current_line, max(0, source.content[:match_start].rfind('\n'))) if source.content[:match_start].rfind('\n') >= 0 else 0))
    if bt_before % 2 == 1:
        return "inline_code"

    # 4. Check if in a markdown table
    if _MD_TABLE_RE.match(current_line):
        # Check if surrounding lines also look like table rows
        is_table = False
        for cl in context_lines:
            if cl.strip() and not _MD_TABLE_RE.match(cl):
                # Check if it's a table separator line
                if not re.match(r'^\s*\|?[\s\-:|]+\|?', cl):
                    is_table = False
                    break
        # If current line is a table row, treat as table context
        if _MD_TABLE_RE.match(current_line) and current_line.count('|') >= 2:
            return "md_table"

    # 5. Check if it's a documentation cross-reference
    if _DOC_XREF_RE.search(context_block):
        return "doc_xref"

    # 6. Check if Unix home-directory path
    if _UNIX_HOME_RE.match(ref_path):
        return "unix_home_path"

    # 7. Check if Windows drive-letter path
    if _WIN_DRIVE_RE.match(ref_path):
        return "win_drive_path"

    # 8. Check if in executable config (YAML command value)
    if source.source_type == "config" and _YAML_CONFIG_VALUE_RE.match(current_line_stripped):
        if _EXEC_CMD_RE.search(current_line):
            return "executable_config"

    return "ambiguous"


def _is_url_reference(text: str) -> bool:
    """Return True if the text looks like a URL."""
    return bool(_URL_RE.search(text))


def _is_doc_example_path(ref_path: str, line_context: str) -> bool:
    """Return True if the path reference appears to be a documentation example.

    Checks for common doc-example patterns like code blocks, example markers,
    or paths that are clearly illustrative rather than actual references.

    Note: We do NOT use "```" as an indicator here because the context window
    may include fence markers from adjacent code blocks even when the path
    itself is outside any code block. The fenced-code-block check in
    _classify_reference_context handles that case separately.
    """
    context_lower = line_context.lower()
    doc_indicators = [
        "example", "e.g.", "such as", "like", "for instance",
        "e.g.:", "e.g. ", "e.g.\n",
    ]
    return any(indicator in context_lower for indicator in doc_indicators)


def _resolve_path_candidates(ref_path: str, source: InstructionSource, hermes_home: Path) -> list[Path]:
    r"""Build a list of candidate paths to check for a given reference.

    Handles:
    - Windows drive-letter paths (C:/..., D:\...)
    - Unix home-directory paths (/Users/username/...)
    - Relative paths (relative to source file dir, hermes_home, repo root)
    - Absolute paths on the current platform
    """
    candidates = []

    # 1. Windows drive-letter path — use as-is
    if _WIN_DRIVE_RE.match(ref_path):
        candidates.append(Path(ref_path))
        return candidates

    # 2. Unix home-directory path on Windows — try to map to Windows equivalent
    unix_home_match = _UNIX_HOME_RE.match(ref_path)
    if unix_home_match:
        # On Windows, /Users/username/ maps to C:/Users/username/
        import sys
        if sys.platform == "win32":
            # Extract the relative part after /Users/username/
            rel_part = ref_path[len(unix_home_match.group()):]
            # Try Windows home directory
            from os import environ
            win_home = environ.get("USERPROFILE", "")
            if win_home:
                candidates.append(Path(win_home) / rel_part)
        # Also try the literal path (may work in WSL/MSYS)
        candidates.append(Path(ref_path))
        return candidates

    # 3. Relative to source file's directory
    stripped = ref_path.lstrip("/\\")
    candidates.append(source.path.parent / stripped)

    # 4. Relative to hermes_home
    candidates.append(hermes_home / stripped)

    # 5. As absolute path (if platform-native)
    if ref_path.startswith("/") or ref_path.startswith("\\"):
        abs_path = Path(ref_path)
        if abs_path.is_absolute():
            candidates.append(abs_path)

    # 6. Relative to repo root (parent of hermes_home)
    repo_root = hermes_home.parent
    if repo_root != hermes_home:
        candidates.append(repo_root / stripped)

    return candidates


def check_duplicates(sources: list[InstructionSource]) -> list[Finding]:
    """Detect near-duplicate directives across sources.

    Skips YAML frontmatter metadata lines (e.g. 'platforms: linux macos windows')
    to avoid false positives on descriptive metadata.

    Consolidates duplicate groups into a single finding per group, listing
    all source files involved.
    """
    findings = []
    # Normalize lines: strip, lowercase, remove punctuation for comparison
    line_map: dict[str, list[tuple[str, int]]] = {}

    for source in sources:
        # Get lines outside YAML frontmatter
        visible_lines = _strip_yaml_frontmatter_lines(source)

        for line_num, stripped in visible_lines:
            # Skip YAML metadata key-value lines
            if _is_yaml_metadata_line(stripped):
                continue

            # Normalize for comparison
            normalized = re.sub(r'[^\w\s]', '', stripped).lower()
            if len(normalized) < 20:
                continue
            key = normalized[:100]  # Truncate for grouping
            if key not in line_map:
                line_map[key] = []
            line_map[key].append((str(source.path), line_num))

    # Find lines appearing in multiple sources — consolidate into one finding per group
    for key, locations in line_map.items():
        if len(locations) < 2:
            continue
        # Check if from different files
        files = set(loc[0] for loc in locations)
        if len(files) < 2:
            continue

        # Consolidate: one finding for the whole group
        first_loc = locations[0]
        sep = "\\"
        related = [f"{loc[0].split('/')[-1].split(sep)[-1]}:{loc[1]}" for loc in locations[1:]]

        findings.append(Finding(
            category="duplicate",
            severity="warning",
            source_file=first_loc[0],
            source_line=first_loc[1],
            evidence=key[:80],
            explanation=f"Near-duplicate directive found in {len(files)} files.",
            suggestion="Review and consolidate duplicate directives into a single source.",
            confidence="medium",
        ))
        # Store related sources in the finding's explanation for reference
        if related:
            findings[-1].explanation += f" Also in: {', '.join(related)}"

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
    """Check for references to non-existent files and unavailable tools.

    Context-aware stale reference detection:
    - Classifies each path reference by its surrounding context
    - Skips paths in documentation links, code blocks, and doc cross-references
    - Handles Windows/Unix path differences correctly
    - Flags genuinely missing local dependencies with clear explanations
    - Reports ambiguous cases at low confidence without false certainty
    """
    findings = []

    for source in sources:
        content = source.content

        # First, find all URL spans to exclude them from path checking
        url_spans = [(m.start(), m.end()) for m in _URL_RE.finditer(content)]

        # Check file path references
        for match in _PATH_REF_RE.finditer(content):
            ref_path = match.group()
            match_start = match.start()
            match_end = match.end()

            # Skip if this path is inside a URL
            in_url = False
            for url_start, url_end in url_spans:
                if match_start >= url_start and match_end <= url_end:
                    in_url = True
                    break
            if in_url:
                continue

            # Classify the reference context
            context = _classify_reference_context(source, match_start, match_end, ref_path)

            # Skip contexts that are documentation, not local dependencies:
            # - markdown_link: [text](path) — internal doc navigation
            # - code_block: inside fenced code — illustrative example
            # - inline_code: inside backticks — code example
            # - doc_xref: "see references/X.md" — documentation cross-reference
            # - md_table: path in a documentation table cell
            skip_contexts = {"markdown_link", "code_block", "inline_code", "doc_xref", "md_table"}
            if context in skip_contexts:
                continue

            # Also skip if it looks like a doc example (legacy check)
            line_num = content[:match_start].count('\n') + 1
            lines = content.split('\n')
            if 0 <= line_num - 1 < len(lines):
                ctx_start = max(0, line_num - 2)
                ctx_end = min(len(lines), line_num + 1)
                line_context = '\n'.join(lines[ctx_start:ctx_end])
                if _is_doc_example_path(ref_path, line_context):
                    continue

            # Resolve path candidates
            candidates = _resolve_path_candidates(ref_path, source, hermes_home)
            found = any(c.exists() for c in candidates)

            if found:
                continue

            # Path not found — generate finding with context-aware explanation
            line_num = content[:match_start].count('\n') + 1

            # Build explanation based on context
            if context == "executable_config":
                # This is a hook command or script path — genuinely required
                explanation = (
                    f"Executable script path '{ref_path}' referenced in configuration does not exist. "
                    f"Checked: {', '.join(str(c) for c in candidates[:3])}."
                )
                confidence = "high"
                suggestion = "Verify the script exists at the specified path, or update the configuration."
            elif context == "unix_home_path":
                # Unix path on Windows — explain the platform mismatch
                explanation = (
                    f"Unix-style path '{ref_path}' cannot be resolved on this platform (Windows). "
                    f"This appears to be a macOS/Linux home-directory path. "
                    f"Checked: {', '.join(str(c) for c in candidates[:3])}."
                )
                confidence = "medium"
                suggestion = "Update the path to use Windows format (e.g., C:/Users/username/...) or verify the script location."
            elif context == "win_drive_path":
                explanation = (
                    f"Windows path '{ref_path}' does not exist. "
                    f"Checked: {', '.join(str(c) for c in candidates[:3])}."
                )
                confidence = "high"
                suggestion = "Verify the path is correct or update the reference."
            else:
                # Ambiguous context — be conservative
                explanation = (
                    f"Path '{ref_path}' not found. Context: {context}. "
                    f"Checked locations: {', '.join(str(c) for c in candidates[:3])}. "
                    f"This may be a reference to an external project file or documentation."
                )
                confidence = "low"
                suggestion = "Verify whether this is a required local dependency or a documentation reference."

            findings.append(Finding(
                category="stale_reference",
                severity="warning",
                source_file=str(source.path),
                source_line=line_num,
                evidence=f"Reference to '{ref_path}'",
                explanation=explanation,
                suggestion=suggestion,
                confidence=confidence,
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
