# Northstar Changelog

## 0.1.0 (MVP) — 2026-11-06

### What works
- **northstar_audit** tool: Scans instruction sources for contradictions, duplicate directives, and stale file references
- **northstar_status** tool: Shows plugin status and last audit results
- **pre_llm_call** hook: On the first turn of a session, injects compact coherence guidance when a cached audit (from a previous `northstar_audit` call in the same process) has errors or warnings. It does not run an audit automatically.
- **Demo fixture**: `fixtures/conflicting-demo/` with `python -m northstar.demo`

### Audit capabilities
- Duplicate directive detection (normalized text comparison across files; YAML frontmatter metadata is excluded)
- Same-file contradiction detection (predefined allow/deny pattern pairs)
- Cross-file contradiction detection (directive conflicts between files)
- Scope-aware contradiction classification (contradictions with different scope qualifiers are downgraded to warnings for human review)
- Stale file reference detection (non-existent paths in instruction files; context-aware: skips markdown links, code blocks, inline code, doc cross-references, and URLs)
- Archived directory exclusion (`.archive`, `.backup`, `deprecated`, etc.)
- Duplicate finding consolidation
- Windows and Unix-style path handling
- Markdown code-fence handling
- JSON and text output formats

### Test coverage
- 96 automated tests, all passing
- Covers discovery, audit logic, tools, registration, hooks, scope-aware contradictions, and input validation
- Official Hermes Plugin Doctor and catalog validation passing

### Known limitations
- Contradiction detection is pattern-based and cannot distinguish novel or implicitly scoped directives
- Tool availability check requires Hermes runtime access (skipped)
- Model reference validation requires provider API access (skipped)
- Confidence levels are heuristic — not calibrated against ground truth
- Enforcement mode (`enforce_mode`, `protected_paths`) is declared in the config schema but not yet implemented

### Next release tasks
- Implement enforcement mode (warn/block)
- Add scope-aware contradiction detection refinements to reduce false positives
- Calibrate confidence levels with ground-truth test cases
- Wire config_schema enforcement settings to actual behavior
- Add tool availability check via Hermes runtime introspection
- Add integration tests with real Hermes home directory
