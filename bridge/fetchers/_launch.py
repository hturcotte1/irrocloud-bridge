"""Chromium launch options shared by the fetcher and discover.

BRIDGE_CHROMIUM_PATH, when set, points at the Chromium binary to use. This is
needed in environments where a pre-installed browser build does not match the
pip playwright version (for example, Claude Code's cloud sandbox provides
/opt/pw-browsers/chromium). On GitHub Actions and normal machines,
`playwright install --with-deps chromium` provides a matching browser and the
variable stays unset.
"""

from __future__ import annotations

import os


def chromium_launch_kwargs() -> dict:
    path = os.environ.get("BRIDGE_CHROMIUM_PATH")
    return {"executable_path": path} if path else {}
