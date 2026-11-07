"""Hermes Northstar plugin entrypoint."""

try:
    from .northstar import register
except ImportError:
    # When imported standalone (e.g., pytest collecting the root package),
    # the relative import fails. This is expected — the plugin is loaded
    # by Hermes as a proper package, not as a standalone module.
    pass
