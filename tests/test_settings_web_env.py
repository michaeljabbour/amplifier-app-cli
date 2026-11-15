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



def test_live_env_follows_the_shell_then_keys_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No restart needed: a new shell value or keys.env value is picked up on refresh."""
    from amplifier_app_cli import settings_web_env as live

    (home / "keys.env").write_text('LIVE_KEY="from-keys-env-1234567890"\n')
    shell = {"HOME": str(home), "LIVE_KEY": "from-shell-0987654321"}
    monkeypatch.setattr(live, "login_shell_env", lambda: dict(shell))
    monkeypatch.delenv("LIVE_KEY", raising=False)
    env = live.LiveEnv()
    assert env.refresh({"LIVE_KEY"}, force=True)
    assert os.environ["LIVE_KEY"] == "from-shell-0987654321"  # the shell wins, as in Amplifier
    del shell["LIVE_KEY"]  # user removes the export from ~/.secrets
    assert env.refresh({"LIVE_KEY"}, force=True)
    assert os.environ["LIVE_KEY"] == "from-keys-env-1234567890"  # falls back to keys.env
    assert not env.refresh({"LIVE_KEY"}, force=True)  # nothing changed this time
    assert not env.refresh({"LIVE_KEY"})  # and refreshes are throttled

def test_change_fingerprint_moves_when_a_file_changes(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from amplifier_app_cli import settings_web, settings_web_env

    monkeypatch.setattr(settings_web_env, "login_shell_env", lambda: {"HOME": str(home)})
    proj = tmp_path / "p4" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))
    server = SimpleNamespace(settings=settings, routing=RoutingService(settings), env=settings_web_env.LiveEnv())
    first = settings_web.changes(server, {})["fingerprint"]
    assert settings_web.changes(server, {})["fingerprint"] == first
    (home / "settings.yaml").write_text("routing:\n  matrix: economy\n")  # e.g. `amplifier routing use economy`
    assert settings_web.changes(server, {})["fingerprint"] != first
