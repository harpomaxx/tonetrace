"""Native Qt GUI package for notegrabber."""

from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    """Run the GUI entry point."""

    from .app import main as app_main

    return app_main()
