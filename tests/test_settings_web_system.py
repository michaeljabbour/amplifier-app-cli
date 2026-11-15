"""Status, keys and provider schemas behind `amplifier settings`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from amplifier_app_cli import settings_web_system as system
from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.settings_web_routing import RoutingService


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from amplifier_app_cli.commands import routing

    def no_fetch() -> None:
        raise AssertionError("the settings page must never fetch the routing bundle")

    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", no_fetch)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AMPLIFIER_HOME", str(h))
    monkeypatch.delenv("MY_KEY", raising=False)
    return h


def test_mask_shows_at_most_a_quarter() -> None:
    assert system.mask("sk-ant-0123456789abcdef") == "sk-…ef"  # 23 characters -> 5 shown
    for n in (16, 17, 40, 200):
        v = "".join(chr(65 + i % 26) for i in range(n))
        shown = len(system.mask(v)) - 1  # minus the ellipsis
        assert shown <= n // 4 and shown <= 10
    assert "abc" not in system.mask("abc")
    assert set(system.mask("fifteen-chars-x")) == {"•"}


@pytest.mark.parametrize("name", ["api_key", "apikey", "apiKey", "access_token", "aws_secret_access_key", "private_key", "subscription_key", "secret_key", "credentials", "credential", "password", "passphrase", "token", "PGPASSWORD", "NGROK_AUTHTOKEN", "dbpassword", "accesstoken"])
def test_secret_names_match(name: str) -> None:
    assert system.SECRET_FIELD.search(name)
    assert system.SECRET_NAME.search(name.upper())


@pytest.mark.parametrize("name", ["turkey", "max_tokens", "max_output_tokens", "monkey", "keyboard", "base_url", "default_model", "tokens", "priority"])
def test_non_secret_names_do_not_match(name: str) -> None:
    assert not system.SECRET_FIELD.search(name)
    assert not system.SECRET_NAME.search(name.upper())


def test_env_refs_use_the_runtime_pattern() -> None:
    assert system.env_refs("${A}/x/${B:fallback}/${C:}") == ["A", "B", "C"]
    assert system.env_refs("plain") == []


def test_defaults_come_from_the_cli() -> None:
    from amplifier_app_cli.lib.bundle_loader.discovery import DEFAULT_BUNDLE
    from amplifier_app_cli.utils.settings_manager import DEFAULT_SETTINGS

    d = system.defaults()
    assert d["bundle"] == DEFAULT_BUNDLE
    assert d["update_check_hours"] == DEFAULT_SETTINGS["updates"]["check_frequency_hours"]
    assert d["matrix"] == "balanced"


def test_reveal_only_for_keys_the_page_lists(home: Path) -> None:
    (home / "keys.env").write_text('MY_KEY="secret-value"\nOTHER="x"\n')
    assert system.reveal_key("MY_KEY", {"MY_KEY"}) == "secret-value"
    with pytest.raises(system.SettingsError):
        system.reveal_key("OTHER", {"MY_KEY"})  # not offered by the page
    with pytest.raises(system.SettingsError):
        system.reveal_key("../etc", {"../etc"})


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
        if module_id == "provider-broken":
            raise RuntimeError("source not activated")
        return declared.get(module_id)

    monkeypatch.setattr(provider_loader, "get_provider_info", fake_info)

    schema = system.provider_schema("provider-demo")
    assert schema == {
        "fields": [
            {"id": "api_key", "display_name": "API Key", "field_type": "secret", "prompt": "Key"},
            {"id": "effort", "display_name": "Effort", "field_type": "choice", "choices": ["low", "high"]},
        ],
        "origin": "provider",
    }
    assert system.provider_schema("provider-unknown") == {"fields": [], "origin": "none"}
    assert system.provider_schema("provider-broken") == {"fields": [], "origin": "none", "error": "RuntimeError: source not activated"}
    assert all(path is None for _, path in calls)  # always the loader's own source selection


def _settings(home: Path, tmp_path: Path, doc: dict) -> AppSettings:
    (home / "settings.yaml").write_text(yaml.safe_dump(doc))
    proj = tmp_path / "p" / ".amplifier"
    proj.mkdir(parents=True, exist_ok=True)
    return AppSettings(SettingsPaths(home / "settings.yaml", proj / "settings.yaml", proj / "settings.local.yaml"))


def _status(settings: AppSettings, keys: list[dict] | None = None) -> dict:
    return system.status(settings, RoutingService(settings), keys or [], settings.get_merged_settings())


def test_status_flags_missing_keys_and_uses_cli_defaults(home: Path, tmp_path: Path) -> None:
    cfg = {"api_key": "${MY_KEY}", "base_url": "${UNSET_BASE_URL}"}
    st = _status(_settings(home, tmp_path, {"config": {"providers": [{"id": "a", "module": "provider-anthropic", "config": cfg}]}}))
    assert st["providers"][0]["missing_keys"] == ["MY_KEY"]
    assert st["routing"]["matrix"] == system.defaults()["matrix"]
    assert st["bundle"]["active"] == system.defaults()["bundle"]  # (no matrices cached: never fetched)


def test_default_provider_is_the_lowest_priority(home: Path, tmp_path: Path) -> None:
    providers = [
        {"id": "first", "module": "provider-openai", "config": {"priority": 5}},
        {"id": "best", "module": "provider-anthropic", "config": {"priority": 1}},
        {"id": "unset", "module": "provider-ollama"},  # 100, as the CLI assumes
    ]
    st = _status(_settings(home, tmp_path, {"config": {"providers": providers}}))
    assert {p["name"]: p["default"] for p in st["providers"]} == {"first": False, "best": True, "unset": False}


def test_environment_value_shadowing_a_saved_key_is_flagged(home: Path, tmp_path: Path) -> None:
    """Amplifier never overrides an exported variable, so a stale one silently wins."""
    settings = _settings(home, tmp_path, {"config": {"providers": [{"id": "a", "module": "provider-anthropic", "config": {"api_key": "${MY_KEY:}"}}]}})
    st = _status(settings, [{"name": "MY_KEY", "stored": True, "in_env": True, "shadowed": True}])
    assert st["providers"][0]["shadowed_keys"] == ["MY_KEY"] and st["providers"][0]["missing_keys"] == []


@pytest.mark.parametrize(
    ("field", "value", "secret"),
    [
        ("api_key", "sk-live-123", True),
        ("api_key", "${MY_KEY}", False),
        ("webhook", "https://user:hunter2@hooks.example/x", True),
        ("webhook", "https://user:${PASS}@hooks.example/x", False),
        ("base_url", "https://api.example/v1", False),
        ("base_url", "https://me@api.example/v1", False),
    ],
)
def test_secret_values_include_url_passwords(field: str, value: str, secret: bool) -> None:
    assert system.is_secret_value(field, value) is secret
