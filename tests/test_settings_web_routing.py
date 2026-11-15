"""Routing model behind `amplifier settings` -- must agree with `amplifier routing`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from amplifier_app_cli.settings_web_routing import RoutingService, compose
from amplifier_app_cli.settings_web_system import SettingsError

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


@pytest.fixture
def svc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RoutingService:
    from amplifier_app_cli.commands import routing

    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", lambda: None)  # stay offline
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


def test_save_matrix_rules(svc: RoutingService) -> None:
    roles = {r: {"description": d["description"], "candidates": d["candidates"]} for r, d in MATRIX["roles"].items()}
    out = svc.save_matrix({"name": "mine", "roles": roles})
    saved = yaml.safe_load(Path(out["path"]).read_text())
    assert saved["name"] == "mine" and set(saved["roles"]) == {"general", "fast"}
    assert "mine" in {m["name"] for m in svc.matrices()}
    with pytest.raises(SettingsError) as exc:
        svc.save_matrix({"name": "mine", "roles": roles})
    assert exc.value.status == 409 and exc.value.extra == {"exists": True}
    svc.save_matrix({"name": "mine", "roles": roles, "overwrite": True})
    with pytest.raises(SettingsError, match="general"):
        svc.save_matrix({"name": "nofast", "roles": {"fast": roles["fast"]}})
    with pytest.raises(SettingsError):
        svc.save_matrix({"name": "../escape", "roles": roles})
    svc.delete_matrix("mine")
    assert "mine" not in {m["name"] for m in svc.matrices()}
    with pytest.raises(SettingsError):
        svc.delete_matrix("../settings")


def test_known_config_offers_values_seen_in_matrices(svc: RoutingService) -> None:
    res = svc.resolve("team", {}, live=False)
    assert res["known_config"]["anthropic"]["reasoning_effort"] == ["medium"]
