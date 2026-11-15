"""LiveEnv: keys.env and login-shell values reach the settings page without a restart."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
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
    """No restart needed: the shell wins, keys.env fills in, a name neither has is removed."""
    from amplifier_app_cli import settings_web_env as live

    (home / "keys.env").write_text('LIVE_KEY="from-keys-env-1234567890"\n')
    shell = {"HOME": str(home), "LIVE_KEY": "from-shell-0987654321"}
    monkeypatch.setattr(live, "login_shell_env", lambda: dict(shell))
    monkeypatch.delenv("LIVE_KEY", raising=False)
    env = live.LiveEnv()
    assert env.refresh({"LIVE_KEY"})
    assert os.environ["LIVE_KEY"] == "from-shell-0987654321"  # the shell wins, as in Amplifier
    del shell["LIVE_KEY"]  # user removes the export from ~/.secrets
    assert env.refresh({"LIVE_KEY"}, force=True)
    assert os.environ["LIVE_KEY"] == "from-keys-env-1234567890"  # falls back to keys.env
    assert not env.refresh({"LIVE_KEY"}, force=True)  # nothing changed this time
    (home / "keys.env").write_text("")
    assert env.refresh({"LIVE_KEY"})
    assert "LIVE_KEY" not in os.environ  # neither has it: gone


def test_login_shell_is_read_at_startup_and_when_forced_only(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web_env as live

    calls = []
    monkeypatch.setattr(live, "login_shell_env", lambda: calls.append(1) or {"HOME": str(home)})
    monkeypatch.delenv("LIVE_KEY", raising=False)
    env = live.LiveEnv()
    env.refresh({"LIVE_KEY"})
    assert len(calls) == 1  # first refresh reads the shell
    env.refresh({"LIVE_KEY"})
    env.refresh({"LIVE_KEY"})
    assert len(calls) == 1  # later unforced refreshes don't
    (home / "keys.env").write_text('LIVE_KEY="saved-elsewhere-1234567890"\n')  # e.g. `amplifier provider use`
    assert env.refresh({"LIVE_KEY"})  # keys.env is still picked up without forcing
    assert os.environ["LIVE_KEY"] == "saved-elsewhere-1234567890"
    assert len(calls) == 1
    env.refresh({"LIVE_KEY"}, force=True)
    assert len(calls) == 2  # forced: read again
    env.refresh({"LIVE_KEY"}, force=True, shell=False)
    assert len(calls) == 2  # shell=False never runs it


def test_shell_reading_can_be_turned_off(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import settings_web_env as live

    monkeypatch.setenv("AMPLIFIER_SETTINGS_SHELL_ENV", "off")
    monkeypatch.setattr(live, "login_shell_env", lambda: pytest.fail("shell read while turned off"))
    env = live.LiveEnv()
    env.refresh(set(), force=True)
    assert "turned off" in (env.info()["shell_error"] or "")
    assert live.where_set("HOME") == []


def test_changes_poll_never_runs_the_shell(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from amplifier_app_cli import settings_web, settings_web_env

    monkeypatch.setattr(settings_web_env, "login_shell_env", lambda: pytest.fail("/api/changes ran the login shell"))
    proj = tmp_path / "p5" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))
    server = SimpleNamespace(settings=settings, routing=RoutingService(settings), env=settings_web_env.LiveEnv())
    first = settings_web.changes(server, {})["fingerprint"]
    (home / "keys.env").write_text('MY_KEY="polled-1234567890"\n')
    assert settings_web.changes(server, {})["fingerprint"] != first  # keys.env still noticed
    assert os.environ["MY_KEY"] == "polled-1234567890"


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
