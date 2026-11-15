"""Local web UI for Amplifier settings (`amplifier settings`).

The browser edits one *scope document* at a time -- the parsed YAML of the
global, project or local settings file -- and saves the whole document back
through ``AppSettings._write_scope``, the same funnel every CLI command uses.
So atomic writes and plaintext-secret normalization apply exactly as they do
for `amplifier provider`. Every form on the page is a view over that document.

Safety model (shared with constellation-observatory and amplifier-unified):
  * binds 127.0.0.1 only, random port by default
  * the printed link carries a per-launch token, exchanged for an HttpOnly,
    SameSite=Strict cookie named for this port (so two instances don't collide)
  * Host must match (blocks DNS rebinding); Origin and Sec-Fetch-Site must be
    same-origin; POSTs need a custom header (forces a CORS preflight, never answered)
  * strict CSP with no inline script or style; responses are never cached
  * saves carry the sha256 of the file the user loaded; a file that changed
    underneath is a 409, never a silent overwrite
  * key values are masked in every response; one key is revealed only when asked
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from collections.abc import Callable
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import yaml
from amplifier_foundation.paths.resolution import get_amplifier_home

from . import settings_web_system as system
from .lib.settings import AppSettings, Scope
from .provider_sources import DEFAULT_PROVIDER_SOURCES
from .settings_web_routing import RoutingService
from .settings_web_system import SettingsError

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).parent / "data" / "settings_ui"
STATIC = {
    "/": ("settings.html", "text/html; charset=utf-8"),
    "/settings.css": ("settings.css", "text/css; charset=utf-8"),
    "/settings.js": ("settings.js", "text/javascript; charset=utf-8"),
}
EDITABLE_SCOPES: tuple[Scope, ...] = ("global", "project", "local")
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
MAX_BODY = 1_000_000
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


# --------------------------------------------------------------------------
# Settings model (plain functions over AppSettings; testable without HTTP)
# --------------------------------------------------------------------------


def file_version(path: Path) -> str:
    """Content hash of a scope file; "" when it does not exist."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def read_doc(path: Path) -> tuple[dict[str, Any], str | None]:
    """Parse a scope file into (doc, error). A broken file is reported, not hidden."""
    if not path.exists():
        return {}, None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return {}, f"{path} is not valid YAML: {e}"
    if not isinstance(data, dict):
        return {}, f"{path} must contain a mapping at the top level"
    return data, None


def placeholders(value: Any) -> set[str]:
    """Every ${VAR} referenced anywhere in a settings value."""
    if isinstance(value, str):
        return set(PLACEHOLDER.findall(value))
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return set().union(*(placeholders(v) for v in items))


def validate_doc(doc: Any) -> list[str]:
    """Reject only the shapes the CLI would choke on; unknown keys pass through."""
    if not isinstance(doc, dict):
        return ["Settings must be a mapping (key: value) at the top level."]
    errs: list[str] = []
    bundle = doc.get("bundle")
    if bundle is not None and not isinstance(bundle, dict):
        errs.append("bundle must be a mapping.")
    elif isinstance(bundle, dict):
        if "active" in bundle and not isinstance(bundle["active"], str):
            errs.append("bundle.active must be a bundle name.")
        app = bundle.get("app")
        if app is not None and not (isinstance(app, list) and all(isinstance(u, str) for u in app)):
            errs.append("bundle.app must be a list of bundle URIs.")
        added = bundle.get("added")
        if added is not None and not (isinstance(added, dict) and all(isinstance(v, str) for v in added.values())):
            errs.append("bundle.added must map names to URIs.")
    config = doc.get("config")
    if config is not None and not isinstance(config, dict):
        errs.append("config must be a mapping.")
    elif isinstance(config, dict) and "providers" in config:
        providers = config["providers"]
        if not isinstance(providers, list):
            errs.append("config.providers must be a list.")
        else:
            seen: set[str] = set()
            for i, p in enumerate(providers):
                where = f"config.providers[{i}]"
                if not isinstance(p, dict):
                    errs.append(f"{where} must be a mapping.")
                    continue
                if not isinstance(p.get("module"), str) or not p["module"]:
                    errs.append(f"{where} needs a module (e.g. provider-anthropic).")
                if "config" in p and not isinstance(p["config"], dict):
                    errs.append(f"{where}.config must be a mapping.")
                ident = p.get("id") or p.get("module")
                if isinstance(ident, str):
                    if ident in seen:
                        errs.append(f"Two providers share the id '{ident}'.")
                    seen.add(ident)
    if doc.get("routing") is not None and not isinstance(doc["routing"], dict):
        errs.append("routing must be a mapping.")
    return errs


def key_list(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keys the page shows: everything in keys.env plus every ${VAR} settings use."""
    stored = system.read_keys_file()
    referenced = set().union(*(placeholders(d) for d in docs)) if docs else set()
    out = []
    for name in sorted(referenced | set(stored)):
        value = stored.get(name) or os.environ.get(name)
        out.append(
            {
                "name": name,
                "stored": name in stored,
                # KeyManager copies keys.env into the environment, so the
                # environment only tells us something for keys not in the file
                "in_env": name not in stored and bool(os.environ.get(name)),
                "referenced": name in referenced,
                "preview": system.mask(value) if value else None,
            }
        )
    return out


def build_state(settings: AppSettings, routing: RoutingService) -> dict[str, Any]:
    scopes, provenance = [], {}
    for scope in EDITABLE_SCOPES:
        path = settings.get_scope_path(scope)
        doc, error = read_doc(path)
        provenance.update(dict.fromkeys(doc, scope))  # later scopes win
        scopes.append(
            {
                "name": scope,
                "path": str(path),
                "exists": path.exists(),
                "available": settings.is_scope_available(scope),
                "version": file_version(path),
                "doc": doc,
                "yaml": path.read_text(encoding="utf-8") if path.exists() else "",
                "error": error,
            }
        )
    try:
        merged, merge_error = settings.get_merged_settings(), None
    except Exception as e:  # noqa: BLE001 -- a malformed file somewhere; show it
        merged, merge_error = {}, str(e)
    keys = key_list([s["doc"] for s in scopes])
    try:
        status = system.status(settings, routing, keys, merged)
    except Exception as e:  # noqa: BLE001 -- home must render even when status can't
        status = {"error": f"Couldn't work out Amplifier's status: {e}"}
    return {
        "home": str(get_amplifier_home()),
        "cwd": str(Path.cwd()),
        "scopes": scopes,
        "effective": {
            "doc": merged,
            "yaml": yaml.safe_dump(merged, sort_keys=True, allow_unicode=True) if merged else "",
            "provenance": provenance,
            "error": merge_error,
        },
        "keys": keys,
        "status": status,
        "cli_top_level_keys": sorted(system.CLI_TOP_LEVEL_KEYS),
        "known_providers": [{"module": m, "source": s} for m, s in sorted(DEFAULT_PROVIDER_SOURCES.items())],
    }


def secure_literal_secrets(settings: AppSettings, doc: dict[str, Any], scope: Scope) -> list[str]:
    """Move literal secrets in provider config into keys.env; return the names written.

    The CLI's ``normalize_provider_secrets`` (also run by ``_write_scope``) skips,
    with only a console warning, any provider whose module can't load in this
    process -- a browser user would never see that warning and the key would land
    in settings.yaml. So run it first (its naming wins whenever it can act), then
    move any literal it left behind to ``AMPLIFIER_<ID>_<FIELD>``, the convention
    shared with amplifier-unified, never reusing a name that holds another value.
    """
    from .key_manager import KeyManager
    from .provider_config_utils import normalize_provider_secrets

    try:
        normalize_provider_secrets(settings, doc, scope)
    except Exception as e:  # noqa: BLE001 -- the fallback below still guarantees no literal
        logger.debug(f"normalize_provider_secrets failed; using fallback: {e}")
    written: list[str] = []
    for entry in (doc.get("config") or {}).get("providers") or []:
        cfg = entry.get("config") if isinstance(entry, dict) else None
        if not isinstance(cfg, dict):
            continue
        ident = re.sub(r"[^A-Za-z0-9]+", "_", str(entry.get("id") or entry.get("module"))).strip("_")
        for field, value in list(cfg.items()):
            if not (isinstance(value, str) and value.strip() and system.SECRET_FIELD.search(field)):
                continue
            if PLACEHOLDER.fullmatch(value.strip()):
                continue
            stored = system.read_keys_file()
            base = name = f"AMPLIFIER_{ident}_{field}".upper()
            n = 2
            while stored.get(name, value.strip()) != value.strip():  # taken by another value
                name, n = f"{base}_{n}", n + 1
            KeyManager().save_key(name, value.strip())
            cfg[field] = "${" + name + "}"
            written.append(name)
    return written


def save_scope(server: SettingsServer, body: dict[str, Any]) -> dict[str, Any]:
    settings = server.settings
    scope = body.get("scope")
    if scope not in EDITABLE_SCOPES:
        raise SettingsError(HTTPStatus.BAD_REQUEST, f"Unknown scope: {scope!r}")
    scope = cast(Scope, scope)
    if "yaml" in body:
        try:
            doc = yaml.safe_load(body["yaml"] or "") or {}
        except yaml.YAMLError as e:
            raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, f"Not valid YAML: {e}") from e
    else:
        doc = body.get("doc")
    errors = validate_doc(doc)
    if errors:
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Settings not saved.", errors=errors)
    path = settings.get_scope_path(scope)
    with settings._scope_lock(scope):
        if file_version(path) != (body.get("version") or ""):
            raise SettingsError(
                HTTPStatus.CONFLICT,
                f"{path.name} changed since you opened it (another command or editor wrote it). "
                "Reload to see the current settings.",
            )
        doc = cast(dict[str, Any], doc)
        from . import provider_config_utils

        # its "could not resolve provider module" warnings would print to the
        # terminal on every save; the page reports problems itself
        quiet, provider_config_utils.console.quiet = provider_config_utils.console.quiet, True
        try:
            moved = secure_literal_secrets(settings, doc, scope)
            settings._write_scope(scope, doc)
        finally:
            provider_config_utils.console.quiet = quiet
    server.routing.refresh()  # providers may have changed: re-check model lists
    return {**build_state(settings, server.routing), "moved_keys": moved}


def save_key(server: SettingsServer, body: dict[str, Any]) -> dict[str, Any]:
    from .key_manager import KeyManager

    name = str(body.get("name") or "").strip()
    value = str(body.get("value") or "")
    if not system.ENV_NAME.match(name):
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Key names use letters, digits and underscores, e.g. ANTHROPIC_API_KEY.")
    if not value.strip() or any(c in value for c in "\r\n\"'"):
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Paste the key on its own: one line, no quotes.")
    KeyManager().save_key(name, value.strip())
    server.routing.refresh()  # a new key can make a provider's models listable
    return build_state(server.settings, server.routing)


def reveal_key(server: SettingsServer, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "")
    docs = [read_doc(server.settings.get_scope_path(s))[0] for s in EDITABLE_SCOPES]
    return {"value": system.reveal_key(name, {k["name"] for k in key_list(docs)})}


def provider_schema(server: SettingsServer, q: dict[str, str]) -> dict[str, Any]:
    module = q.get("module", "")
    # the source comes from settings, never from the request
    source = next((p.get("source") for p in server.settings.get_provider_overrides() if p.get("module") == module), None)
    return system.provider_schema(module, source)


# --------------------------------------------------------------------------
# HTTP layer: one route table, one place that turns errors into responses
# --------------------------------------------------------------------------

Handler = Callable[["SettingsServer", Any], dict[str, Any]]
ROUTES: dict[tuple[str, str], Handler] = {
    ("GET", "/api/state"): lambda s, q: build_state(s.settings, s.routing),
    ("GET", "/api/reset/categories"): lambda s, q: system.reset_info(),
    ("GET", "/api/provider-schema"): provider_schema,
    ("GET", "/api/jobs"): lambda s, q: s.jobs.get(q.get("id", "")),
    ("GET", "/api/routing/matrices"): lambda s, q: {"matrices": s.routing.matrices()},
    ("GET", "/api/routing/models"): lambda s, q: s.routing.models(q.get("provider", "")),
    ("POST", "/api/scope"): save_scope,
    ("POST", "/api/key"): save_key,
    ("POST", "/api/key/reveal"): reveal_key,
    ("POST", "/api/jobs"): lambda s, b: s.jobs.start(str(b.get("kind")), b.get("options") if isinstance(b.get("options"), dict) else {}),
    ("POST", "/api/routing/resolve"): lambda s, b: s.routing.resolve(str(b.get("matrix") or ""), b.get("overrides") or {}, bool(b.get("live"))),
    ("POST", "/api/routing/matrix"): lambda s, b: s.routing.save_matrix(b),
    ("POST", "/api/routing/matrix/delete"): lambda s, b: s.routing.delete_matrix(str(b.get("name") or "")),
}


class _RequestHandler(BaseHTTPRequestHandler):
    server: SettingsServer  # type: ignore[assignment]
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # quiet unless --verbose
        if self.server.verbose:
            super().log_message(format, *args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _send(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in {
            "Content-Type": ctype,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": CSP,
            **(headers or {}),
        }.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, default=str).encode(), "application/json")

    def _allowed(self, method: str) -> bool:
        h = self.headers
        return (
            h.get("Host") == self.server.host_header
            and h.get("Origin") in (None, self.server.origin)
            and h.get("Sec-Fetch-Site") in (None, "same-origin", "none")
            and (method == "GET" or h.get("X-Amplifier-Settings") == "1")
        )

    def _authed(self) -> bool:
        morsel = SimpleCookie(self.headers.get("Cookie", "")).get(self.server.cookie)
        return morsel is not None and secrets.compare_digest(morsel.value, self.server.session)

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not self._allowed(method):
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Request refused."})
        if method == "GET" and url.path == "/favicon.ico":
            return self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
        if method == "GET" and url.path == "/" and "t" in query:  # launch link -> cookie
            if not secrets.compare_digest(query["t"], self.server.launch_token):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "This link isn't valid. Open the link `amplifier settings` printed."})
            cookie = f"{self.server.cookie}={self.server.session}; HttpOnly; SameSite=Strict; Path=/"
            return self._send(HTTPStatus.SEE_OTHER, b"", "text/plain", {"Location": "/", "Set-Cookie": cookie})
        if not self._authed():
            return self._send(HTTPStatus.UNAUTHORIZED, b"Open the link printed by `amplifier settings` to use this page.", "text/plain; charset=utf-8")
        if method == "GET" and url.path in STATIC:
            name, ctype = STATIC[url.path]
            return self._send(HTTPStatus.OK, (UI_DIR / name).read_bytes(), ctype)
        route = ROUTES.get((method, url.path))
        if route is None:
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
        try:
            arg: Any = query
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise SettingsError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request too large.")
                arg = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(arg, dict):
                    raise SettingsError(HTTPStatus.BAD_REQUEST, "Expected a JSON object.")
            self._json(HTTPStatus.OK, route(self.server, arg))
        except SettingsError as e:
            self._json(e.status, {"error": str(e), **e.extra})
        except json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Expected a JSON object."})
        except Exception as e:  # noqa: BLE001 -- always answer with something readable
            logger.exception("settings page request failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Something went wrong: {type(e).__name__}: {e}"})


class SettingsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0, settings: AppSettings | None = None, verbose: bool = False) -> None:
        super().__init__(("127.0.0.1", port), _RequestHandler)
        self.settings = settings or AppSettings()
        self.routing = RoutingService(self.settings)
        self.jobs = system.Jobs()
        self.verbose = verbose
        self.launch_token = secrets.token_urlsafe(24)
        self.session = secrets.token_urlsafe(32)
        port = self.server_address[1]
        self.cookie = f"amplifier_settings_{port}"  # cookies don't separate by port; names do
        self.host_header = f"127.0.0.1:{port}"
        self.origin = f"http://{self.host_header}"

    @property
    def launch_url(self) -> str:
        return f"{self.origin}/?t={self.launch_token}"
