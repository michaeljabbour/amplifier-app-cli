"""Shared test fixtures for amplifier-app-cli tests.

With --import-mode=importlib (set in pyproject.toml), test files cannot import
directly from conftest.py. Shared helper functions live in tests/helpers.py,
which is made importable by adding the tests/ directory to sys.path below.
"""

import sys
from pathlib import Path

import pytest

# Make tests/ importable as a directory so test files can do:
#   from helpers import make_command_processor
sys.path.insert(0, str(Path(__file__).parent))

from amplifier_app_cli.main import CommandProcessor  # noqa: E402


# ---------------------------------------------------------------------------
# Autouse fixture — reset class-level SKILL_SHORTCUTS between tests to
# prevent state leaking from one test into another via the shared class dict.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def no_live_model_listing():
    """`routing show` verifies candidate globs against live ``list_models()``.

    Unit tests must never do that: a fixture provider with no credentials
    retries for ~35s before failing (measured: the routing test files went
    from 3s to 578s), and the result would depend on the network anyway. The
    default here is "could not list" -- every glob renders as unverified,
    which is byte-identical to the pre-verification output. Tests that
    exercise verification itself patch ``_list_models_bounded`` with an
    explicit table on top of this (tests/test_routing_show_verifies_globs.py).

    Patched at the bounded wrapper, not at ``_list_models_for_provider``, so
    the tests that exercise THAT helper directly still see the real thing.

    Uses a PRIVATE MonkeyPatch, deliberately not the shared ``monkeypatch``
    fixture. Requesting the shared one from an autouse fixture makes it set
    up before -- and therefore torn down after -- every test's own fixtures.
    Tests that ``monkeypatch.chdir()`` into a ``TemporaryDirectory`` then had
    that directory deleted while it was still the process cwd: fine on
    Linux/macOS, PermissionError on Windows, deterministically, in
    tests/lib/mention_loading/test_resolver.py. Measured on CI for #323.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "amplifier_app_cli.commands.routing._list_models_bounded",
            lambda _selector, _settings: None,
        )
        yield


# The real Amplifier home as resolved before any test patches HOME,
# Path.home() or AMPLIFIER_HOME.
from amplifier_foundation.paths.resolution import (  # noqa: E402
    get_amplifier_home as _real_get_amplifier_home,
)

_REAL_AMPLIFIER_HOME = _real_get_amplifier_home()


@pytest.fixture(autouse=True)
def keys_env_never_real(tmp_path_factory):
    """KeyManager must never read or write the developer's real
    ``~/.amplifier/keys.env`` from a unit test.

    ``normalize_provider_secrets()`` moves literal provider secrets into
    keys.env on every provider write -- including entries whose module
    can't be resolved -- so any test that writes a provider entry with a
    literal ``api_key`` without isolating its home would otherwise append
    test values to the real store. Tests that DO isolate (``Path.home``,
    ``HOME`` or ``AMPLIFIER_HOME``) are unaffected; only a resolution that
    lands on the real home is redirected to a per-test temp dir. Private
    MonkeyPatch for the same reason as ``no_live_model_listing`` above.
    """
    fallback: list[Path] = []

    def _guarded() -> Path:
        home = _real_get_amplifier_home()
        if home != _REAL_AMPLIFIER_HOME:
            return home
        # Created lazily: an extra directory under the shared pytest
        # basetemp is visible to tests that audit it (test_doctor_entrypoint).
        if not fallback:
            fallback.append(tmp_path_factory.mktemp("amplifier_home"))
        return fallback[0]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("amplifier_app_cli.key_manager.get_amplifier_home", _guarded)
        yield


@pytest.fixture(autouse=True)
def reset_skill_shortcuts():
    """Clear SKILL_SHORTCUTS before and after every test in this suite."""
    CommandProcessor.SKILL_SHORTCUTS.clear()
    yield
    CommandProcessor.SKILL_SHORTCUTS.clear()


# ---------------------------------------------------------------------------
# Home-directory isolation that actually holds on Windows.
#
# ``Path.home()`` is ``os.path.expanduser("~")``. On POSIX that reads HOME; on
# Windows ``ntpath.expanduser`` reads USERPROFILE (then HOMEDRIVE+HOMEPATH) and
# NEVER consults HOME. So ``monkeypatch.setenv("HOME", tmp_path)`` alone -- the
# idiom every SessionStore-touching test used -- isolated nothing on Windows:
# tests wrote real records into the runner's (or a developer's) actual
# ``~/.amplifier/projects/<slug>/sessions/``, and two tests asserting that a
# record did NOT exist found one left by a previous test. Set both.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path, monkeypatch) -> Path:
    """Point ``Path.home()`` at ``tmp_path`` on every platform; returns it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path
