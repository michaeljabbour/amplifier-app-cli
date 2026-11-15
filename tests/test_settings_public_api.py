"""Tests for the small public API surface used by external settings UIs:

1. ``AppSettings.write_scope`` (returns the env var names it moved) and
   ``AppSettings.scope_lock``
2. ``KeyManager.stored_items``
3. ``commands.routing``: ``_discover_matrix_files(fetch=False)``,
   ``load_matrices``, ``LiveModels``, ``MATRIX_NAME_RE``
4. ``commands.reset.unmanaged_files`` and
   ``commands.provider.ensure_providers_ready``
5. ``normalize_provider_secrets``' best-effort fallback for provider
   entries whose module can't be resolved (never aborts a write)

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
    before = set(os.environ)
    yield tmp_path
    # KeyManager exports what it saves into os.environ; don't leak it.
    for name in set(os.environ) - before:
        os.environ.pop(name, None)


def _make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        paths=SettingsPaths(
            global_settings=tmp_path / "global" / "settings.yaml",
            project_settings=tmp_path / "project" / "settings.yaml",
            local_settings=tmp_path / "local" / "settings.local.yaml",
        )
    )


def _stored(home: Path) -> dict[str, str]:
    from amplifier_app_cli.key_manager import KeyManager

    return KeyManager().stored_items()


_INFO = {
    "config_fields": [
        {"id": "api_key", "field_type": "secret", "env_var": "BAR_API_KEY"}
    ]
}


def _resolvable():
    """Every module resolves to a provider whose secret field is api_key."""
    return patch(
        "amplifier_app_cli.provider_config_utils.get_provider_info",
        return_value=_INFO,
    )


# ---------------------------------------------------------------------------
# 1. AppSettings.write_scope / scope_lock
# ---------------------------------------------------------------------------


class TestWriteScope:
    def test_roundtrip_returns_empty_list_without_providers(self, home):
        settings = _make_settings(home)
        moved = settings.write_scope("global", {"bundle": {"active": "foundation"}})

        assert moved == []
        on_disk = yaml.safe_load(
            (home / "global" / "settings.yaml").read_text(encoding="utf-8")
        )
        assert on_disk == {"bundle": {"active": "foundation"}}
        assert settings._read_scope("global") == on_disk

    def test_returns_moved_names_and_writes_placeholders(self, home):
        settings = _make_settings(home)
        data = {
            "config": {
                "providers": [
                    {"module": "provider-bar", "config": {"api_key": "sk-1"}},
                    {
                        "module": "provider-bar",
                        "id": "bar-work",
                        "config": {"api_key": "sk-2"},
                    },
                ]
            }
        }
        with _resolvable(), patch("amplifier_app_cli.provider_config_utils.console"):
            moved = settings.write_scope("global", data)

        assert moved == ["BAR_API_KEY", "BAR_WORK_API_KEY"]
        raw = (home / "global" / "settings.yaml").read_text(encoding="utf-8")
        assert "sk-1" not in raw and "sk-2" not in raw
        assert "${BAR_API_KEY}" in raw and "${BAR_WORK_API_KEY}" in raw
        assert _stored(home) == {"BAR_API_KEY": "sk-1", "BAR_WORK_API_KEY": "sk-2"}

    def test_placeholders_are_not_reported_as_moved(self, home):
        settings = _make_settings(home)
        data = {
            "config": {
                "providers": [
                    {"module": "provider-bar", "config": {"api_key": "${BAR_API_KEY}"}}
                ]
            }
        }
        with _resolvable():
            assert settings.write_scope("global", data) == []

    def test_moved_names_come_from_return_value_not_console(self, home):
        """The names are returned even when console output is discarded."""
        settings = _make_settings(home)
        data = {
            "config": {
                "providers": [{"module": "provider-bar", "config": {"api_key": "x"}}]
            }
        }
        with (
            _resolvable(),
            patch("amplifier_app_cli.provider_config_utils.console") as console,
        ):
            console.quiet = True
            assert settings.write_scope("global", data) == ["BAR_API_KEY"]

    def test_private_write_scope_returns_the_same(self, home):
        settings = _make_settings(home)
        data = {
            "config": {
                "providers": [{"module": "provider-bar", "config": {"api_key": "x"}}]
            }
        }
        with _resolvable(), patch("amplifier_app_cli.provider_config_utils.console"):
            assert settings._write_scope("global", data) == ["BAR_API_KEY"]

    def test_existing_callers_unaffected(self, home):
        """Mutators that call _write_scope still work and return None."""
        settings = _make_settings(home)
        assert settings.set_active_bundle("foundation", scope="global") is None
        assert settings._read_scope("global")["bundle"]["active"] == "foundation"

    def test_unavailable_scope_raises_before_writing(self, home):
        settings = _make_settings(home)
        with pytest.raises(ValueError):
            settings.write_scope("session", {"a": 1})

    def test_scopes_are_independent(self, home):
        settings = _make_settings(home)
        settings.write_scope("project", {"a": 1})
        assert settings._read_scope("global") == {}
        assert settings._read_scope("project") == {"a": 1}

    def test_read_scope_was_cut(self):
        assert not hasattr(AppSettings, "read_scope")


class TestScopeLock:
    def test_is_the_private_lock_file(self, home):
        settings = _make_settings(home)
        lock = settings.scope_lock("global")
        assert lock.lock_file == settings._scope_lock("global").lock_file
        assert lock.lock_file == str(home / "global" / "settings.yaml") + ".lock"

    def test_read_modify_write_under_lock(self, home):
        settings = _make_settings(home)
        settings.write_scope("global", {"keep": True})
        with settings.scope_lock("global"):
            data = settings._read_scope("global")
            data["added"] = 1
            settings.write_scope("global", data)
        assert settings._read_scope("global") == {"keep": True, "added": 1}

    def test_excludes_a_second_holder(self, home):
        from filelock import FileLock, Timeout

        settings = _make_settings(home)
        with settings.scope_lock("global"):
            other = FileLock(settings.scope_lock("global").lock_file, timeout=0)
            with pytest.raises(Timeout):
                other.acquire()


# ---------------------------------------------------------------------------
# 2. KeyManager.stored_items
# ---------------------------------------------------------------------------


class TestStoredItems:
    def test_missing_file_returns_empty(self, home):
        from amplifier_app_cli.key_manager import KeyManager

        assert KeyManager().stored_items() == {}

    def test_returns_saved_values_unquoted(self, home):
        from amplifier_app_cli.key_manager import KeyManager

        KeyManager().save_key("SI_TEST_KEY", "value=with=equals")
        assert KeyManager().stored_items() == {"SI_TEST_KEY": "value=with=equals"}

    def test_independent_of_environment(self, home, monkeypatch):
        from amplifier_app_cli.key_manager import KeyManager

        keys = home / ".amplifier" / "keys.env"
        keys.parent.mkdir(parents=True)
        keys.write_text(
            "# comment\nSI_STORED=\"from-file\"\n  SI_SINGLE = 'single'\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SI_STORED", "from-env")
        monkeypatch.setenv("SI_ENV_ONLY", "env")

        km = KeyManager()
        assert km.stored_items() == {"SI_STORED": "from-file", "SI_SINGLE": "single"}
        assert km.stored_keys() == {"SI_STORED", "SI_SINGLE"}

    def test_one_lock_one_read(self, home):
        from amplifier_app_cli.key_manager import KeyManager

        keys = home / ".amplifier" / "keys.env"
        keys.parent.mkdir(parents=True)
        keys.write_text("A=1\nB=2\nC=3\n", encoding="utf-8")
        km = KeyManager()
        real_open = open
        opened: list[str] = []

        def counting_open(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        with (
            patch.object(km, "_lock") as mock_lock,
            patch("builtins.open", counting_open),
        ):
            assert km.stored_items() == {"A": "1", "B": "2", "C": "3"}
        mock_lock.__enter__.assert_called_once()
        assert opened == [str(keys)]

    def test_get_stored_was_removed(self):
        from amplifier_app_cli.key_manager import KeyManager

        assert not hasattr(KeyManager, "get_stored")


# ---------------------------------------------------------------------------
# 3. commands.routing
# ---------------------------------------------------------------------------


def _seed_matrices(home: Path) -> None:
    cached = (
        home / ".amplifier" / "cache" / "amplifier-bundle-routing-matrix-x" / "routing"
    )
    cached.mkdir(parents=True)
    (cached / "balanced.yaml").write_text(
        "name: balanced\nroles: {}\n", encoding="utf-8"
    )
    (cached / "shadowed.yaml").write_text("name: from-bundle\n", encoding="utf-8")
    (cached / "empty.yaml").write_text("", encoding="utf-8")
    custom = home / ".amplifier" / "routing"
    custom.mkdir(parents=True)
    (custom / "mine.yaml").write_text("name: mine\n", encoding="utf-8")
    (custom / "shadowed.yaml").write_text("name: from-user\n", encoding="utf-8")


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

        _seed_matrices(home)
        with patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch:
            result = routing._discover_matrix_files(fetch=False)

        mock_fetch.assert_not_called()
        assert sorted(p.name for p in result) == [
            "balanced.yaml",
            "empty.yaml",
            "mine.yaml",
            "shadowed.yaml",
            "shadowed.yaml",
        ]

    def test_default_still_fetches(self, home):
        from amplifier_app_cli.commands import routing

        with (
            patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch,
            patch.object(routing, "console"),
        ):
            routing._discover_matrix_files()
        mock_fetch.assert_called_once()


class TestLoadMatrices:
    def test_fetch_false_is_offline_and_silent(self, home):
        from amplifier_app_cli.commands import routing

        with (
            patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch,
            patch.object(routing, "console") as mock_console,
        ):
            assert routing.load_matrices(fetch=False) == {}
        mock_fetch.assert_not_called()
        mock_console.print.assert_not_called()

    def test_default_fetches(self, home):
        from amplifier_app_cli.commands import routing

        with (
            patch.object(routing, "_ensure_routing_bundle_cached") as mock_fetch,
            patch.object(routing, "console"),
        ):
            routing.load_matrices()
        mock_fetch.assert_called_once()

    def test_same_rows_as_the_private_pair(self, home):
        from amplifier_app_cli.commands import routing

        _seed_matrices(home)
        result = routing.load_matrices(fetch=False)
        expected = routing._load_all_matrices_with_paths(
            routing._discover_matrix_files(fetch=False)
        )
        assert result == expected

        # stem -> (data, winning path); empty files dropped; user dir wins.
        assert sorted(result) == ["balanced", "mine", "shadowed"]
        data, path = result["shadowed"]
        assert data == {"name": "from-user"}
        assert path == home / ".amplifier" / "routing" / "shadowed.yaml"


class TestRoutingPublicNames:
    def test_live_models_public_with_private_alias(self):
        from amplifier_app_cli.commands import routing

        assert routing._LiveModels is routing.LiveModels

    @pytest.mark.parametrize(
        "name", ["balanced", "my-matrix", "a", "x_2", "anthropic-knob-consistent"]
    )
    def test_matrix_name_accepted(self, name):
        from amplifier_app_cli.commands.routing import MATRIX_NAME_RE

        assert MATRIX_NAME_RE.fullmatch(name)

    @pytest.mark.parametrize(
        "name", ["Fast", "2x", "-x", "_x", "my.matrix", "a/b", "", "a" * 65]
    )
    def test_matrix_name_rejected(self, name):
        from amplifier_app_cli.commands.routing import MATRIX_NAME_RE

        assert not MATRIX_NAME_RE.fullmatch(name)

    def test_matrix_names_are_a_subset_of_the_hook_rule(self):
        """Everything the CLI accepts, hooks-routing's validate_matrix_name
        (``[a-z][a-z0-9_-]*``) accepts too."""
        import re

        from amplifier_app_cli.commands.routing import MATRIX_NAME_RE

        hook = re.compile(r"[a-z][a-z0-9_-]*")
        for name in ["Fast", "2x", "ok", "ok-2", "o_k", "Z", "9"]:
            if MATRIX_NAME_RE.fullmatch(name):
                assert hook.fullmatch(name)

    @pytest.mark.parametrize("name", ["Fast", "2x"])
    def test_edit_flow_refuses_names_the_hook_rejects(self, home, name):
        """The clone-and-edit flow's save step refuses the name and never
        writes a file (same rule guards the create flow)."""
        from amplifier_app_cli.commands import routing

        settings = _make_settings(home)
        with patch(
            "amplifier_app_cli.provider_config_utils.normalize_provider_secrets"
        ):
            settings._write_scope(
                "global", {"config": {"providers": [{"module": "provider-anthropic"}]}}
            )
        base = {
            "name": "balanced",
            "description": "Balanced matrix",
            "roles": {
                "general": {
                    "description": "General",
                    "candidates": [{"provider": "anthropic", "model": "claude-x"}],
                },
                "fast": {
                    "description": "Fast",
                    "candidates": [{"provider": "anthropic", "model": "claude-y"}],
                },
            },
        }
        import copy

        with (
            patch.object(routing, "console") as console,
            patch.object(
                routing, "_pick_base_matrix", return_value=copy.deepcopy(base)
            ),
            patch.object(routing, "_discover_matrix_files", return_value=[]),
            patch.object(
                routing, "_load_all_matrices", return_value={"balanced": base}
            ),
            patch.object(routing, "get_provider_models", return_value=[]),
            patch.object(routing, "Prompt") as prompt,
        ):
            prompt.ask.side_effect = ["s", name]
            routing._routing_edit_matrix(settings)

        assert not (home / ".amplifier" / "routing").exists() or not list(
            (home / ".amplifier" / "routing").glob("*.yaml")
        )
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "lowercase letter" in printed


# ---------------------------------------------------------------------------
# 4. commands.reset / commands.provider public names
# ---------------------------------------------------------------------------


class TestResetAndProviderPublicNames:
    def test_unmanaged_files_delegates(self, home):
        from amplifier_app_cli.commands import reset

        with patch.object(reset, "_get_unmanaged_files", return_value=["x"]) as m:
            assert reset.unmanaged_files() == ["x"]
        m.assert_called_once_with()

    def test_unmanaged_files_missing_dir(self, home):
        from amplifier_app_cli.commands import reset

        with patch.object(reset, "_get_amplifier_dir", return_value=home / "nope"):
            assert reset.unmanaged_files() == []

    def test_ensure_providers_ready_delegates(self):
        import importlib

        # The package re-exports the click group under the same name.
        provider = importlib.import_module("amplifier_app_cli.commands.provider")

        with patch.object(provider, "_ensure_providers_ready") as m:
            assert provider.ensure_providers_ready() is None
        m.assert_called_once_with()


# ---------------------------------------------------------------------------
# 5. normalize_provider_secrets: unresolvable provider module fallback
# ---------------------------------------------------------------------------


@pytest.fixture
def unresolvable(home):
    """get_provider_info() -> None for every module; console captured."""
    with (
        patch(
            "amplifier_app_cli.provider_config_utils.get_provider_info",
            return_value=None,
        ),
        patch("amplifier_app_cli.provider_config_utils.console") as mock_console,
    ):
        yield mock_console


def _normalize(home: Path, providers: list, scope: str = "global"):
    from amplifier_app_cli.provider_config_utils import normalize_provider_secrets

    scope_settings = {"config": {"providers": providers}}
    moved = normalize_provider_secrets(_make_settings(home), scope_settings, scope)
    return scope_settings, moved


def _printed(console) -> str:
    return " ".join(str(c) for c in console.print.call_args_list)


class TestSecretFieldNameRe:
    @pytest.mark.parametrize(
        "name",
        [
            "api_key",
            "apikey",
            "API_KEY",
            "Azure_API_KEY",
            "access_token",
            "token",
            "client_secret",
            "aws_secret_access_key",
            "private_key",
            "subscription_key",
            "password",
            "passphrase",
            "credentials",
            "credential",
        ],
    )
    def test_matches(self, name):
        from amplifier_app_cli.provider_config_utils import _SECRET_FIELD_NAME_RE

        assert _SECRET_FIELD_NAME_RE.search(name)

    @pytest.mark.parametrize(
        "name",
        [
            "max_tokens",
            "max_output_tokens",
            "keyword",
            "token_budget",
            "base_url",
            "default_model",
            "monkey",
        ],
    )
    def test_does_not_match(self, name):
        from amplifier_app_cli.provider_config_utils import _SECRET_FIELD_NAME_RE

        assert not _SECRET_FIELD_NAME_RE.search(name)


class TestUnresolvableModuleSecrets:
    def test_literal_api_key_moved_to_keys_env(self, home, unresolvable):
        result, moved = _normalize(
            home,
            [{"module": "provider-foo", "id": "foo", "config": {"api_key": "sk-1"}}],
        )
        cfg = result["config"]["providers"][0]["config"]
        assert cfg["api_key"] == "${FOO_API_KEY}"
        assert moved == ["FOO_API_KEY"]
        assert _stored(home) == {"FOO_API_KEY": "sk-1"}

    def test_secret_like_fields_case_insensitive(self, home, unresolvable):
        result, moved = _normalize(
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
                        "aws_secret_access_key": "e",
                        "base_url": "https://example.invalid",
                        "max_tokens": 4096,
                        "max_output_tokens": "8192",
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
        assert cfg["aws_secret_access_key"] == "${FOO_AWS_SECRET_ACCESS_KEY}"
        # Untouched: not secret-looking names, or not strings.
        assert cfg["base_url"] == "https://example.invalid"
        assert cfg["max_tokens"] == 4096
        assert cfg["max_output_tokens"] == "8192"
        assert cfg["token_budget"] == "1000"
        assert (
            set(moved)
            == set(_stored(home))
            == {
                "FOO_AZURE_API_KEY",
                "FOO_ACCESS_TOKEN",
                "FOO_CLIENT_SECRET",
                "FOO_PASSWORD",
                "FOO_AWS_SECRET_ACCESS_KEY",
            }
        )

    def test_placeholders_and_empty_values_left_alone(self, home, unresolvable):
        result, moved = _normalize(
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
        assert moved == []
        assert not (home / ".amplifier" / "keys.env").exists()

    def test_default_name_claimed_falls_back_to_instance_name(self, home, unresolvable):
        from amplifier_app_cli.key_manager import KeyManager

        KeyManager().save_key("FOO_API_KEY", "existing")
        result, moved = _normalize(
            home,
            [
                {
                    "module": "provider-foo",
                    "id": "foo-work",
                    "config": {"api_key": "new"},
                }
            ],
        )
        assert result["config"]["providers"][0]["config"]["api_key"] == (
            "${FOO_WORK_API_KEY}"
        )
        assert moved == ["FOO_WORK_API_KEY"]
        assert _stored(home) == {"FOO_API_KEY": "existing", "FOO_WORK_API_KEY": "new"}

    def test_two_entries_same_batch_never_collide(self, home, unresolvable):
        result, moved = _normalize(
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
        keys = [p["config"]["api_key"] for p in result["config"]["providers"]]
        assert keys == ["${FOO_API_KEY}", "${FOO_TWO_API_KEY}"]
        assert moved == ["FOO_API_KEY", "FOO_TWO_API_KEY"]
        assert _stored(home) == {"FOO_API_KEY": "one", "FOO_TWO_API_KEY": "two"}

    def test_no_usable_name_warns_and_leaves_entry(self, home, unresolvable):
        """Default name taken and the id adds nothing distinguishing: unlike
        the resolvable path, never raise -- warn and leave the value, as
        main did before the fallback existed."""
        from amplifier_app_cli.key_manager import KeyManager

        KeyManager().save_key("FOO_API_KEY", "existing")
        result, moved = _normalize(
            home,
            [{"module": "provider-foo", "id": "foo", "config": {"api_key": "sk-x"}}],
        )
        assert result["config"]["providers"][0]["config"]["api_key"] == "sk-x"
        assert moved == []
        assert _stored(home) == {"FOO_API_KEY": "existing"}
        printed = _printed(unresolvable)
        assert "Left 'api_key'" in printed
        assert "sk-x" not in printed

    def test_keys_env_failure_warns_and_leaves_entry(self, home, unresolvable):
        from amplifier_app_cli.key_manager import KeyManager

        with patch.object(KeyManager, "save_key", side_effect=OSError("disk full")):
            result, moved = _normalize(
                home,
                [{"module": "provider-foo", "config": {"api_key": "sk-x"}}],
            )
        assert result["config"]["providers"][0]["config"]["api_key"] == "sk-x"
        assert moved == []
        assert "disk full" in _printed(unresolvable)

    def test_one_failure_does_not_stop_other_entries(self, home, unresolvable):
        result, moved = _normalize(
            home,
            [
                {"config": {"api_key": "no-name"}},  # no module, no id
                {"module": "provider-foo", "config": {"api_key": "ok"}},
            ],
        )
        first, second = (p["config"] for p in result["config"]["providers"])
        assert first["api_key"] == "no-name"
        assert second["api_key"] == "${FOO_API_KEY}"
        assert moved == ["FOO_API_KEY"]

    def test_warning_is_accurate(self, home, unresolvable):
        _normalize(home, [{"module": "provider-foo", "id": "foo", "config": {}}])
        printed = _printed(unresolvable)
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
        assert "sk-SECRET" not in _printed(unresolvable)

    def test_entry_without_module_but_with_id_is_moved(self, home, unresolvable):
        result, moved = _normalize(
            home, [{"id": "mystery", "config": {"api_key": "sk-x"}}]
        )
        cfg = result["config"]["providers"][0]["config"]
        assert cfg["api_key"] == "${PROVIDER_MYSTERY_API_KEY}"
        assert moved == ["PROVIDER_MYSTERY_API_KEY"]

    def test_resolvable_provider_unaffected(self, home):
        """A resolved provider still only touches its declared secret field,
        and its unusable-name error still aborts the write."""
        with _resolvable(), patch("amplifier_app_cli.provider_config_utils.console"):
            result, moved = _normalize(
                home,
                [
                    {
                        "module": "provider-bar",
                        "config": {"api_key": "k", "access_token": "t"},
                    }
                ],
            )
            cfg = result["config"]["providers"][0]["config"]
            assert cfg == {"api_key": "${BAR_API_KEY}", "access_token": "t"}
            assert moved == ["BAR_API_KEY"]

            with pytest.raises(ValueError):
                _normalize(
                    home,
                    [
                        {
                            "module": "provider-bar",
                            "id": "bar",
                            "config": {"api_key": "x"},
                        }
                    ],
                )

    def test_write_scope_reports_fallback_moves(self, home, unresolvable):
        settings = _make_settings(home)
        data = {
            "config": {
                "providers": [
                    {"module": "provider-foo", "config": {"access_token": "t"}}
                ]
            }
        }
        assert settings.write_scope("global", data) == ["FOO_ACCESS_TOKEN"]
        raw = (home / "global" / "settings.yaml").read_text(encoding="utf-8")
        assert "${FOO_ACCESS_TOKEN}" in raw


class TestFallbackNeverAbortsUnrelatedWrites:
    """Regression (review blocker 5): with one provider entry on disk that
    has no module and a literal api_key, an unrelated settings write must
    succeed, exactly as it did before the fallback existed."""

    @pytest.fixture
    def default_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AMPLIFIER_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project = tmp_path / "project"
        project.mkdir()
        monkeypatch.chdir(project)
        before = set(os.environ)
        yield tmp_path
        for name in set(os.environ) - before:
            os.environ.pop(name, None)

    def _seed(self, home: Path, entry: dict) -> Path:
        path = home / ".amplifier" / "settings.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"config": {"providers": [entry]}}), encoding="utf-8"
        )
        return path

    def test_set_active_bundle_succeeds_with_moduleless_literal(self, default_home):
        path = self._seed(default_home, {"config": {"api_key": "sk-literal"}})

        settings = AppSettings()
        # Safety: the default paths must resolve inside the tmp home.
        assert settings.paths.global_settings == path

        with patch("amplifier_app_cli.provider_config_utils.console") as console:
            settings.set_active_bundle("foundation")

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["bundle"]["active"] == "foundation"
        # Left as it is, as main did -- and the user is told so.
        assert data["config"]["providers"][0]["config"]["api_key"] == "sk-literal"
        printed = _printed(console)
        assert "Could not resolve provider module" in printed
        assert "Left 'api_key'" in printed
        assert "sk-literal" not in printed

    def test_set_active_bundle_succeeds_when_name_is_taken(self, default_home):
        from amplifier_app_cli.key_manager import KeyManager

        KeyManager().save_key("FOO_API_KEY", "existing")
        path = self._seed(
            default_home,
            {"module": "provider-foo", "id": "foo", "config": {"api_key": "sk-l"}},
        )
        with (
            patch(
                "amplifier_app_cli.provider_config_utils.get_provider_info",
                return_value=None,
            ),
            patch("amplifier_app_cli.provider_config_utils.console"),
        ):
            AppSettings().set_active_bundle("foundation")

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["bundle"]["active"] == "foundation"
        assert data["config"]["providers"][0]["config"]["api_key"] == "sk-l"
        assert _stored(default_home) == {"FOO_API_KEY": "existing"}
