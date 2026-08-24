"""Chromium launch options shared by the fetcher and discover.

BRIDGE_CHROMIUM_PATH, when set, points at the Chromium binary to use. This is
needed in environments where a pre-installed browser build does not match the
pip playwright version (for example, Claude Code's cloud sandbox provides
/opt/pw-browsers/chromium). On GitHub Actions and normal machines,
`playwright install --with-deps chromium` provides a matching browser and the
variable stays unset.

HTTPS_PROXY, when set, is passed to Chromium as its proxy server — unlike
curl, Chromium does not read the environment variable on its own. Claude
Code's cloud sandbox routes all outbound HTTPS through such a proxy (and
pre-trusts its CA in the browser's NSS store); everywhere else the variable
is normally unset and nothing changes. NO_PROXY becomes the bypass list so
localhost test servers stay direct.

install_context_routing() goes one step further, for the same sandbox: its
egress passes some hosts' TLS straight through, and that path resets
Chromium's TLS handshake (netlog: ECONNRESET right after the ClientHello)
while accepting Node's. Routing every page request through the context's
API request stack — Playwright's route.fetch() — sidesteps the browser's
TLS entirely and shares the cookie jar with the page, so login sessions
still work. It activates only when HTTPS_PROXY is set (force it on or off
with BRIDGE_ROUTE_VIA_REQUEST=1/0); on GitHub Actions and normal machines
nothing changes.
"""

from __future__ import annotations

import os


def chromium_launch_kwargs() -> dict:
    kwargs: dict = {}
    path = os.environ.get("BRIDGE_CHROMIUM_PATH")
    if not path:
        # The Claude sandbox pre-installs a browser under
        # PLAYWRIGHT_BROWSERS_PATH with a "chromium" symlink, but its build
        # number rarely matches the pip playwright's pin — point straight at
        # the symlink so the CLI works there without any manual export.
        browsers_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if browsers_dir:
            candidate = os.path.join(browsers_dir, "chromium")
            if os.path.exists(candidate):
                path = candidate
    if path:
        kwargs["executable_path"] = path
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        kwargs["proxy"] = {"server": proxy}
        bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
        if bypass:
            kwargs["proxy"]["bypass"] = bypass
    return kwargs


def _routing_enabled() -> bool:
    override = os.environ.get("BRIDGE_ROUTE_VIA_REQUEST")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes")
    return bool(os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"))


def install_context_routing(context) -> None:
    """Serve the page's requests via the context's API request stack (see
    module docstring). Redirect chains are followed inside route.fetch and
    the final response fulfilled — a fulfilled 30x makes Chromium follow the
    hop itself, outside interception, which is the very path being avoided.
    The browser's address would then lag behind the redirect, and a form
    with no action attribute would post to the wrong URL — so redirected
    HTML navigations get the final URL patched in via history.replaceState."""
    if not _routing_enabled():
        return

    import json

    def handle(route):
        try:
            response = route.fetch()
            body = None
            if (
                route.request.is_navigation_request()
                and response.url != route.request.url
                and "text/html" in response.headers.get("content-type", "")
            ):
                fix = (
                    "<script>history.replaceState(null,'',"
                    f"{json.dumps(response.url)})</script>"
                ).encode()
                body = response.body() + fix
            if body is None:
                route.fulfill(response=response)
            else:
                headers = {
                    k: v
                    for k, v in response.headers.items()
                    if k.lower()
                    not in ("content-length", "content-encoding", "transfer-encoding")
                }
                route.fulfill(status=response.status, headers=headers, body=body)
        except Exception:  # noqa: BLE001 — surface as a normal network error
            try:
                route.abort("connectionfailed")
            except Exception:  # noqa: BLE001 — page may already be gone
                pass

    context.route("**/*", handle)
