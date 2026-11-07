# Hermes Northstar Plugin

Deterministic coherence audit for Hermes Agent instruction sources. Scans SOUL.md, SKILL.md, and other instruction files for contradictions, duplicate directives, and stale file references — then injects findings as context on the first turn of each session.

## What Is Implemented

### Tools

| Tool | Description |
|------|-------------|
| `northstar_audit` | Scans instruction sources for contradictions, duplicates, stale references. Accepts `scope` (home/project/skills/all) and `output` (json/text) args. |
| `northstar_status` | Returns plugin status and last audit results as JSON. |

### Hook

| Hook | Description |
|------|-------------|
| `pre_llm_call` | On the first turn of each session, injects a compact coherence guidance block when audit findings exist. Matches the Hermes hook contract: `(session_id, user_message, conversation_history, is_first_turn, model, platform, **kwargs)`. |

### Audit Checks

- **Duplicate directive detection** — Near-duplicate lines across files (Levenshtein similarity > 0.85)
- **Same-file contradiction detection** — Regex-based pattern pairs within a single file
- **Cross-file contradiction detection** — Directive conflicts between files
- **Stale file reference detection** — Non-existent file paths mentioned in instruction files
- **Scope-aware contradiction classification** — Directives governing different scopes (e.g., "production files" vs "test files") are flagged as warnings requiring human review, not errors

### Output

- JSON and text report formats
- Confidence levels: `high`, `medium`, `low`
- Severity levels: `error`, `warning`, `info`

## Heuristic Limitations

The audit uses regex-based pattern matching, not semantic analysis. Known limitations:

1. **Scope detection is pattern-based** — Recognizes common scope qualifiers (production/test/dev/config/new file/routine task/CI-CD) but may miss custom or implicit scopes. Unrecognized scopes are reported as possible conflicts requiring human review.
2. **Contradiction patterns are finite** — Only predefined verb/phrase pairs are checked (e.g., "always" vs "never", "require approval" vs "without asking"). Novel contradictions may be missed.
3. **Duplicate detection uses Levenshtein similarity** — Semantically identical but differently worded directives may not be caught.
4. **Stale reference check is filesystem-only** — Cannot detect references to external URLs, database entries, or dynamically generated paths.
5. **Confidence calibration** — Confidence levels are heuristic, not calibrated against ground truth.

## Planned (Not Yet Implemented)

- `pre_tool_call` enforcement mode (warn/block on tool calls conflicting with audit findings)
- Tool availability check via Hermes runtime introspection
- Model reference validation against provider API
- Confidence level calibration with ground-truth test cases
- Configurable enforcement settings wired to actual behavior

## Installation

```bash
# Copy plugin directory to Hermes plugins path
cp -r hermes-northstar ~/.hermes/plugins/northstar

# Or develop in-place
ln -s /path/to/hermes-northstar ~/.hermes/plugins/northstar
```

## Usage

```bash
# Run the demo audit
python -m northstar.demo

# Run tests
python -m pytest tests/ -v

# Manual audit (via Hermes tools)
# In Hermes: /northstar_audit scope=all output=json
```

## Demo Fixture

`fixtures/conflicting-demo/` contains synthetic instruction files with known contradictions for testing:

- `ExampleCore.md` — Core directives
- `ExampleBehavior.md` — Behavior rules (includes cross-file contradictions with ExampleCore.md)
- `README.md` — Explanatory text (excluded from audit scan)

## Test Coverage

52 tests covering discovery, audit logic, tools, registration, hooks, scope-aware contradictions, and input validation.
