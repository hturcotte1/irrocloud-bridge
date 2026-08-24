"""Playwright fetcher for the real IrroCloud website. READ-ONLY by rule.

Never change a setting, threshold, alert, device name, password, or user. If a
control might change state, do not click it (build brief §2). This fetcher
logs in once, exports each field's CSV once, and logs out.

============================================================================
MORNING-ADJUSTMENT SECTION — everything site-specific lives right here.
After the real `discover` run, adjust SELECTORS (and STRATEGY, below) to
match discovery/REPORT.md. Nothing below this section should need editing.
============================================================================
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from bridge.config import Config, FieldConfig
from bridge.errors import AlertError
from bridge.fetchers.base import raw_export_path
from bridge.parse import validate_export_file

SELECTORS = {
    # The fake-IrroCloud test site satisfies these; the real site's values
    # come from the morning discovery run.
    "login_email": 'input[type="email"], input[name="email"]',
    "login_password": 'input[type="password"]',
    "login_submit": 'button[type="submit"], input[type="submit"]',
    # Something that only exists once logged in:
    "logged_in_marker": 'a[href*="devices"], a[href*="logout"]',
    "device_list_link": 'a[href*="devices"]',
    "date_start": 'input[name="start"], input#start',
    "date_end": 'input[name="end"], input#end',
    "export_control": (
        'a:has-text("Export"), button:has-text("Export"), '
        'a:has-text("Download"), a:has-text("CSV")'
    ),
    "logout": 'a:has-text("Log out"), a:has-text("Logout"), a[href*="logout"]'
}

# "click": drive the page's own export control with download handling (works
#          everywhere, more moving parts).
# "request": replay the site's own data request with the logged-in context's
#          cookies (fewer moving parts — preferred IF discovery finds one).
#          The morning session switches this after reading REPORT.md and fills
#          DATA_REQUEST_TEMPLATE with the URL pattern discovery recorded,
#          using {device}, {start}, {end} placeholders.
STRATEGY = "click"
DATA_REQUEST_TEMPLATE: str | None = None

# ==========================================================================
# End of the morning-adjustment section.
# ==========================================================================

# Words that mean the login flow grew an unexpected step. Stop, do not guess.
UNEXPECTED_STEP_MARKERS = [
    "verification code",
    "verify it's you",
    "enter the code",
    "two-factor",
    "captcha",
    "i am not a robot",
    "terms of service",
    "accept the terms",
]

STEP_TIMEOUT_MS = 20_000
RETRY_BACKOFF_S = (2.0, 4.0)  # two retries per step
TOTAL_BUDGET_S = 8 * 60
SECONDS_BETWEEN_FIELDS = 3.0
VIEWPORT = {"width": 1366, "height": 768}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class IrroCloudBrowserFetcher:
    """Logs in lazily on the first fetch; one export per field; logs out on
    close. Every failure path screenshots first, then raises AlertError."""

    def __init__(
        self,
        cfg: Config,
        headless: bool = True,
        slow_mo_ms: int = 150,
        sleep=time.sleep,
    ):
        if not cfg.irrocloud_email or not cfg.irrocloud_password:
            raise AlertError(
                "IRROCLOUD_EMAIL / IRROCLOUD_PASSWORD are not set in .env, so the "
                "browser fetcher cannot log in."
            )
        self.cfg = cfg
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms
        self.sleep = sleep
        self._playwright = None
        self._browser = None
        self._page = None
        self._started_at: float | None = None
        self._fetched_once = False

    # ---- plumbing -------------------------------------------------------
    def _shot(self, name: str) -> str:
        """Screenshot into logs/screenshots/ (attached to alert emails)."""
        self.cfg.screenshots_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%H%M%S")
        path = self.cfg.screenshots_dir / f"{stamp}-{name}.png"
        try:
            if self._page is not None:
                self._page.screenshot(path=str(path), full_page=True)
                return str(path)
        except Exception:  # noqa: BLE001 — a failed screenshot must not mask the error
            pass
        return ""

    def _check_budget(self) -> None:
        if self._started_at and time.monotonic() - self._started_at > TOTAL_BUDGET_S:
            raise AlertError(
                "The IrroCloud fetch has been running longer than its 8-minute "
                "budget; stopping so the site is not hammered.",
                screenshots=[self._shot("budget-exceeded")],
            )

    def _retry(self, step_name: str, fn):
        """Two retries with backoff per step (build brief §7)."""
        last_exc: Exception | None = None
        for attempt in range(3):
            self._check_budget()
            try:
                return fn()
            except AlertError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < 2:
                    self.sleep(RETRY_BACKOFF_S[attempt])
        raise AlertError(
            f"The step '{step_name}' kept failing on the IrroCloud site: "
            f"{last_exc}. The page may have changed since discovery.",
            screenshots=[self._shot(f"failed-{step_name}")],
        )

    # ---- browser lifecycle ---------------------------------------------
    def _ensure_logged_in(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright

        from bridge.fetchers._launch import chromium_launch_kwargs

        self._started_at = time.monotonic()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless, slow_mo=self.slow_mo_ms, **chromium_launch_kwargs()
        )
        context = self._browser.new_context(viewport=VIEWPORT, user_agent=USER_AGENT)
        self._page = context.new_page()
        page = self._page

        def do_login():
            page.goto(self.cfg.irrocloud_url, timeout=STEP_TIMEOUT_MS)
            page.wait_for_selector(SELECTORS["login_email"], timeout=STEP_TIMEOUT_MS)
            page.fill(SELECTORS["login_email"], self.cfg.irrocloud_email)
            page.fill(SELECTORS["login_password"], self.cfg.irrocloud_password)
            page.click(SELECTORS["login_submit"])
            page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)

        self._retry("login", do_login)

        content = (page.content() or "").lower()
        for marker in UNEXPECTED_STEP_MARKERS:
            if marker in content:
                raise AlertError(
                    "IrroCloud showed an unexpected step after login "
                    f"(the page mentions '{marker}'). Stopping rather than "
                    "guessing — a human needs to look at this once.",
                    screenshots=[self._shot("unexpected-login-step")],
                )
        if page.locator(SELECTORS["logged_in_marker"]).count() == 0:
            raise AlertError(
                "The login did not reach a logged-in page (no device list or "
                "log-out link appeared). The password may be wrong or the site "
                "may have changed.",
                screenshots=[self._shot("login-not-logged-in")],
            )

    # ---- fetching -------------------------------------------------------
    def fetch(self, field: FieldConfig, start: date, end: date) -> Path:
        if field.irrocloud_device_name is None:
            raise AlertError(
                f"fields.json has no IrroCloud device name for {field.name} yet "
                "(it is REPLACE_ME). Run `python -m bridge.cli discover` first "
                "and fill it in."
            )
        self._ensure_logged_in()
        if self._fetched_once:
            self.sleep(SECONDS_BETWEEN_FIELDS)  # be gentle: pause between fields
        self._fetched_once = True

        if STRATEGY == "request" and DATA_REQUEST_TEMPLATE:
            return self._fetch_via_request(field, start, end)
        return self._fetch_via_click(field, start, end)

    def _open_device_page(self, field: FieldConfig) -> None:
        page = self._page
        device = field.irrocloud_device_name

        def open_list_and_device():
            page.click(SELECTORS["device_list_link"])
            page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
            link = page.locator(f'a:has-text("{device}")')
            if link.count() == 0:
                raise AlertError(
                    f"No device called '{device}' appears on the IrroCloud device "
                    f"list for {field.name}. The name in fields.json must match "
                    "the site exactly — re-run discover to see the real names.",
                    screenshots=[self._shot("device-not-found")],
                )
            link.first.click()
            page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)

        self._retry(f"open-device-{field.key}", open_list_and_device)

    def _fetch_via_click(self, field: FieldConfig, start: date, end: date) -> Path:
        page = self._page
        self._open_device_page(field)

        def set_dates():
            if page.locator(SELECTORS["date_start"]).count():
                page.fill(SELECTORS["date_start"], start.isoformat())
            if page.locator(SELECTORS["date_end"]).count():
                page.fill(SELECTORS["date_end"], end.isoformat())
                # Fire the change events a real user would.
                page.locator(SELECTORS["date_end"]).first.dispatch_event("change")
            if page.locator(SELECTORS["date_start"]).count():
                page.locator(SELECTORS["date_start"]).first.dispatch_event("change")

        self._retry(f"set-dates-{field.key}", set_dates)

        out_path = raw_export_path(self.cfg, field.key, datetime.now(UTC))

        def download():
            control = page.locator(SELECTORS["export_control"])
            if control.count() == 0:
                raise AlertError(
                    f"No Export/Download/CSV control on the device page for "
                    f"{field.name}. The site layout may have changed.",
                    screenshots=[self._shot("no-export-control")],
                )
            with page.expect_download(timeout=STEP_TIMEOUT_MS) as download_info:
                control.first.click()
            download_info.value.save_as(str(out_path))

        self._retry(f"export-{field.key}", download)
        self._validate(out_path, field)
        return out_path

    def _fetch_via_request(self, field: FieldConfig, start: date, end: date) -> Path:
        """Replay the site's own data request using the logged-in context's
        cookies. Enabled by the morning session via STRATEGY/DATA_REQUEST_TEMPLATE."""
        page = self._page
        url = DATA_REQUEST_TEMPLATE.format(
            device=field.irrocloud_device_name,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        out_path = raw_export_path(self.cfg, field.key, datetime.now(UTC))

        def request():
            response = page.context.request.get(url, timeout=STEP_TIMEOUT_MS)
            if not response.ok:
                raise RuntimeError(f"data request returned HTTP {response.status}")
            out_path.write_bytes(response.body())

        self._retry(f"data-request-{field.key}", request)
        self._validate(out_path, field)
        return out_path

    def _validate(self, path: Path, field: FieldConfig) -> None:
        report = validate_export_file(path)
        if not report.ok:
            first_lines = path.read_text(errors="replace").splitlines()[:5]
            raise AlertError(
                f"The export downloaded for {field.name} does not look like "
                f"sensor data: {'; '.join(report.problems)}.",
                evidence_lines=first_lines,
                screenshots=[self._shot(f"bad-export-{field.key}")],
            )

    def close(self) -> None:
        try:
            if self._page is not None:
                logout = self._page.locator(SELECTORS["logout"])
                if logout.count():
                    logout.first.click()
                    self._page.wait_for_load_state(
                        "networkidle", timeout=STEP_TIMEOUT_MS
                    )
        except Exception:  # noqa: BLE001 — logout is best-effort
            pass
        finally:
            for closer in (self._browser, self._playwright):
                try:
                    if closer is not None:
                        closer.close() if closer is self._browser else closer.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._page = self._browser = self._playwright = None
