"""Season replay: report builds offline from fixtures with honest labeling."""

from __future__ import annotations

import shutil
from pathlib import Path

from bridge.config import load_config
from bridge.replay import build_replay_report

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_cfg(tmp_path):
    root = tmp_path / "repo"
    (root / "tests" / "fixtures").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "fields.json", root / "fields.json")
    shutil.copytree(
        REPO_ROOT / "tests" / "fixtures" / "synthetic",
        root / "tests" / "fixtures" / "synthetic",
    )
    return load_config(root=root, environ={})


def test_replay_builds_from_fixtures_offline(tmp_path):
    cfg = make_cfg(tmp_path)
    path = build_replay_report(cfg, precip_fn=None)  # fully offline
    text = path.read_text()

    # One section per field, in the fixed order.
    for name in ("Cunningham 6", "RV80", "Bennett 1 N", "Whitted"):
        assert f"<h2>{name}" in text
    assert text.index("Cunningham 6") < text.index("RV80") < text.index("Whitted")

    # Self-contained: inline SVG, no external scripts or stylesheets.
    assert "<svg" in text
    assert "<script" not in text
    assert 'src="http' not in text and "link rel" not in text

    # Honest labeling: synthetic source note, rain-unknown labeling, trigger.
    assert "SYNTHETIC" in text
    assert "rain unknown" in text
    assert "placeholder trigger 50 cb" in text
    assert "trigger 50 cb" in text  # the reference line label

    # Whitted's ceiling glitch must appear as an artifact.
    whitted = text.split("<h2>Whitted")[1]
    assert "artifact" in whitted

    # Events and hindsight tables exist with their key columns.
    assert "18\" responded after" in text
    assert "would have said" in text
    assert "actually crossed" in text


def test_replay_marks_all_probe_series(tmp_path):
    cfg = make_cfg(tmp_path)
    path = build_replay_report(cfg, precip_fn=None)
    text = path.read_text()
    # Bold primary + muted context probes + aqua 18" lines all present.
    assert 'stroke="#2a78d6" stroke-width="2.4"' in text
    assert 'stroke="#898781"' in text
    assert 'stroke="#1baf7a"' in text
