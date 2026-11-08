from pathlib import Path

from northstar.discovery import InstructionSource, classify_source
from northstar.audit import run_audit


def test_official_demo_fixture_findings():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "conflicting-demo"

    sources = []
    for name in ("ExampleBehavior.md", "ExampleCore.md"):
        path = fixture / name
        sources.append(
            InstructionSource(
                path,
                path.read_text(encoding="utf-8"),
                classify_source(path),
            )
        )

    report = run_audit(sources, fixture)
    result = report.to_dict()
    summary = result["summary"]

    assert summary["total_findings"] == 3
    assert summary["errors"] == 1
    assert summary["warnings"] == 2
    assert summary["by_category"] == {
        "contradiction": 1,
        "duplicate": 1,
        "stale_reference": 1,
    }

    text = report.to_text()
    assert "Conflicting instructions between ExampleBehavior.md and ExampleCore.md" in text
    assert "'without human' vs 'require human approval'" in text
    assert "/old/path/settings.yaml" in text