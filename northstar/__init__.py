"""Northstar plugin — registration and hooks."""

import logging

from . import schemas, tools
from .discovery import discover_sources
from .audit import run_audit

logger = logging.getLogger(__name__)


def _on_pre_tool_call(tool_name: str, args: dict, **kwargs):
    """Hook: intercept tool calls when enforcement is active.

    NOTE: Enforcement is not yet implemented for MVP. This hook is registered
    but does nothing. Remove this function and its registration when
    enforcement mode is fully implemented and tested.
    """
    pass


def _on_pre_llm_call(session_id: str, is_first_turn: bool, **kwargs):
    """Hook: inject compact coherence guidance on first turn."""
    # Only inject on first turn to avoid repeating every turn
    if not is_first_turn:
        return None

    # Check if we have a cached audit
    last_audit = getattr(tools, "_last_audit", None)
    if last_audit is None:
        return None

    findings = last_audit.get("findings", [])
    errors = [f for f in findings if f.get("severity") == "error"]
    warnings = [f for f in findings if f.get("severity") == "warning"]

    if not errors and not warnings:
        return None

    # Build compact guidance
    parts = ["[Northstar Coherence Notice]"]
    if errors:
        parts.append(f"  {len(errors)} conflict(s) detected in instruction sources:")
        for e in errors[:3]:  # Limit to 3
            cat = e.get("category", "unknown")
            loc = e.get("source_file", "?")
            parts.append(f"  - [{cat}] {loc}: {e.get('explanation', '')[:80]}")

    if warnings:
        parts.append(f"  {len(warnings)} warning(s) detected.")

    parts.append("  Run /northstar audit for full details.")

    text = "\n".join(parts)

    # Respect max_chars limit
    max_chars = 2000  # Default from config_schema
    if len(text) > max_chars:
        text = text[:max_chars] + "\n  [truncated]"

    return {"context": text}


def register(ctx):
    """Wire schemas to handlers and register hooks."""
    logger.info("Northstar plugin registering")

    # Register tools
    ctx.register_tool(
        name="northstar_audit",
        toolset="northstar",
        schema=schemas.NORTHSTAR_AUDIT,
        handler=tools.northstar_audit,
    )

    ctx.register_tool(
        name="northstar_status",
        toolset="northstar",
        schema=schemas.NORTHSTAR_STATUS,
        handler=tools.northstar_status,
    )

    # Register hooks
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)

    logger.info("Northstar plugin registered successfully")
