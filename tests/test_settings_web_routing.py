"""Routing model behind `amplifier settings` -- must agree with `amplifier routing`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.settings_web_routing import RoutingService, compose
from amplifier_app_cli.settings_web_system import SettingsError, run_with_timeout

MATRIX = {
    "name": "team",
    "description": "Test matrix",
    "updated": "2026-11-01",
    "roles": {
        "general": {
            "description": "Catch-all",
            "candidates": [
                {"provider": "gemini", "model": "gemini-pro"},
                {"provider": "anthropic", "model": "claude-sonnet-5", "config": {"reasoning_effort": "medium"}},
                {"provider": "openai", "model": "gpt-6"},
            ],
        },
        "fast": {"description": "Quick", "candidates": [{"provider": "gemini", "model": "gemini-flash"}]},
    },
}


@pytest.fixture(autouse=True)
def _no_fetch(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from amplifier_app_cli.commands import routing

    fetched: list[str] = []
    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", lambda: fetched.append("fetch"))
    return fetched


@pytest.fixture
def svc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RoutingService:
    home = tmp_path / "home"
    (home / "routing").mkdir(parents=True)
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    (home / "routing" / "team.yaml").write_text(yaml.safe_dump(MATRIX))
    (home / "settings.yaml").write_text(
        yaml.safe_dump(
            {
                "routing": {"matrix": "team"},
                "config": {
                    "providers": [
                        {"module": "provider-anthropic", "config": {"priority": 1}},
                        {"id": "work-openai", "module": "provider-openai", "config": {"priority": 2}},
                    ]
                },
            }
        )
    )
    proj = tmp_path / "proj" / ".amplifier"
    proj.mkdir(parents=True)
    settings = AppSettings(
        SettingsPaths(
            global_settings=home / "settings.yaml",
            project_settings=proj / "settings.yaml",
            local_settings=proj / "settings.local.yaml",
        )
    )
    return RoutingService(settings)


def test_matrices_report_coverage_like_routing_list(svc: RoutingService) -> None:
    [m] = [m for m in svc.matrices() if m["name"] == "team"]
    assert (m["covered"], m["total"]) == (1, 2)  # fast only has gemini
    assert m["origin"] == "yours"


def test_resolution_marks_winner_fallback_and_skipped(svc: RoutingService) -> None:
    res = svc.resolve("team", {}, live=False)
    general = next(r for r in res["roles"] if r["name"] == "general")
    assert [c["state"] for c in general["candidates"]] == ["unconfigured", "winner", "fallback"]
    assert general["candidates"][general["winner"]]["provider"] == "anthropic"
    fast = next(r for r in res["roles"] if r["name"] == "fast")
    assert fast["winner"] is None and fast["problem"] == "no-provider"
    # instance ids count as configured, as in `routing show`
    assert {"anthropic", "work-openai"} <= {p["name"] for p in res["providers"]}


def test_pin_with_base_goes_first_then_matrix_list(svc: RoutingService) -> None:
    overrides = {"fast": {"candidates": [{"provider": "anthropic", "model": "claude-haiku-5"}, "base"]}}
    res = svc.resolve("team", overrides, live=False)
    fast = next(r for r in res["roles"] if r["name"] == "fast")
    assert fast["overridden"]
    assert [(c["provider"], c["state"]) for c in fast["candidates"]] == [
        ("anthropic", "winner"),
        ("gemini", "unconfigured"),
    ]


def test_compose_without_base_replaces_role() -> None:
    roles = compose(MATRIX["roles"], {"general": {"candidates": [{"provider": "x", "model": "y"}]}})
    assert roles["general"]["candidates"] == [{"provider": "x", "model": "y"}]


def test_unknown_matrix_is_404(svc: RoutingService) -> None:
    with pytest.raises(SettingsError) as exc:
        svc.resolve("nope", {}, live=False)
    assert exc.value.status == 404


def test_empty_cache_never_fetches(svc: RoutingService, _no_fetch: list[str]) -> None:
    """No routing-matrix bundle cached: the page lists what exists and never downloads."""
    assert [m["name"] for m in svc.matrices()] == ["team"]
    svc.resolve("team", {}, live=False)
    assert _no_fetch == []


def test_slow_provider_call_is_abandoned_on_a_daemon_thread() -> None:
    import threading

    release = threading.Event()
    with pytest.raises(TimeoutError):
        run_with_timeout(lambda: release.wait(5), 0.05)
    [t] = [t for t in threading.enumerate() if t.name == "settings-provider-call"]
    assert t.daemon  # interpreter exit won't wait for it
    release.set()
    assert run_with_timeout(lambda: 7, 1) == 7
    with pytest.raises(ValueError):
        run_with_timeout(lambda: int("x"), 1)


def test_known_config_offers_values_seen_in_matrices(svc: RoutingService) -> None:
    res = svc.resolve("team", {}, live=False)
    assert res["known_config"]["anthropic"]["reasoning_effort"] == ["medium"]
