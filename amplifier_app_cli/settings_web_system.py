"""Machine-level pieces of the settings page: keys, provider schemas, status, jobs.

* Key values are masked everywhere; one is revealed only on request.
* Provider settings come only from the provider's own ConfigField declarations.
* Update and reset run the real `amplifier update` / `amplifier reset` from a
  fixed allow-list. They replace the program serving the page, so they start
  detached and log to a private folder that outlives the server.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from amplifier_foundation.paths.resolution import get_amplifier_home

from .commands import reset as reset_cli

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_FIELD = re.compile(r"(api_key|token|secret|password)$", re.IGNORECASE)  # config field names
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)$", re.IGNORECASE)  # environment variable names
PLACEHOLDER_ONLY = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class SettingsError(Exception):
    """An error the page can show as-is, with the HTTP status to send."""

    def __init__(self, status: HTTPStatus, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra


# ---------------------------------------------------------------- keys
def mask(value: str) -> str:
    """Same preview rule as amplifier-unified: 6…4 for long values, else dots."""
    v = value.strip()
    return f"{v[:6]}…{v[-4:]}" if len(v) >= 16 else "•" * min(max(len(v), 4), 10)


def read_keys_file() -> dict[str, str]:
    """name -> value saved in keys.env (independent of the environment)."""
    from .key_manager import KeyManager

    km = KeyManager()
    return {name: value for name in km.stored_keys() if (value := km.get_stored(name)) is not None}


def reveal_key(name: str, allowed: set[str]) -> str:
    """Full value of one key the page lists. Never a bulk read."""
    if ENV_NAME.match(name) and name in allowed:
        value = read_keys_file().get(name) or os.environ.get(name)
        if value:
            return value
    raise SettingsError(HTTPStatus.NOT_FOUND, "There's no saved value for that key.")


# ---------------------------------------------------------------- provider schema
_FIELD_KEYS = ("id", "display_name", "field_type", "prompt", "choices", "default")


def provider_schema(module_id: str, source: str | None = None) -> dict[str, Any]:
    """{fields, origin}: the provider's own ConfigField declarations, nothing inferred.

    `source` is the module's `source` from settings; a file:// checkout is loaded from
    that root, anything else through normal discovery (startup repairs the modules).
    """
    from .provider_loader import get_provider_info

    path = Path(source.removeprefix("file://")) if source and source.startswith("file://") else None
    try:
        info = (get_provider_info(module_id, source_path=path) if path else get_provider_info(module_id)) or {}
    except Exception:  # noqa: BLE001 -- an unloadable provider just has no form
        info = {}
    fields = [{k: f[k] for k in _FIELD_KEYS if f.get(k) is not None} for f in info.get("config_fields") or [] if f.get("id")]
    return {"fields": fields, "origin": "provider" if fields else "none"}


# ---------------------------------------------------------------- reset
def _size(path: Path, budget_s: float = 4.0) -> int | None:
    """Bytes under path; None if it takes too long to measure (shown as "large")."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total, start = 0, time.time()
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
        if time.time() - start > budget_s:
            return None
    return total


def reset_info() -> dict[str, Any]:
    """Reset's own categories, with sizes, plus what reset never touches."""
    home = get_amplifier_home()
    cats = []
    for cat in reset_cli.CATEGORY_ORDER:
        paths = [home / p for p in reset_cli.RESET_CATEGORIES[cat]]
        sizes = [_size(p) for p in paths]
        cats.append(
            {
                "name": cat,
                "description": reset_cli.CATEGORY_DESCRIPTIONS[cat],
                "exists": any(p.exists() for p in paths),
                "bytes": None if None in sizes else sum(s or 0 for s in sizes),
                "default_remove": cat in reset_cli.DEFAULT_REMOVE,
            }
        )
    return {"categories": cats, "unmanaged": reset_cli._get_unmanaged_files(), "home": str(home)}


# ---------------------------------------------------------------- jobs
class Jobs:
    """Runs allow-listed amplifier commands and keeps their output."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.log_dir = Path(tempfile.mkdtemp(prefix="amplifier-settings-jobs-"))  # private to this user

    @staticmethod
    def argv(kind: str, options: dict[str, Any]) -> list[str]:
        if kind == "update-check":
            args = ["update", "--check-only"]
        elif kind == "update":
            args = ["update", "-y"]
        elif kind in ("reset-plan", "reset"):
            cats = [c for c in options.get("remove", []) if c in reset_cli.RESET_CATEGORIES]
            if options.get("full"):
                args = ["reset", "--full"]
            elif cats:
                args = ["reset", "--remove", ",".join(cats)]  # --remove keeps everything else
            else:
                raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Choose at least one thing to reset.")
            args += ["-y"] + (["--dry-run"] if kind == "reset-plan" else [])
        else:
            raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, f"Unknown action: {kind}")
        return [sys.executable, "-m", "amplifier_app_cli", *args]

    def start(self, kind: str, options: dict[str, Any]) -> dict[str, Any]:
        argv = self.argv(kind, options)
        mutating = kind in ("update", "reset")
        with self._lock:
            if mutating and any(j["mutating"] and j["proc"].poll() is None for j in self._jobs.values()):
                raise SettingsError(HTTPStatus.CONFLICT, "Another update or reset is still running.")
            job_id = uuid.uuid4().hex[:12]
            log = self.log_dir / f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{job_id}.log"
            env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "100", "PYTHONUNBUFFERED": "1"}
            with open(log, "wb") as fh:
                proc = subprocess.Popen(  # noqa: S603 -- argv comes from the allow-list above
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=str(Path.home()),
                    start_new_session=mutating,  # survives this server being replaced
                )
            self._jobs[job_id] = {"proc": proc, "log": log, "started": time.time(), "mutating": mutating, "argv": argv}
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        j = self._jobs.get(job_id)
        if j is None:
            raise SettingsError(HTTPStatus.NOT_FOUND, "That job isn't known to this settings page.")
        code = j["proc"].poll()
        try:
            text = j["log"].read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        return {
            "id": job_id,
            "running": code is None,
            "exit_code": code,
            "output": text[-60_000:],
            "log": str(j["log"]),
            "command": "amplifier " + " ".join(j["argv"][3:]),
            "seconds": round(time.time() - j["started"]),
        }


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
    providers = []
    for i, p in enumerate(settings.get_provider_overrides()):
        cfg = p.get("config") or {}
        # ${NAME} references by field. An unset *secret* is a real problem; an unset
        # optional value such as `base_url: ${ANTHROPIC_BASE_URL}` just means "use the
        # provider's default", so only secrets count as missing. An environment value
        # overriding a saved one matters for any field.
        refs = {k: m.group(1) for k, v in cfg.items() if isinstance(v, str) and (m := PLACEHOLDER_ONLY.match(v))}
        providers.append({
            "name": routing.provider_name(p),
            "model": cfg.get("default_model"),
            "default": i == 0,
            "missing_keys": [n for k, n in refs.items() if SECRET_FIELD.search(k) and n not in have],
            "shadowed_keys": [n for n in refs.values() if n in shadowed],
        })  # fmt: skip

    rcfg = merged.get("routing") or {}
    route: dict[str, Any] = {"matrix": rcfg.get("matrix", "balanced")}
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
        "bundle": {"active": bundle.get("active") or "foundation", "always": len(bundle.get("app") or [])},
        "updates": {"hours_ago": hours_ago, "every_hours": upd.get("check_frequency_hours")},
    }


# ---------------------------------------------------------------- phone test
NTFY_TOPIC = "AMPLIFIER_NTFY_TOPIC"  # same variable `amplifier notify ntfy` uses


def notify_test(merged: dict[str, Any]) -> dict[str, Any]:
    """Send one test notification the way hooks-notify-push will (ntfy POST)."""
    import urllib.error
    import urllib.request

    topic = read_keys_file().get(NTFY_TOPIC) or os.environ.get(NTFY_TOPIC)
    if not topic:
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, "Create a channel first.")
    cfg = (merged.get("config") or {}).get("notifications") or {}
    server = str((cfg.get("ntfy") or {}).get("server") or (cfg.get("push") or {}).get("server") or "https://ntfy.sh").rstrip("/")
    if not server.startswith(("http://", "https://")):
        raise SettingsError(HTTPStatus.UNPROCESSABLE_ENTITY, f"The ntfy server must be an http(s) URL, not {server}.")
    req = urllib.request.Request(
        f"{server}/{topic}",
        data="If you can read this, Amplifier can reach your phone.".encode(),
        headers={"Title": "Amplifier test notification", "Tags": "white_check_mark"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 -- http(s) checked above
            return {"ok": True, "status": r.status, "server": server}
    except urllib.error.HTTPError as e:
        raise SettingsError(HTTPStatus.BAD_GATEWAY, f"{server} answered {e.code}. Check the channel name and server.") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise SettingsError(HTTPStatus.BAD_GATEWAY, f"Couldn't reach {server}: {getattr(e, 'reason', e)}") from e
