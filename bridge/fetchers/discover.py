"""`python -m bridge.cli discover` — map the real IrroCloud site, read-only.

Built tonight against a fake site; run in the morning against the real one.
Everything it learns lands in discovery/: screenshots, saved HTML, a full
network log (network.jsonl), a sample export, and REPORT.md — the document the
morning session reads before adjusting the browser fetcher's SELECTORS.

Read-only discipline (build brief §2): navigate, read, screenshot, download
one export. Never click anything that might change a setting; the account
pages are visited to READ (looking for API/integration mentions), not touch.
"""

from __future__ import annotations

import difflib
import json
import re
from datetime import UTC, datetime, timedelta

import pandas as pd

from bridge.config import Config
from bridge.errors import AlertError
from bridge.fetchers.irrocloud_browser import (
    SELECTORS,
    UNEXPECTED_STEP_MARKERS,
    USER_AGENT,
    VIEWPORT,
)

STEP_TIMEOUT_MS = 25_000
BODY_CAPTURE_LIMIT = 2 * 1024 * 1024
DEVICE_LINK_HINT = re.compile(r"device|site|field|sensor|station", re.I)
NAV_WORD = re.compile(r"account|setting|user|integration|profile|admin", re.I)
API_WORDS = re.compile(r"\bapi\b|token|key|integration|share|add user", re.I)
TIME_STRING = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}|\b\d{1,2}:\d{2}\s*(am|pm|AM|PM)\b")
SENSOR_LABEL = re.compile(r"SM\d|sensor|12\s*(?:in|\")|18\s*(?:in|\")", re.I)
MAX_DEVICES = 8


def run_discovery(cfg: Config, headless: bool = True) -> int:
    if not cfg.irrocloud_email or not cfg.irrocloud_password:
        print(
            "IRROCLOUD_EMAIL / IRROCLOUD_PASSWORD are not set in .env — "
            "discover needs a login. See NEXT.md."
        )
        return 2

    from playwright.sync_api import sync_playwright

    out = cfg.discovery_dir
    out.mkdir(parents=True, exist_ok=True)
    network: list[dict] = []
    report: dict = {
        "started_utc": datetime.now(UTC).isoformat(),
        "base_url": cfg.irrocloud_url,
        "nav_links": [],
        "devices": [],
        "export": {},
        "account_findings": [],
        "timezone_evidence": {},
        "problems": [],
    }

    def shot(page, name: str) -> str:
        path = out / f"{name}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        return path.name

    def on_request(request):
        entry = {
            "type": "request",
            "method": request.method,
            "url": request.url,
        }
        # Never record credentials: scrub any request body that mentions a
        # password (the login POST).
        try:
            body = request.post_data
        except Exception:  # noqa: BLE001
            body = None
        if body:
            entry["body"] = "[scrubbed: contains credentials]" if (
                "password" in body.lower() or "login" in request.url.lower()
            ) else body[:2000]
        network.append(entry)

    def on_response(response):
        entry = {
            "type": "response",
            "status": response.status,
            "url": response.url,
            "content_type": response.headers.get("content-type", ""),
        }
        try:
            body = response.body()
            entry["size"] = len(body)
            if (
                len(body) < BODY_CAPTURE_LIMIT
                and any(t in entry["content_type"] for t in ("json", "csv"))
                and "login" not in response.url.lower()
            ):
                entry["body"] = body.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        network.append(entry)

    from bridge.fetchers._launch import chromium_launch_kwargs

    exit_code = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=headless, slow_mo=200, **chromium_launch_kwargs()
        )
        context = browser.new_context(viewport=VIEWPORT, user_agent=USER_AGENT)
        page = context.new_page()
        page.on("request", on_request)
        page.on("response", on_response)

        try:
            # 1. Login page.
            page.goto(cfg.irrocloud_url, timeout=STEP_TIMEOUT_MS)
            shot(page, "01-login-page")
            page.fill(SELECTORS["login_email"], cfg.irrocloud_email)
            page.fill(SELECTORS["login_password"], cfg.irrocloud_password)
            page.click(SELECTORS["login_submit"])
            page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)

            # 2. Anything unexpected → stop and report in plain English.
            content_lower = (page.content() or "").lower()
            for marker in UNEXPECTED_STEP_MARKERS:
                if marker in content_lower:
                    shot(page, "02-unexpected-step")
                    raise AlertError(
                        "After signing in, IrroCloud showed an extra step (the "
                        f"page mentions '{marker}') — probably a code sent by "
                        "text/email, a captcha, or a terms dialog. A human needs "
                        "to do this step once in a normal browser, then re-run "
                        "discover. Screenshot: discovery/02-unexpected-step.png"
                    )
            if page.locator(SELECTORS["logged_in_marker"]).count() == 0:
                shot(page, "02-login-failed")
                raise AlertError(
                    "The login did not reach a logged-in page — wrong email or "
                    "password, or the site changed. Screenshot: "
                    "discovery/02-login-failed.png"
                )

            # 3. Landing page: screenshot, save HTML, list the navigation.
            shot(page, "03-landing")
            (out / "landing.html").write_text(page.content(), encoding="utf-8")
            for link in page.locator("a").all()[:80]:
                try:
                    text = (link.inner_text() or "").strip()
                    href = link.get_attribute("href") or ""
                except Exception:  # noqa: BLE001
                    continue
                if text or href:
                    report["nav_links"].append({"text": text[:80], "href": href[:200]})

            # 4. The device list: open each device, record names and sensors.
            device_links = []
            for link in report["nav_links"]:
                if DEVICE_LINK_HINT.search(link["text"] or "") or DEVICE_LINK_HINT.search(
                    link["href"] or ""
                ):
                    device_links.append(link)
            if device_links:
                page.goto(
                    page.url if not device_links[0]["href"] else _absolute(
                        page.url, device_links[0]["href"]
                    ),
                    timeout=STEP_TIMEOUT_MS,
                )
                page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
            candidates = []
            for link in page.locator("a").all():
                try:
                    text = (link.inner_text() or "").strip()
                    href = link.get_attribute("href") or ""
                except Exception:  # noqa: BLE001
                    continue
                if href and text and not NAV_WORD.search(text) and text.lower() not in (
                    "devices", "log out", "logout"
                ):
                    candidates.append((text, href))
            seen = set()
            device_pages = []
            for text, href in candidates:
                if href in seen or len(device_pages) >= MAX_DEVICES:
                    continue
                seen.add(href)
                device_pages.append((text, href))
            for index, (text, href) in enumerate(device_pages, start=1):
                try:
                    page.goto(_absolute(page.url, href), timeout=STEP_TIMEOUT_MS)
                    page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
                    shot(page, f"04-device-{index}")
                    body_text = page.inner_text("body")[:8000]
                    sensors = sorted(set(SENSOR_LABEL.findall(body_text)))[:12]
                    times = TIME_STRING.findall(body_text)
                    report["devices"].append(
                        {
                            "link_text": text,
                            "href": href,
                            "title": page.title(),
                            "sensor_labels_seen": sensors,
                            "time_strings_seen": [t if isinstance(t, str) else t
                                                  for t in times][:5],
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    report["problems"].append(f"could not open device '{text}': {exc}")
                page.go_back(timeout=STEP_TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)

            # 5. One export from the FIRST device only (fetch once, never poll).
            if device_pages:
                text, href = device_pages[0]
                page.goto(_absolute(page.url, href), timeout=STEP_TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
                end = datetime.now(UTC).date()
                start = end - timedelta(days=3)
                try:
                    if page.locator(SELECTORS["date_start"]).count():
                        page.fill(SELECTORS["date_start"], start.isoformat())
                        page.locator(SELECTORS["date_start"]).first.dispatch_event("change")
                    if page.locator(SELECTORS["date_end"]).count():
                        page.fill(SELECTORS["date_end"], end.isoformat())
                        page.locator(SELECTORS["date_end"]).first.dispatch_event("change")
                    control = page.locator(SELECTORS["export_control"])
                    if control.count():
                        with page.expect_download(
                            timeout=STEP_TIMEOUT_MS
                        ) as download_info:
                            control.first.click()
                        sample = out / "sample-export.csv"
                        download_info.value.save_as(str(sample))
                        first_lines = sample.read_text(
                            errors="replace"
                        ).splitlines()[:10]
                        report["export"] = {
                            "device": text,
                            "saved_as": "discovery/sample-export.csv",
                            "first_lines": first_lines,
                            "date_range_tried": f"{start} → {end}",
                        }
                    else:
                        report["problems"].append(
                            "no Export/Download/CSV control found on the first "
                            "device page"
                        )
                except Exception as exc:  # noqa: BLE001
                    report["problems"].append(f"export attempt failed: {exc}")
                    shot(page, "05-export-failed")

            # 6. Account/settings pages — read only, looking for API mentions.
            for link in report["nav_links"]:
                if NAV_WORD.search(link["text"] or "") or NAV_WORD.search(link["href"] or ""):
                    try:
                        page.goto(_absolute(page.url, link["href"]), timeout=STEP_TIMEOUT_MS)
                        page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
                        shot(page, f"06-{_slugify(link['text'] or 'account')}")
                        body_text = page.inner_text("body")
                        for line in body_text.splitlines():
                            if API_WORDS.search(line):
                                report["account_findings"].append(
                                    {"page": link["text"], "line": line.strip()[:200]}
                                )
                    except Exception as exc:  # noqa: BLE001
                        report["problems"].append(
                            f"could not read page '{link['text']}': {exc}"
                        )

            # 7. Log out politely.
            try:
                logout = page.locator(SELECTORS["logout"])
                if logout.count():
                    logout.first.click()
                    page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)
            except Exception:  # noqa: BLE001
                report["problems"].append("logout link not found or failed")

        except AlertError as exc:
            report["problems"].append(exc.reason)
            print(f"DISCOVERY STOPPED: {exc.reason}")
            exit_code = 3
        finally:
            browser.close()

    # Timezone evidence: our clock vs whatever the pages and export showed.
    now_utc = datetime.now(UTC)
    report["timezone_evidence"] = {
        "our_clock_utc": now_utc.isoformat(),
        "our_clock_boise": pd.Timestamp(now_utc).tz_convert(cfg.tz).isoformat(),
        "on_page_time_strings": [
            t for d in report["devices"] for t in d.get("time_strings_seen", [])
        ][:8],
        "export_newest_timestamp": (
            report["export"].get("first_lines", [""])[-1].split(",")[0]
            if report["export"].get("first_lines")
            else None
        ),
        "how_to_read": (
            "If the on-page/export times match the Boise clock, exports are "
            "local time (the bridge's default assumption). If they match UTC, "
            "set TIMEZONE=UTC in .env."
        ),
    }

    # 8. Write the artifacts.
    with open(out / "network.jsonl", "w", encoding="utf-8") as fh:
        for entry in network:
            fh.write(json.dumps(entry) + "\n")
    (out / "REPORT.md").write_text(_render_report(cfg, report), encoding="utf-8")
    print(f"discovery: wrote {out / 'REPORT.md'} ({len(network)} network entries)")
    if report["problems"]:
        print("problems found:")
        for problem in report["problems"]:
            print(f"  - {problem}")
    return exit_code


def _absolute(current_url: str, href: str) -> str:
    from urllib.parse import urljoin

    return urljoin(current_url, href)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "page"


def _guess_mapping(cfg: Config, report: dict) -> list[str]:
    lines = []
    device_names = [d["link_text"] for d in report["devices"]]
    for field in cfg.fields:
        match = difflib.get_close_matches(field.name, device_names, n=1, cutoff=0.3)
        guess = match[0] if match else "(no close match)"
        lines.append(f"- `{field.key}` → best guess: **{guess}**")
    return lines


def _render_report(cfg: Config, report: dict) -> str:
    lines = [
        "# IrroCloud discovery report",
        "",
        f"Run at {report['started_utc']} against {report['base_url']}.",
        "This file is committed; screenshots/HTML/network logs stay local "
        "(gitignored) because they can contain account details.",
        "",
        "## Devices seen",
        "",
    ]
    for device in report["devices"]:
        lines.append(
            f"- **{device['link_text']}** (href `{device['href']}`, title "
            f"'{device['title']}'); sensor labels: "
            f"{', '.join(device['sensor_labels_seen']) or 'none seen'}"
        )
    lines += ["", "## Best-guess device → field mapping (CONFIRM BY HAND)", ""]
    lines += _guess_mapping(cfg, report)
    lines += ["", "## Export", ""]
    if report["export"]:
        lines.append(f"- Worked on device **{report['export']['device']}**, "
                     f"range {report['export']['date_range_tried']}.")
        lines.append(f"- Saved as `{report['export']['saved_as']}` (gitignored).")
        lines.append("- First lines:")
        lines += [f"  ```", *[f"  {line}" for line in report["export"]["first_lines"]],
                  "  ```"]
    else:
        lines.append("- No export was captured — see problems below.")
    lines += ["", "## Timezone evidence", ""]
    for key, value in report["timezone_evidence"].items():
        lines.append(f"- {key}: {value}")
    lines += ["", "## Account/settings findings (API, tokens, integrations)", ""]
    if report["account_findings"]:
        for finding in report["account_findings"]:
            lines.append(f"- {finding['page']}: {finding['line']}")
    else:
        lines.append("- Nothing mentioning API/token/integration was seen.")
    lines += ["", "## Navigation links seen", ""]
    for link in report["nav_links"][:40]:
        lines.append(f"- [{link['text'] or '(no text)'}]({link['href']})")
    lines += ["", "## Problems", ""]
    if report["problems"]:
        lines += [f"- {p}" for p in report["problems"]]
    else:
        lines.append("- None.")
    lines += [
        "",
        "## What the morning session does with this",
        "",
        "1. Fill `irrocloud_device_name` for each field in fields.json from the "
        "mapping above (exact names).",
        "2. Adjust SELECTORS in bridge/fetchers/irrocloud_browser.py if any "
        "step failed.",
        "3. If network.jsonl shows a clean data request (JSON/CSV), consider "
        "STRATEGY='request' with DATA_REQUEST_TEMPLATE.",
        "4. Confirm the export timezone and set TIMEZONE in .env if it is not "
        "Boise local.",
    ]
    return "\n".join(lines) + "\n"
