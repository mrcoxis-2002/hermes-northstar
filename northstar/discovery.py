"""Instruction source discovery — finds and reads instruction files."""

from pathlib import Path
from typing import Optional


# Known instruction file names that Hermes uses
INSTRUCTION_FILES = {
    "SOUL.md",
    "AGENTS.md",
    "MEMORY.md",
    ".hermes.md",
    "CLAUDE.md",
    "GEMINI.md",
    "CUSTOM_INSTRUCTIONS.md",
    "README.md",
    "BOOT.md",
}

# Hermes config files
CONFIG_FILES = {
    "config.yaml",
    "config.yml",
}

# Skill files
SKILL_FILES = {
    "SKILL.md",
}

# Directory names that indicate archived, backup, or inactive content.
# These are excluded from normal discovery to avoid auditing stale material.
ARCHIVED_DIR_NAMES = {
    ".archive",
    ".curator_backups",
    ".backup",
    ".backups",
    "archive",
    "archived",
    "old",
    "deprecated",
}


def _is_archived_path(path: Path) -> bool:
    """Return True if any component of the path is an archived directory."""
    for part in path.parts:
        if part.lower() in {n.lower() for n in ARCHIVED_DIR_NAMES}:
            return True
    return False


class InstructionSource:
    """A discovered instruction source with its content and metadata."""

    def __init__(self, path: Path, content: str, source_type: str = "file"):
        self.path = path
        self.content = content
        self.source_type = source_type  # "instruction", "config", "skill", "other"
        self.lines = content.splitlines()
        self.line_count = len(self.lines)

    def get_line(self, index: int) -> str:
        """Get a 1-indexed line, or empty string if out of range."""
        if 0 <= index - 1 < len(self.lines):
            return self.lines[index - 1]
        return ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "source_type": self.source_type,
            "line_count": self.line_count,
        }


def classify_source(path: Path) -> str:
    """Classify a file by its name."""
    name = path.name.upper()
    if name in {s.upper() for s in INSTRUCTION_FILES}:
        return "instruction"
    if name in {s.upper() for s in CONFIG_FILES}:
        return "config"
    if name in {s.upper() for s in SKILL_FILES}:
        return "skill"
    if path.suffix.lower() in (".yaml", ".yml", ".json", ".toml"):
        return "config"
    if path.suffix.lower() in (".md", ".txt"):
        return "instruction"
    return "other"


def discover_sources(
    hermes_home: Optional[Path] = None,
    project_root: Optional[Path] = None,
    additional_dirs: Optional[list[str]] = None,
    include_archived: bool = False,
) -> list[InstructionSource]:
    """Discover instruction sources in approved directories.

    Scans:
    - Hermes home directory (SOUL.md, AGENTS.md, MEMORY.md, config.yaml)
    - Project root (.hermes.md, AGENTS.md, CLAUDE.md, etc.)
    - Skills directory
    - Additional directories specified by user

    By default, directories with names matching ARCHIVED_DIR_NAMES are
    excluded (e.g. .archive, .curator_backups). Set include_archived=True
    to include them for diagnostic use.
    """
    sources = []
    dirs_to_scan = []

    # Hermes home
    if hermes_home is None:
        from os import environ
        hermes_home = Path(environ.get("HERMES_HOME", Path.home() / ".hermes"))

    # Scan Hermes home for known files
    for name in INSTRUCTION_FILES | CONFIG_FILES:
        candidate = hermes_home / name
        if candidate.is_file():
            dirs_to_scan.append(candidate)

    # Scan skills directory
    skills_dir = hermes_home / "skills"
    if skills_dir.is_dir():
        for skill_md in skills_dir.rglob("SKILL.md"):
            if not include_archived and _is_archived_path(skill_md):
                continue
            dirs_to_scan.append(skill_md)

    # Project root
    if project_root is None:
        project_root = Path.cwd()

    for name in INSTRUCTION_FILES | {"README.md"}:
        candidate = project_root / name
        if candidate.is_file():
            dirs_to_scan.append(candidate)

    # .hermes/ subdirectory in project
    project_hermes = project_root / ".hermes"
    if project_hermes.is_dir():
        for name in INSTRUCTION_FILES | CONFIG_FILES:
            candidate = project_hermes / name
            if candidate.is_file():
                dirs_to_scan.append(candidate)

    # Additional dirs
    if additional_dirs:
        for d in additional_dirs:
            p = Path(d)
            if not p.is_absolute():
                p = hermes_home / p
            if p.is_dir():
                for f in p.iterdir():
                    if f.is_file() and f.suffix.lower() in (".md", ".yaml", ".yml", ".json", ".txt"):
                        if not include_archived and _is_archived_path(f):
                            continue
                        dirs_to_scan.append(f)

    # Read and deduplicate
    seen = set()
    for p in dirs_to_scan:
        try:
            resolved = p.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            content = p.read_text(encoding="utf-8", errors="replace")
            source_type = classify_source(p)
            sources.append(InstructionSource(p, content, source_type))
        except (OSError, PermissionError):
            pass

    return sources
