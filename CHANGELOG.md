# Northstar Changelog

## 0.1.0 (MVP) — 2026-11-06

### What works
- **northstar_audit** tool: Scans instruction sources for contradictions, duplicate directives, and stale file references
- **northstar_status** tool: Shows plugin status and last audit results
- **pre_llm_call** hook: Injects compact coherence guidance on first turn when audit findings exist
- **pre_tool_call** hook: Registered but inactive (enforcement not yet implemented)
- **Demo fixture**: `fixtures/conflicting-demo/` with `python -m northstar.demo`

### Audit capabilities
- Duplicate directive detection (near-duplicate lines across files)
- Same-file contradiction detection (regex-based pattern pairs)
- Cross-file contradiction detection (directive conflicts between files)
- Stale file reference detection (non-existent paths in instruction files)
- JSON and text output formats

### Test coverage
- 34 tests, all passing
- Discovery: 6 tests (hermes home, project, skills, empty, unreadable, dedup)
- Audit: 14 tests (clean config, duplicates, contradictions, stale refs, reports, false positives)
- Tools: 4 tests (audit with/without sources, status with/without audit)
- Registration: 3 tests (no crash, tool names, hook names)
- Hooks: 4 tests (pre_llm_call variants, pre_tool_call no crash)
- Input validation: 3 tests (empty scope, invalid scope, empty args)

### Known limitations
- Contradiction detection is regex-based and cannot distinguish scoped directives (e.g., "ask for production" vs "proceed without asking for test files")
- Tool availability check requires Hermes runtime access (skipped)
- Model reference validation requires provider API access (skipped)
- pre_tool_call enforcement hook is a no-op placeholder
- Confidence levels are all "medium" or "low" — no calibration against ground truth

### Next release tasks
- Implement pre_tool_call enforcement mode (warn/block)
- Add scope-aware contradiction detection to reduce false positives
- Calibrate confidence levels with ground-truth test cases
- Wire config_schema enforcement settings to actual behavior
- Add tool availability check via Hermes runtime introspection
- Add integration tests with real Hermes home directory
