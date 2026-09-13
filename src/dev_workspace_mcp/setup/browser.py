"""Explicit browser-launch boundary for setup documentation actions."""

from __future__ import annotations

import webbrowser


class SetupBrowserError(RuntimeError):
    """Safe failure while opening a non-secret setup URL."""


def open_setup_url(url: str, *, no_browser: bool) -> bool:
    """Open a reviewed documentation URL unless browser use was disabled."""

    if no_browser:
        return False
    try:
        opened = webbrowser.open(url, new=2)
    except webbrowser.Error as exc:
        raise SetupBrowserError("Could not open the external documentation URL") from exc
    if not opened:
        raise SetupBrowserError("The system browser did not accept the documentation URL")
    return True


__all__ = ["SetupBrowserError", "open_setup_url"]
