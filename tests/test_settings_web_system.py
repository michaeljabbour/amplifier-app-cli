"""Status, maintenance jobs, keys and provider schemas behind `amplifier settings`."""

from __future__ import annotations

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


def test_mask_never_shows_short_values_and_keeps_ends_of_long_ones() -> None:
    assert system.mask("sk-ant-0123456789abcdef") == "sk-ant…cdef"
    assert "abc" not in system.mask("abc")
    assert set(system.mask("short-secret")) == {"•"}


def test_reveal_only_for_keys_the_page_lists(home: Path) -> None:
    (home / "keys.env").write_text('MY_KEY="secret-value"\nOTHER="x"\n')
    assert system.reveal_key("MY_KEY", {"MY_KEY"}) == "secret-value"
    with pytest.raises(system.SettingsError):
        system.reveal_key("OTHER", {"MY_KEY"})  # not offered by the page
    with pytest.raises(system.SettingsError):
        system.reveal_key("../etc", {"../etc"})


def test_jobs_only_build_allow_listed_commands() -> None:
    base = [sys.executable, "-m", "amplifier_app_cli"]
    assert system.Jobs.argv("update-check", {}) == [*base, "update", "--check-only"]
    assert system.Jobs.argv("reset-plan", {"remove": ["cache", "registry", "rm -rf /"]}) == [
        *base, "reset", "--remove", "cache,registry", "-y", "--dry-run",
    ]  # fmt: skip
    assert system.Jobs.argv("reset", {"full": True}) == [*base, "reset", "--full", "-y"]
    with pytest.raises(system.SettingsError):
        system.Jobs.argv("reset", {"remove": []})
    with pytest.raises(system.SettingsError):
        system.Jobs.argv("shell", {})


def test_reset_categories_report_sizes(home: Path) -> None:
    (home / "cache").mkdir()
    (home / "cache" / "blob").write_bytes(b"x" * 2048)
    cats = {c["name"]: c for c in system.reset_info()["categories"]}
    assert cats["cache"]["bytes"] == 2048 and cats["cache"]["default_remove"]
    assert not cats["projects"]["default_remove"]


def test_provider_schema_comes_only_from_the_providers_declarations(monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli import provider_loader

    declared = {
        "provider-demo": {
            "config_fields": [
                {"id": "api_key", "display_name": "API Key", "field_type": "secret", "prompt": "Key", "env_var": "X"},
                {"id": "effort", "display_name": "Effort", "field_type": "choice", "choices": ["low", "high"], "default": None},
            ]
        },
    }
    calls: list[tuple[str, object]] = []

    def fake_info(module_id: str, *, source_path: Path | None = None) -> dict | None:
        calls.append((module_id, source_path))
        return declared.get(module_id)

    monkeypatch.setattr(provider_loader, "get_provider_info", fake_info)

    schema = system.provider_schema("provider-demo", "git+https://example.invalid/demo")
    assert schema == {
        "fields": [
            {"id": "api_key", "display_name": "API Key", "field_type": "secret", "prompt": "Key"},
            {"id": "effort", "display_name": "Effort", "field_type": "choice", "choices": ["low", "high"]},
        ],
        "origin": "provider",
    }
    assert system.provider_schema("provider-unknown") == {"fields": [], "origin": "none"}
    system.provider_schema("provider-demo", "file:///tmp/demo-checkout")
    assert calls[-1] == ("provider-demo", Path("/tmp/demo-checkout"))  # a local checkout loads from its root


def test_status_flags_missing_keys(home: Path, tmp_path: Path) -> None:
    (home / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "max_iterations": 200,
                "config": {"providers": [{"id": "a", "module": "provider-anthropic", "config": {"api_key": "${MY_KEY}", "base_url": "${UNSET_BASE_URL}"}}]},
            }
        )
    )
    proj = tmp_path / "p" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))
    routing = RoutingService(settings)
    st = build_state(settings, routing)["status"]
    assert st["providers"][0]["missing_keys"] == ["MY_KEY"]
    assert st["routing"]["matrix"] == "balanced"


def test_environment_value_shadowing_a_saved_key_is_flagged(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Amplifier never overrides an exported variable, so a stale one silently wins."""
    (home / "keys.env").write_text('MY_KEY="saved-good-key-0123456789"\n')
    monkeypatch.setenv("MY_KEY", "stale-shell-key-9876543210")
    (home / "settings.yaml").write_text(
        yaml.safe_dump({"config": {"providers": [{"id": "a", "module": "provider-anthropic", "config": {"api_key": "${MY_KEY}"}}]}})
    )
    proj = tmp_path / "p2" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))
    state = build_state(settings, RoutingService(settings))
    key = next(k for k in state["keys"] if k["name"] == "MY_KEY")
    assert key["shadowed"] and key["stored"]
    assert state["status"]["providers"][0]["shadowed_keys"] == ["MY_KEY"]
    assert "stale-shell" not in str(state) and "saved-good" not in str(state)  # neither value leaks


def test_notify_test_needs_a_channel(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMPLIFIER_NTFY_TOPIC", raising=False)
    with pytest.raises(system.SettingsError):
        system.notify_test({})

