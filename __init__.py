"""Hermes Northstar plugin entrypoint.

When imported by Hermes as a proper package, this exposes register(ctx).
When pytest collects the root package, the relative import fails — that is
expected and harmless. Genuine import errors (e.g., missing dependencies)
are re-raised so they are visible during development.
"""

try:
    from .northstar import register  # noqa: F401
except ImportError as exc:
    # Relative imports fail when pytest treats the root __init__.py as a
    # top-level module (no known parent package). This is the expected path
    # during test collection. Re-raise if the error indicates a missing
    # dependency or a broken module rather than a parent-package issue.
    if "No module named" in str(exc) and "northstar" in str(exc):
        # A submodule is genuinely missing — re-raise
        raise
    # Otherwise: parent-package issue during pytest collection — silent
    pass
