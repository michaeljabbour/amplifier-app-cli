"""Status, maintenance jobs, keys and provider schemas behind `amplifier settings`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

from amplifier_app_cli import settings_web_system as system
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.settings_web import build_state
from amplifier_app_cli.settings_web_routing import RoutingService


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli.commands import routing

    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", lambda: None)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AMPLIFIER_HOME", str(h))
    monkeypatch.delenv("MY_KEY", raising=False)
    return h



def test_diagnose_explains_each_step_and_never_leaks_keys(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli.settings_web_diagnose import diagnose

    (home / "keys.env").write_text('DEMO_KEY="saved-value-abcdef123456"\n')
    monkeypatch.setenv("DEMO_KEY", "shell-value-zyxwvu987654")
    (home / "settings.yaml").write_text(
        yaml.safe_dump({"config": {"providers": [{"id": "demo", "module": "provider-nothing-here", "config": {"api_key": "${DEMO_KEY}", "base_url": "${DEMO_URL}"}}]}})
    )
    proj = tmp_path / "p3" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))
    report = diagnose(settings, "demo")
    steps = {s["name"]: s for s in report["steps"]}
    assert steps["Provider code"]["status"] == "fail"
    assert any("provider install" in t for t in report["tips"])
    assert "DEMO_URL" in steps["Settings"]["summary"]  # unset variable is named
    assert steps["API key"]["status"] == "warn" and "two different values" in steps["API key"]["summary"]
    wheres = [s["where"] for s in steps["API key"]["sources"]]
    assert wheres[0] == "keys.env" and wheres[1].startswith("your ")  # names the shell file when it can
    assert "saved-value" not in str(report) and "shell-value" not in str(report)
    assert diagnose(settings, "missing")["steps"][0]["status"] == "fail"