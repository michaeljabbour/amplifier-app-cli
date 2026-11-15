"""Tests for `amplifier settings` (local web settings editor)."""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest
import yaml

from amplifier_app_cli.lib.settings import AppSettings, SettingsPaths
from types import SimpleNamespace

from amplifier_app_cli.settings_web import (
    SettingsError,
    SettingsServer,
    build_state,
    save_key,
    save_scope,
    validate_doc,
)
from amplifier_app_cli.settings_web_routing import RoutingService


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AMPLIFIER_HOME", str(h))
    return h


@pytest.fixture
def settings(home: Path, tmp_path: Path) -> AppSettings:
    proj = tmp_path / "proj" / ".amplifier"
    proj.mkdir(parents=True)
    return AppSettings(
        SettingsPaths(
            global_settings=home / "settings.yaml",
            project_settings=proj / "settings.yaml",
            local_settings=proj / "settings.local.yaml",
        )
    )


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI fetches the routing bundle when none is cached; tests stay offline."""
    from amplifier_app_cli.commands import routing

    monkeypatch.setattr(routing, "_ensure_routing_bundle_cached", lambda: None)


def _srv(settings: AppSettings) -> SimpleNamespace:
    from amplifier_app_cli.settings_web_env import LiveEnv

    return SimpleNamespace(settings=settings, routing=RoutingService(settings), env=LiveEnv())


def _state(settings: AppSettings) -> dict:
    return build_state(settings, RoutingService(settings))


def _global(state: dict) -> dict:
    return next(s for s in state["scopes"] if s["name"] == "global")


def test_validate_doc_rejects_shapes_the_cli_chokes_on() -> None:
    assert validate_doc({"anything": {"unknown": 1}}) == []
    assert validate_doc([1]) != []
    errs = validate_doc(
        {"config": {"providers": [{"id": "a", "module": "provider-x"}, {"id": "a"}]}}
    )
    assert any("needs a module" in e for e in errs)
    assert any("share the id 'a'" in e for e in errs)
    assert validate_doc({"bundle": {"app": "not-a-list"}}) != []


def test_round_trip_preserves_unknown_keys(settings: AppSettings) -> None:
    original = {"custom_thing": {"nested": [1, 2]}, "routing": {"matrix": "balanced"}}
    settings.paths.global_settings.write_text(yaml.safe_dump(original))
    g = _global(_state(settings))
    save_scope(_srv(settings), {"scope": "global", "doc": g["doc"], "version": g["version"]})
    assert yaml.safe_load(settings.paths.global_settings.read_text()) == original


def test_stale_version_is_a_conflict_not_an_overwrite(settings: AppSettings) -> None:
    settings.paths.global_settings.write_text("a: 1\n")
    g = _global(_state(settings))
    settings.paths.global_settings.write_text("a: 2\n")  # someone else wrote it
    with pytest.raises(SettingsError) as exc:
        save_scope(_srv(settings), {"scope": "global", "doc": {"a": 3}, "version": g["version"]}
        )
    assert exc.value.status == 409
    assert settings.paths.global_settings.read_text() == "a: 2\n"


def test_yaml_save_and_invalid_yaml(settings: AppSettings) -> None:
    st = save_scope(_srv(settings),
        {"scope": "project", "yaml": "routing:\n  matrix: economy\n", "version": ""},
    )
    assert st["effective"]["provenance"]["routing"] == "project"
    with pytest.raises(SettingsError) as exc:
        save_scope(_srv(settings), {"scope": "global", "yaml": "a: [", "version": ""})
    assert exc.value.status == 422


def test_session_scope_not_editable(settings: AppSettings) -> None:
    with pytest.raises(SettingsError):
        save_scope(_srv(settings), {"scope": "session", "doc": {}})


def test_pasted_secret_never_lands_in_yaml(settings: AppSettings, home: Path) -> None:
    doc = {
        "config": {
            "providers": [
                {
                    "id": "my-llm",
                    "module": "provider-does-not-exist",
                    "config": {"api_key": "sk-literal-123"},
                }
            ]
        }
    }
    st = save_scope(_srv(settings), {"scope": "global", "doc": doc, "version": ""})
    raw = settings.paths.global_settings.read_text()
    assert "sk-literal-123" not in raw
    # the CLI's own write path moved it (even though the provider can't load here)
    [name] = st["moved_keys"]
    assert f"${{{name}}}" in raw
    assert "sk-literal-123" in (home / "keys.env").read_text()
    assert "sk-literal-123" not in json.dumps(st, default=str)


def test_key_values_are_write_only(settings: AppSettings, home: Path) -> None:
    st = save_key(_srv(settings), {"name": "MY_TOKEN", "value": "secret-value"})
    assert "secret-value" in (home / "keys.env").read_text()
    assert "secret-value" not in json.dumps(st, default=str)
    assert {"name": "MY_TOKEN", "stored": True}.items() <= next(
        k for k in st["keys"] if k["name"] == "MY_TOKEN"
    ).items()
    for bad in (
        {"name": "bad name", "value": "x"},
        {"name": "OK", "value": "a\nB=1"},
        {"name": "OK", "value": ""},
    ):
        with pytest.raises(SettingsError):
            save_key(_srv(settings), bad)


# ---------------- HTTP guards ----------------


@pytest.fixture
def shell_reads(home: Path, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stand in for the user's login shell (never run the real one in tests); counts reads."""
    from amplifier_app_cli import settings_web_env

    calls: list[int] = []
    monkeypatch.setattr(settings_web_env, "login_shell_env", lambda: calls.append(1) or {"HOME": str(home)})
    return calls


@pytest.fixture
def server(settings: AppSettings, shell_reads: list[int]):
    srv = SettingsServer(port=0, settings=settings)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _req(
    srv: SettingsServer,
    method: str,
    path: str,
    headers: dict | None = None,
    body: dict | None = None,
):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
    conn.request(
        method,
        path,
        body=json.dumps(body) if body is not None else None,
        headers=headers or {},
    )
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r, data


def test_http_guards(server: SettingsServer) -> None:
    r, _ = _req(server, "GET", "/api/state")
    assert r.status == 401
    r, _ = _req(server, "GET", "/?t=wrong")
    assert r.status == 403
    r, _ = _req(server, "GET", f"/?t={server.launch_token}")
    assert r.status == 303
    set_cookie = r.getheader("Set-Cookie") or ""
    cookie = set_cookie.split(";")[0]
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie

    ok = {"Cookie": cookie}
    r, data = _req(server, "GET", "/api/state", ok)
    assert r.status == 200 and "scopes" in json.loads(data)
    assert "script-src 'self'" in (r.getheader("Content-Security-Policy") or "")

    assert (
        _req(server, "GET", "/api/state", {**ok, "Host": "evil.example:80"})[0].status
        == 403
    )
    assert (
        _req(server, "GET", "/api/state", {**ok, "Origin": "http://evil.example"})[
            0
        ].status
        == 403
    )
    assert (
        _req(server, "GET", "/api/state", {**ok, "Sec-Fetch-Site": "cross-site"})[
            0
        ].status
        == 403
    )

    body = {"scope": "global", "doc": {"a": 1}, "version": ""}
    assert (
        _req(server, "POST", "/api/scope", ok, body)[0].status == 403
    )  # no custom header
    assert (
        _req(server, "POST", "/api/scope", {"X-Amplifier-Settings": "1"}, body)[
            0
        ].status
        == 401
    )  # no cookie
    r, _ = _req(server, "POST", "/api/scope", {**ok, "X-Amplifier-Settings": "1"}, body)
    assert r.status == 200


def test_only_state_fresh_and_diagnose_rerun_the_shell(server: SettingsServer, shell_reads: list[int]) -> None:
    cookie = (_req(server, "GET", f"/?t={server.launch_token}")[0].getheader("Set-Cookie") or "").split(";")[0]
    ok = {"Cookie": cookie}
    assert _req(server, "GET", "/api/changes", ok)[0].status == 200
    assert shell_reads == []  # the poll never starts a shell, not even the first time
    assert _req(server, "GET", "/api/state", ok)[0].status == 200
    assert len(shell_reads) == 1  # startup read
    for path in ("/api/state", "/api/changes", "/api/changes"):
        assert _req(server, "GET", path, ok)[0].status == 200
    assert len(shell_reads) == 1
    assert _req(server, "GET", "/api/state?fresh=1", ok)[0].status == 200
    assert len(shell_reads) == 2  # page regained focus
    _req(server, "POST", "/api/providers/diagnose", {**ok, "X-Amplifier-Settings": "1"}, {"name": "nope"})
    assert len(shell_reads) == 3  # diagnose sees what a new terminal would


def test_static_assets_ship_with_package() -> None:
    ui = Path(__file__).parents[1] / "amplifier_app_cli" / "data" / "settings_ui"
    for name in ("settings.html", "settings.css", "app.js"):
        assert (ui / name).is_file()
    # every module app.js (transitively) imports exists, so the page can't 404 on a module
    import re

    seen, todo = set(), ["app.js"]
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        path = ui / rel
        assert path.is_file(), rel
        for spec in re.findall(r"""from\s+["'](\.{1,2}/[^"']+)["']""", path.read_text()):
            todo.append(str((path.parent / spec).resolve().relative_to(ui.resolve())))
    assert "<script>" not in (ui / "settings.html").read_text()  # CSP: no inline script
