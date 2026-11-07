"""Tool handlers — the code that runs when the LLM calls each tool."""

import json
import logging
from pathlib import Path
from os import environ

from .discovery import discover_sources
from .audit import run_audit

logger = logging.getLogger(__name__)

# Plugin state
_last_audit = None
_last_audit_text = None


def _get_hermes_home() -> Path:
    from pathlib import Path
    return Path(environ.get("HERMES_HOME", Path.home() / ".hermes"))


def _resolve_scope(scope: str, hermes_home: Path, project_root: Path):
    """Return discovery kwargs for a given scope."""
    if scope == "home":
        return {"hermes_home": hermes_home, "project_root": None}
    elif scope == "project":
        return {"hermes_home": None, "project_root": project_root}
    elif scope == "skills":
        skills_dir = hermes_home / "skills"
        return {"hermes_home": skills_dir if skills_dir.is_dir() else None, "project_root": None}
    else:  # full
        return {"hermes_home": hermes_home, "project_root": project_root}


def northstar_audit(args: dict, **kwargs) -> str:
    """Run a coherence audit on instruction sources."""
    try:
        scope = args.get("scope", "full")
        output_format = args.get("output_format", "text")

        hermes_home = _get_hermes_home()
        project_root = Path.cwd()

        discovery_kwargs = _resolve_scope(scope, hermes_home, project_root)
        sources = discover_sources(**discovery_kwargs)

        if not sources:
            result = {
                "status": "ok",
                "message": f"No instruction sources found in {scope} scope.",
                "sources_inspected": 0,
                "findings": [],
            }
            return json.dumps(result, indent=2)

        report = run_audit(sources, hermes_home)

        global _last_audit, _last_audit_text
        _last_audit = report.to_dict()
        _last_audit_text = report.to_text()

        if output_format == "json":
            return json.dumps(report.to_dict(), indent=2)
        else:
            return report.to_text()

    except Exception as e:
        logger.exception("northstar_audit failed")
        return json.dumps({
            "status": "error",
            "error": f"Audit failed: {e}",
        })


def northstar_status(args: dict, **kwargs) -> str:
    """Show current Northstar configuration status."""
    try:
        include_last = args.get("include_last_audit", False)

        result = {
            "plugin": "northstar",
            "version": "0.1.0",
            "enforcement_mode": "off",  # Default; would read from config in full impl
            "protected_paths": [],
        }

        if include_last and _last_audit is not None:
            result["last_audit"] = _last_audit
        elif include_last:
            result["last_audit"] = "No audit has been run yet. Use northstar_audit first."

        return json.dumps(result, indent=2)

    except Exception as e:
        logger.exception("northstar_status failed")
        return json.dumps({
            "status": "error",
            "error": f"Status check failed: {e}",
        })
