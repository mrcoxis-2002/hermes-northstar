"""Tool schemas — what the LLM sees."""

NORTHSTAR_AUDIT = {
    "name": "northstar_audit",
    "description": (
        "Run a coherence audit on instruction sources. Scans Hermes home, "
        "project root, and skills for contradictory directives, duplicate "
        "instructions, stale file references, and inconsistent constraints. "
        "Returns a structured report with findings, severity levels, and "
        "suggested actions. Use this when the user asks to check for "
        "configuration conflicts, audit their setup, or verify instruction coherence."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["full", "home", "project", "skills"],
                "description": (
                    "Scope of the audit: 'full' scans all sources, "
                    "'home' scans Hermes home only, 'project' scans project root only, "
                    "'skills' scans skills directory only"
                ),
            },
            "output_format": {
                "type": "string",
                "enum": ["text", "json"],
                "description": "Output format: 'text' for human-readable, 'json' for machine-readable",
            },
        },
        "required": [],
    },
}

NORTHSTAR_STATUS = {
    "name": "northstar_status",
    "description": (
        "Show the current Northstar configuration status: enforcement mode, "
        "protected paths, last audit results, and active policy rules. "
        "Use this to check what Northstar is currently enforcing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "include_last_audit": {
                "type": "boolean",
                "description": "Include the last audit results in the status",
            },
        },
        "required": [],
    },
}
