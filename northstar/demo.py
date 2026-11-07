"""Northstar demo — run audit on the conflicting-demo fixture."""

import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from northstar.discovery import InstructionSource, classify_source
from northstar.audit import run_audit


def main():
    fixture_dir = project_root / "fixtures" / "conflicting-demo"
    if not fixture_dir.is_dir():
        print(f"Fixture directory not found: {fixture_dir}")
        sys.exit(1)

    # Discover .md files in fixture
    sources = []
    for md_file in sorted(fixture_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        source_type = classify_source(md_file)
        sources.append(InstructionSource(md_file, content, source_type))

    if not sources:
        print("No instruction sources found in fixture.")
        sys.exit(1)

    print(f"Discovered {len(sources)} sources:")
    for s in sources:
        print(f"  - {s.path.name} ({s.source_type}, {s.line_count} lines)")
    print()

    # Run audit
    report = run_audit(sources, fixture_dir)

    # Print human-readable report
    print(report.to_text())
    print()

    # Print JSON summary
    data = report.to_dict()
    summary = data["summary"]
    print("=" * 60)
    print("JSON SUMMARY")
    print("=" * 60)
    print(json.dumps(summary, indent=2))
    print()

    if summary["errors"] > 0:
        print(f"RESULT: {summary['errors']} error(s) found — configuration has conflicts.")
    elif summary["warnings"] > 0:
        print(f"RESULT: {summary['warnings']} warning(s) — review recommended.")
    else:
        print("RESULT: Clean — no issues detected.")


if __name__ == "__main__":
    main()
