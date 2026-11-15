"""Tests for the small public API surface used by external settings UIs:

1. ``AppSettings.read_scope`` / ``write_scope`` / ``scope_lock``
2. ``normalize_provider_secrets`` still moving literal secrets to keys.env
   when a provider entry's module can't be resolved
3. ``KeyManager.get_stored``
4. ``commands.routing._discover_matrix_files(fetch=False)``

Every test points ``Path.home()`` at ``tmp_path`` and clears
``AMPLIFIER_HOME`` so nothing ever touches the real ~/.amplifier.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("AMPLIFIER_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        paths=SettingsPaths(
            global_settings=tmp_path / "global" / "settings.yaml",
            project_settings=tmp_path / "project" / "settings.yaml",
            local_settings=tmp_path / "local" / "settings.local.yaml",
        )
    )


def _keys_env(home: Path) -> dict[str, str]:
    from amplifier_app_cli.key_manager import KeyManager

    km = KeyManager()
    return {name: km.get_stored(name) or "" for name in km.stored_keys()}


# ---------------------------------------------------------------------------
# 1. AppSettings public scope I/O
# ---------------------------------------------------------------------------


class TestPublicScopeIO:
    def test_read_write_roundtrip(self, home):
        settings = _make_settings(home)
        assert settings.read_scope("global") == {}

        settings.write_scope("global", {"bundle": {"active": "foundation"}})

        assert settings.read_scope("global") == {"bundle": {"active": "foundation"}}
        assert settings.read_scope("global") == settings._read_scope("global")
        on_disk = yaml.safe_load(
            (home / "global" / "settings.yaml").read_text(encoding="utf-8")
        )
        assert on_disk == {"bundle": {"active": "foundation"}}

    def test_scopes_are_independent(self, home):
        settings = _make_settings(home)
        settings.write_scope("project", {"a": 1})
        assert settings.read_scope("global") == {}
        assert settings.read_scope("project") == {"a": 1}

    def test_scope_lock_is_the_private_lock_file(self, home):
        settings = _make_settings(home)
        lock = settings.scope_lock("global")
        assert lock.lock_file == settings._scope_lock("global").lock_file
        assert lock.lock_file == str(home / "global" / "settings.yaml") + ".lock"

    def test_read_modify_write_under_lock(self, home):
        settings = _make_settings(home)
        settings.write_scope("global", {"keep": True})
        with settings.scope_lock("global"):
            data = settings.read_scope("global")
            data["added"] = 1
            settings.write_scope("global", data)
        assert settings.read_scope("global") == {"keep": True, "added": 1}

    def test_scope_lock_excludes_a_second_holder(self, home):
        from filelock import FileLock, Timeout

        settings = _make_settings(home)
        with settings.scope_lock("global"):
            other = FileLock(settings.scope_lock("global").lock_file, timeout=0)
            with pytest.raises(Timeout):
                other.acquire()

    def test_unavailable_session_scope_raises(self, home):
        settings = _make_settings(home)
        with pytest.raises(ValueError):
            settings.read_scope("session")

    def test_write_scope_normalizes_literal_provider_secrets(self, home):
        """write_scope goes through the same secret funnel as _write_scope."""
        settings = _make_settings(home)
        data = {
            "config": {
                "providers": [
                    {"module": "provider-missing", "config": {"api_key": "sk-lit"}}
                ]
            }
        }
        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=None,
            ),
            patch("amplifier_app_cli.provider_config_utils.console"),
        ):
            settings.write_scope("global", data)
        try:
            raw = (home / "global" / "settings.yaml").read_text(encoding="utf-8")
            assert "sk-lit" not in raw
            assert "${MISSING_API_KEY}" in raw
        finally:
            os.environ.pop("MISSING_API_KEY", None)


# ---------------------------------------------------------------------------
# 2. normalize_provider_secrets: unresolvable provider module
# ---------------------------------------------------------------------------


@pytest.fixture
def unresolvable(home):
    """get_provider_info() -> None for every module; console captured; env
    vars set by KeyManager.save_key() are cleaned up afterwards."""
    before = set(os.environ)
    with (
        patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=None,
        ),
        patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
    ):
        yield mock_console
    for name in set(os.environ) - before:
        os.environ.pop(name, None)


def _normalize(home: Path, providers: list, scope: str = "global") -> dict:
    from amplifier_app_cli.provider_config_utils import normalize_provider_secrets

    scope_settings = {"config": {"providers": providers}}
    normalize_provider_secrets(_make_settings(home), scope_settings, scope)
    return scope_settings


class TestUnresolvableModuleSecrets:
    def test_literal_api_key_moved_to_keys_env(self, home, unresolvable):
        result = _normalize(
            home,
            [{"module": "provider-foo", "id": "foo", "config": {"api_key": "sk-1"}}],
        )
        cfg = result["config"]["providers"][0]["config"]
        assert cfg["api_key"] == "${FOO_API_KEY}"
        assert _keys_env(home) == {"FOO_API_KEY": "sk-1"}

    def test_all_secret_suffixes_case_insensitive(self, home, unresolvable):
        result = _normalize(
            home,
            [
                {
                    "module": "provider-foo",
                    "id": "foo",
                    "config": {
                        "Azure_API_KEY": "a",
                        "access_token": "b",
                        "client_secret": "c",
                        "Password": "d",
                        "base_url": "https://example.invalid",
                        "max_tokens": 4096,
                        "token_budget": "1000",
                    },
                }
            ],
        )
        cfg = result["config"]["providers"][0]["config"]
        assert cfg["Azure_API_KEY"] == "${FOO_AZURE_API_KEY}"
        assert cfg["access_token"] == "${FOO_ACCESS_TOKEN}"
        assert cfg["client_secret"] == "${FOO_CLIENT_SECRET}"
        assert cfg["Password"] == "${FOO_PASSWORD}"
        # Untouched: not secret-looking names, or not strings.
        assert cfg["base_url"] == "https://example.invalid"
        assert cfg["max_tokens"] == 4096
        assert cfg["token_budget"] == "1000"
        assert _keys_env(home) == {
            "FOO_AZURE_API_KEY": "a",
            "FOO_ACCESS_TOKEN": "b",
            "FOO_CLIENT_SECRET": "c",
            "FOO_PASSWORD": "d",
        }

    def test_placeholders_and_empty_values_left_alone(self, home, unresolvable):
        result = _normalize(
            home,
            [
                {
                    "module": "provider-foo",
                    "config": {"api_key": "${SOME_VAR}", "token": "", "secret": None},
                }
            ],
        )
        cfg = result["config"]["providers"][0]["config"]
        assert cfg == {"api_key": "${SOME_VAR}", "token": "", "secret": None}
        assert not (home / ".amplifier" / "keys.env").exists()

    def test_default_name_claimed_falls_back_to_instance_name(self, home, unresolvable):
        from amplifier_app_cli.key_manager import KeyManager

        KeyManager().save_key("FOO_API_KEY", "existing")
        result = _normalize(
            home,
            [
                {
                    "module": "provider-foo",
                    "id": "foo-work",
                    "config": {"api_key": "new"},
                }
            ],
        )
        cfg = result["config"]["providers"][0]["config"]
        assert cfg["api_key"] == "${FOO_WORK_API_KEY}"
        stored = _keys_env(home)
        assert stored["FOO_API_KEY"] == "existing"  # never clobbered
        assert stored["FOO_WORK_API_KEY"] == "new"

    def test_two_entries_same_batch_never_collide(self, home, unresolvable):
        result = _normalize(
            home,
            [
                {"module": "provider-foo", "id": "foo", "config": {"api_key": "one"}},
                {
                    "module": "provider-foo",
                    "id": "foo-two",
                    "config": {"api_key": "two"},
                },
            ],
        )
        cfgs = [p["config"]["api_key"] for p in result["config"]["providers"]]
        assert cfgs == ["${FOO_API_KEY}", "${FOO_TWO_API_KEY}"]
        assert _keys_env(home) == {"FOO_API_KEY": "one", "FOO_TWO_API_KEY": "two"}

    def test_placeholder_in_another_scope_counts_as_claimed(self, home, unresolvable):
        settings = _make_settings(home)
        (home / "project").mkdir()
        (home / "project" / "settings.yaml").write_text(
            yaml.safe_dump(
                {
                    "config": {
                        "providers": [
                            {
                                "module": "provider-foo",
                                "config": {"api_key": "${FOO_API_KEY}"},
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        from amplifier_app_cli.provider_config_utils import normalize_provider_secrets

        scope_settings = {
            "config": {
                "providers": [
                    {
                        "module": "provider-foo",
                        "id": "foo-b",
                        "config": {"api_key": "x"},
                    }
                ]
            }
        }
        normalize_provider_secrets(settings, scope_settings, "global")
        assert (
            scope_settings["config"]["providers"][0]["config"]["api_key"]
            == "${FOO_B_API_KEY}"
        )

    def test_unusable_name_aborts_rather_than_colliding(self, home, unresolvable):
        """Default name taken and the id adds nothing distinguishing: like
        the resolvable path, raise so _write_scope never writes, instead of
        clobbering."""
        from amplifier_app_cli.key_manager import KeyManager

        KeyManager().save_key("FOO_API_KEY", "existing")
        with pytest.raises(ValueError):
            _normalize(
                home,
                [{"module": "provider-foo", "id": "foo", "config": {"api_key": "x"}}],
            )
        assert _keys_env(home) == {"FOO_API_KEY": "existing"}

    def test_warning_is_accurate(self, home, unresolvable):
        _normalize(home, [{"module": "provider-foo", "id": "foo", "config": {}}])
        printed = " ".join(str(c) for c in unresolvable.print.call_args_list)
        assert "Could not resolve provider module" in printed
        assert "skipping plaintext-secret scan" not in printed
        assert "by field name" in printed

    def test_secret_value_never_printed(self, home, unresolvable):
        _normalize(
            home,
            [
                {
                    "module": "provider-foo",
                    "id": "foo",
                    "config": {"api_key": "sk-SECRET"},
                }
            ],
        )
        printed = " ".join(str(c) for c in unresolvable.print.call_args_list)
        assert "sk-SECRET" not in printed

    def test_entry_without_module_string_still_scanned(self, home, unresolvable):
        result = _normalize(home, [{"id": "mystery", "config": {"api_key": "sk-x"}}])
        cfg = result["config"]["providers"][0]["config"]
        assert cfg["api_key"] == "${PROVIDER_MYSTERY_API_KEY}"
        assert _keys_env(home) == {"PROVIDER_MYSTERY_API_KEY": "sk-x"}

    def test_resolvable_provider_unaffected(self, home):
        """A resolved provider still only touches its declared secret field."""
        info = {
            "config_fields": [
                {"id": "api_key", "field_type": "secret", "env_var": "BAR_API_KEY"}
            ]
        }
        before = set(os.environ)
        try:
            with (
                patch(
                    "amplifier_app_cli.provider_config_utils.get_provider_info",
                    return_value=info,
                ),
                patch("amplifier_app_cli.provider_config_utils.console"),
            ):
                result = _normalize(
                    home,
                    [
                        {
                            "module": "provider-bar",
                            "config": {"api_key": "k", "access_token": "t"},
                        }
                    ],
                )
        finally:
            for name in set(os.environ) - before:
                os.environ.pop(name, None)
        cfg = result["config"]["providers"][0]["config"]
        assert cfg == {"api_key": "${BAR_API_KEY}", "access_token": "t"}


# ---------------------------------------------------------------------------
# 3. KeyManager.get_stored
# ---------------------------------------------------------------------------


class TestKeyManagerGetStored:
    def test_missing_file_returns_none(self, home):
        from amplifier_app_cli.key_manager import KeyManager

        assert KeyManager().get_stored("ANYTHING") is None

    def test_returns_saved_value_unquoted(self, home, monkeypatch):
        from amplifier_app_cli.key_manager import KeyManager

        monkeypatch.delenv("GS_TEST_KEY", raising=False)
        KeyManager().save_key("GS_TEST_KEY", "value=with=equals")
        assert KeyManager().get_stored("GS_TEST_KEY") == "value=with=equals"
        assert KeyManager().get_stored("GS_OTHER") is None

    def test_independent_of_environment(self, home, monkeypatch):
        from amplifier_app_cli.key_manager import KeyManager

        keys = home / ".amplifier" / "keys.env"
        keys.parent.mkdir(parents=True)
        keys.write_text(
            "# comment\nGS_STORED=\"from-file\"\n  GS_SINGLE = 'single'\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GS_STORED", "from-env")
        monkeypatch.setenv("GS_ENV_ONLY", "env")

        km = KeyManager()
        assert km.get_stored("GS_STORED") == "from-file"
        assert km.get_stored("GS_SINGLE") == "single"
        assert km.get_stored("GS_ENV_ONLY") is None
        assert km.stored_keys() == {"GS_STORED", "GS_SINGLE"}

    def test_uses_the_keys_env_lock(self, home):
        from amplifier_app_cli.key_manager import KeyManager

        km = KeyManager()
        keys = home / ".amplifier" / "keys.env"
        keys.parent.mkdir(parents=True)
        keys.write_text("A=1\n", encoding="utf-8")
        with patch.object(km, "_lock") as mock_lock:
            km.get_stored("A")
        mock_lock.__enter__.assert_called_once()


# ---------------------------------------------------------------------------
# 4. _discover_matrix_files(fetch=False)
# ---------------------------------------------------------------------------


class TestDiscoverMatrixFilesNoFetch:
    def test_fetch_false_never_fetches_on_empty_cache(self, home):
        from amplifier_app_cli.commands import routing

        with (
            patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch,
            patch.object(routing, "console") as mock_console,
        ):
            result = routing._discover_matrix_files(fetch=False)

        mock_fetch.assert_not_called()
        mock_console.print.assert_not_called()
        assert result == []

    def test_fetch_false_still_returns_cached_and_custom(self, home):
        from amplifier_app_cli.commands import routing

        cached = (
            home
            / ".amplifier"
            / "cache"
            / "amplifier-bundle-routing-matrix-x"
            / "routing"
        )
        cached.mkdir(parents=True)
        (cached / "balanced.yaml").write_text("name: balanced\n", encoding="utf-8")
        custom = home / ".amplifier" / "routing"
        custom.mkdir(parents=True)
        (custom / "mine.yaml").write_text("name: mine\n", encoding="utf-8")

        with patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch:
            result = routing._discover_matrix_files(fetch=False)

        mock_fetch.assert_not_called()
        assert sorted(p.name for p in result) == ["balanced.yaml", "mine.yaml"]

    def test_default_still_fetches(self, home):
        from amplifier_app_cli.commands import routing

        with (
            patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch,
            patch.object(routing, "console"),
        ):
            routing._discover_matrix_files()
        mock_fetch.assert_called_once()
