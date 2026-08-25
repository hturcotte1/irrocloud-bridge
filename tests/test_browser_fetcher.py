"""Browser fetcher + discover against the fake IrroCloud site.

These tests prove the PLUMBING — login, navigation, download handling,
validation, screenshots-on-failure, the discovery report — so the morning's
work against the real site is a matter of adjusting selectors, not writing
code (build brief §6). Skipped with a clear reason when no Chromium can start.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

playwright_api = pytest.importorskip(
    "playwright.sync_api", reason="playwright is not installed"
)

from bridge.config import load_config  # noqa: E402
from bridge.errors import AlertError  # noqa: E402
from bridge.fetchers.discover import run_discovery  # noqa: E402
from bridge.fetchers.irrocloud_browser import IrroCloudBrowserFetcher  # noqa: E402
from bridge.parse import load_irrocloud_csv  # noqa: E402
from tests.fixtures.fake_irrocloud.server import FakeIrroCloud  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_DEVICE_NAMES = {
    "cunningham-6": "SBF Cunningham 6",
    "rv80": "SBF RV80",
    "bennett-1-n": "SBF Bennett 1N",
    "whitted": "SBF Whitted",
}
FAKE_DEVICE_IDS = {
    "cunningham-6": "1",
    "rv80": "2",
    "bennett-1-n": "3",
    "whitted": "4",
}


@pytest.fixture(scope="session", autouse=True)
def chromium_or_skip():
    """Make sure a Chromium can actually launch; otherwise skip the module.
    (In the cloud sandbox the pip playwright build may not match the
    preinstalled browser — BRIDGE_CHROMIUM_PATH points at the working one.)"""
    import os

    from playwright.sync_api import sync_playwright

    from bridge.fetchers._launch import chromium_launch_kwargs

    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True, **chromium_launch_kwargs()).close()
        return
    except Exception:
        fallback = "/opt/pw-browsers/chromium"
        if Path(fallback).exists():
            os.environ["BRIDGE_CHROMIUM_PATH"] = fallback
            try:
                with sync_playwright() as p:
                    p.chromium.launch(headless=True, **chromium_launch_kwargs()).close()
                return
            except Exception:
                pass
        pytest.skip(
            "No launchable Chromium: run `playwright install --with-deps "
            "chromium` (see NEXT.md), then run these tests."
        )


def make_cfg(tmp_path, base_url: str, password: str = "tension123"):
    root = tmp_path / "repo"
    root.mkdir()
    fields_doc = json.loads((REPO_ROOT / "fields.json").read_text())
    for entry in fields_doc["fields"]:
        entry["irrocloud_device_name"] = FAKE_DEVICE_NAMES[entry["key"]]
        entry["irrocloud_device_id"] = FAKE_DEVICE_IDS[entry["key"]]
    (root / "fields.json").write_text(json.dumps(fields_doc))
    return load_config(
        root=root,
        environ={
            "FETCHER": "browser",
            "IRROCLOUD_URL": base_url,
            "IRROCLOUD_EMAIL": "grower@example.com",
            "IRROCLOUD_PASSWORD": password,
        },
    )


def window():
    end = datetime(2026, 8, 24, tzinfo=UTC).date()
    return end - timedelta(days=3), end


def test_login_navigate_download_validate_logout(tmp_path):
    with FakeIrroCloud() as fake:
        cfg = make_cfg(tmp_path, fake.base_url)
        fetcher = IrroCloudBrowserFetcher(cfg, slow_mo_ms=0, sleep=lambda s: None)
        try:
            start, end = window()
            path = fetcher.fetch(cfg.field_by_key("rv80"), start, end)
            second = fetcher.fetch(cfg.field_by_key("whitted"), start, end)
        finally:
            fetcher.close()
        # Raw files landed under data/raw/<field>/ and parse as modern layout.
        assert path.parent.name == "rv80"
        parsed = load_irrocloud_csv(path, "rv80")
        assert parsed.layout == "modern"
        assert parsed.kept_rows > 24
        assert second.parent.name == "whitted"
        # One login for both fields; logout happened on close.
        assert sum("POST /login" in r for r in fake.request_log) == 1
        assert any("GET /logout" in r for r in fake.request_log)


def test_wrong_password_alerts_with_screenshot(tmp_path):
    with FakeIrroCloud() as fake:
        cfg = make_cfg(tmp_path, fake.base_url, password="wrong")
        fetcher = IrroCloudBrowserFetcher(cfg, slow_mo_ms=0, sleep=lambda s: None)
        try:
            start, end = window()
            with pytest.raises(AlertError) as excinfo:
                fetcher.fetch(cfg.field_by_key("rv80"), start, end)
        finally:
            fetcher.close()
        assert "did not reach a logged-in page" in excinfo.value.reason
        assert excinfo.value.screenshots
        assert Path(excinfo.value.screenshots[0]).exists()


def test_unexpected_login_step_stops(tmp_path):
    with FakeIrroCloud(twofa=True) as fake:
        cfg = make_cfg(tmp_path, fake.base_url)
        fetcher = IrroCloudBrowserFetcher(cfg, slow_mo_ms=0, sleep=lambda s: None)
        try:
            start, end = window()
            with pytest.raises(AlertError) as excinfo:
                fetcher.fetch(cfg.field_by_key("rv80"), start, end)
        finally:
            fetcher.close()
        assert "unexpected step" in excinfo.value.reason


def test_unknown_device_name_alerts_before_browser(tmp_path):
    with FakeIrroCloud() as fake:
        cfg = make_cfg(tmp_path, fake.base_url)
        field = cfg.field_by_key("rv80")
        object.__setattr__(field, "irrocloud_device_name", None)
        fetcher = IrroCloudBrowserFetcher(cfg, slow_mo_ms=0, sleep=lambda s: None)
        start, end = window()
        with pytest.raises(AlertError) as excinfo:
            fetcher.fetch(field, start, end)
        assert "discover" in excinfo.value.reason


def test_missing_device_on_site_alerts(tmp_path):
    with FakeIrroCloud() as fake:
        cfg = make_cfg(tmp_path, fake.base_url)
        field = cfg.field_by_key("rv80")
        object.__setattr__(field, "irrocloud_device_id", "999")
        fetcher = IrroCloudBrowserFetcher(cfg, slow_mo_ms=0, sleep=lambda s: None)
        try:
            start, end = window()
            with pytest.raises(AlertError) as excinfo:
                fetcher.fetch(field, start, end)
        finally:
            fetcher.close()
        assert "data-request-rv80" in excinfo.value.reason


def test_missing_device_id_alerts_before_requesting(tmp_path):
    with FakeIrroCloud() as fake:
        cfg = make_cfg(tmp_path, fake.base_url)
        field = cfg.field_by_key("rv80")
        object.__setattr__(field, "irrocloud_device_id", None)
        fetcher = IrroCloudBrowserFetcher(cfg, slow_mo_ms=0, sleep=lambda s: None)
        try:
            start, end = window()
            with pytest.raises(AlertError) as excinfo:
                fetcher.fetch(field, start, end)
        finally:
            fetcher.close()
        assert "irrocloud_device_id" in excinfo.value.reason


def test_discover_maps_the_fake_site(tmp_path):
    with FakeIrroCloud() as fake:
        cfg = make_cfg(tmp_path, fake.base_url)
        rc = run_discovery(cfg)
    assert rc == 0
    out = cfg.discovery_dir
    report = (out / "REPORT.md").read_text()
    for name in FAKE_DEVICE_NAMES.values():
        assert name in report
    assert "sample-export.csv" in report
    assert (out / "sample-export.csv").exists()
    assert "Timestamp,SM1" in (out / "sample-export.csv").read_text()
    assert "API access: contact Irrometer" in report  # account-page finding
    assert (out / "landing.html").exists()
    assert list(out.glob("*.png"))  # screenshots saved

    # The network log exists and the login body is scrubbed.
    entries = [
        json.loads(line) for line in (out / "network.jsonl").read_text().splitlines()
    ]
    assert entries
    login_posts = [
        e for e in entries
        if e["type"] == "request" and e["method"] == "POST" and "login" in e["url"]
    ]
    assert login_posts
    for post in login_posts:
        assert "tension123" not in post.get("body", "")
        assert "scrubbed" in post.get("body", "")


def test_discover_stops_on_twofa(tmp_path):
    with FakeIrroCloud(twofa=True) as fake:
        cfg = make_cfg(tmp_path, fake.base_url)
        rc = run_discovery(cfg)
    assert rc == 3
    report = (cfg.discovery_dir / "REPORT.md").read_text()
    assert "extra step" in report
    assert (cfg.discovery_dir / "02-unexpected-step.png").exists()
