"""Machine-level pieces of the settings page: keys, provider schemas, status.

* Key values are masked everywhere; one is revealed only on request.
* Provider settings come only from the provider's own ConfigField declarations.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime
from http import HTTPStatus
from typing import Any, Callable, TypeVar


from .lib.env_vars import ENV_PATTERN

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Ends in a secret word. Only bare "key" needs a word boundary (monkey, turkey);
# max_tokens / max_output_tokens end in "tokens", so they never match.
_SECRET_WORDS = r"((^|_)key|apikey|token|secret|password|passphrase|credentials?)$"
SECRET_FIELD = re.compile(_SECRET_WORDS, re.IGNORECASE)  # config field names
SECRET_NAME = re.compile(_SECRET_WORDS, re.IGNORECASE)  # environment variable names
URL_CREDENTIALS = re.compile(r"://[^/?#@\s:]*:[^/?#@\s]+@")  # https://user:password@host


def is_secret_value(field: Any, value: Any) -> bool:
    """A literal (not only ${...}) secret: under a secret-named field, or a URL with a password."""
    if not isinstance(value, str):
        return False
    literal = ENV_PATTERN.sub("", value)
    named = isinstance(field, str) and bool(SECRET_FIELD.search(field))
    return bool(literal.strip()) and named or bool(URL_CREDENTIALS.search(literal))


DEFAULT_MATRIX = "balanced"  # what `amplifier routing` falls back to (commands/routing.py)
T = TypeVar("T")


def env_refs(value: str) -> list[str]:
    """Every variable a value references, as the runtime expands it: ${VAR} and ${VAR:default}."""
    return [m.group(1) for m in ENV_PATTERN.finditer(value)]


def defaults() -> dict[str, Any]:
    """What the CLI uses when settings don't say, from the CLI's own constants."""
    from .lib.bundle_loader.discovery import DEFAULT_BUNDLE
    from .utils.settings_manager import DEFAULT_SETTINGS

    return {
        "bundle": DEFAULT_BUNDLE,
        "update_check_hours": DEFAULT_SETTINGS["updates"]["check_frequency_hours"],
        "matrix": DEFAULT_MATRIX,
    }


class SettingsError(Exception):
    """An error the page can show as-is, with the HTTP status to send."""

    def __init__(self, status: HTTPStatus, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


def run_with_timeout(fn: Callable[[], T], timeout: float) -> T:
    """fn() on a daemon thread; TimeoutError if it takes longer. A hung call is left behind
    and never joined, so Ctrl+C doesn't wait on a provider that isn't answering."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 -- re-raised in the caller
            box["error"] = e

    t = threading.Thread(target=run, name="settings-provider-call", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError
    if "error" in box:
        raise box["error"]
    return box["value"]


# ---------------------------------------------------------------- keys
def mask(value: str) -> str:
    """At most a quarter of the value (ends only, never more than 10 characters); dots under 16."""
    v = value.strip()
    if len(v) < 16:
        return "•" * min(max(len(v), 4), 10)
    shown = min(len(v) // 4, 10)
    return f"{v[: shown - shown // 2]}…{v[len(v) - shown // 2 :]}"


def read_keys_file() -> dict[str, str]:
    """name -> value saved in keys.env (independent of the environment)."""
    from .key_manager import KeyManager

    return KeyManager().stored_items()


def reveal_key(name: str, allowed: set[str]) -> str:
    """Full value of one key the page lists. Never a bulk read."""
    if ENV_NAME.match(name) and name in allowed:
        value = read_keys_file().get(name) or os.environ.get(name)
        if value:
            return value
    raise SettingsError(HTTPStatus.NOT_FOUND, "There's no saved value for that key.")


# ---------------------------------------------------------------- provider schema
_FIELD_KEYS = ("id", "display_name", "field_type", "prompt", "choices", "default")


def provider_schema(module_id: str) -> dict[str, Any]:
    """{fields, origin[, error]}: the provider's own ConfigField declarations, nothing inferred.

    Loaded through the CLI's normal provider discovery; a load failure is reported, not hidden.
    """
    from .provider_loader import get_provider_info

    try:
        info = get_provider_info(module_id) or {}
    except Exception as e:  # noqa: BLE001 -- shown on the page instead of an empty form
        return {"fields": [], "origin": "none", "error": f"{type(e).__name__}: {e}"[:300]}
    fields = [{k: f[k] for k in _FIELD_KEYS if f.get(k) is not None} for f in info.get("config_fields") or [] if f.get("id")]
    return {"fields": fields, "origin": "provider" if fields else "none"}


# ---------------------------------------------------------------- home status
def status(settings: Any, routing: Any, keys: list[dict[str, Any]], merged: dict[str, Any]) -> dict[str, Any]:
    """What the home page shows, from the same sources the CLI uses."""
    from .utils.version import get_core_version, get_version_info

    try:
        version = {"display": get_version_info().display, "core": get_core_version()}
    except Exception:  # noqa: BLE001
        version = {"display": "unknown", "core": ""}

    have = {k["name"] for k in keys if k["stored"] or k["in_env"]}
    shadowed = {k["name"] for k in keys if k.get("shadowed")}
    overrides = settings.get_provider_overrides()
    # The default is the lowest config.priority (ties all count), as `amplifier provider list` marks it.
    priority = [c.get("priority", 100) if isinstance(c := p.get("config"), dict) else 100 for p in overrides]
    providers = []
    for p, pri in zip(overrides, priority):
        cfg = p.get("config") or {}
        # ${NAME} references by field. An unset *secret* is a real problem; an unset
        # optional value such as `base_url: ${ANTHROPIC_BASE_URL}` just means "use the
        # provider's default", so only secrets count as missing. An environment value
        # overriding a saved one matters for any field.
        refs = {k: m.group(1) for k, v in cfg.items() if isinstance(v, str) and (m := ENV_PATTERN.fullmatch(v.strip()))}
        providers.append({
            "name": routing.provider_name(p),
            "model": cfg.get("default_model"),
            "default": pri == min(priority),
            "missing_keys": [n for k, n in refs.items() if SECRET_FIELD.search(k) and n not in have],
            "shadowed_keys": [n for n in refs.values() if n in shadowed],
        })  # fmt: skip

    rcfg = merged.get("routing") or {}
    route: dict[str, Any] = {"matrix": rcfg.get("matrix", DEFAULT_MATRIX)}
    try:
        res = routing.resolve(route["matrix"], rcfg.get("overrides") or {}, live=False)
        route |= {
            "roles": len(res["roles"]),
            "unserved": [r["name"] for r in res["roles"] if r["problem"]],
            "pinned": [r["name"] for r in res["roles"] if r["overridden"]],
        }
    except SettingsError:
        route["missing"] = True

    upd = merged.get("updates") or {}
    hours_ago = None
    try:
        hours_ago = round((datetime.now() - datetime.fromisoformat(str(upd["last_check"]))).total_seconds() / 3600, 1)
    except (KeyError, ValueError, TypeError):
        pass

    bundle = merged.get("bundle") or {}
    return {
        "version": version,
        "providers": providers,
        "routing": route,
        "bundle": {"active": bundle.get("active") or defaults()["bundle"], "always": len(bundle.get("app") or [])},
        "updates": {"hours_ago": hours_ago, "every_hours": upd.get("check_frequency_hours")},
    }
