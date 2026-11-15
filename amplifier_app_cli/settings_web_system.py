"""Machine-level pieces of the settings page: keys, provider schemas, status, jobs.

* Key values are masked everywhere; one is revealed only on request.
* Provider settings come from the provider's own ConfigField declarations. When
  its code can't be imported here (its SDK lives only in a session's
  environment) they're read from the cached source with `ast` -- parsed, never run.
* Update and reset run the real `amplifier update` / `amplifier reset` from a
  fixed allow-list. They replace the program serving the page, so they start
  detached and log to a private folder that outlives the server.
"""

from __future__ import annotations

import ast
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
SECRET_FIELD = re.compile(r"(api_key|token|secret|password)$", re.IGNORECASE)
PLACEHOLDER_ONLY = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# Top-level settings keys the CLI reads. Other keys are labelled "not read by
# the Amplifier CLI" -- other Amplifier apps share the file and may read them.
CLI_TOP_LEVEL_KEYS = {
    "bundle", "config", "sources", "overrides", "modules", "routing",
    "updates", "ui", "provider", "configurator", "agents",
}  # fmt: skip


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
    """name -> value from keys.env (KeyManager only exposes names)."""
    path = get_amplifier_home() / "keys.env"
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def reveal_key(name: str, allowed: set[str]) -> str:
    """Full value of one key the page lists. Never a bulk read."""
    if ENV_NAME.match(name) and name in allowed:
        value = read_keys_file().get(name) or os.environ.get(name)
        if value:
            return value
    raise SettingsError(HTTPStatus.NOT_FOUND, "There's no saved value for that key.")


# ---------------------------------------------------------------- provider schema
def _provider_source_dir(module_id: str, source: str | None) -> Path | None:
    """A provider's checkout: a file:// source from settings, else the newest cache dir."""
    if source and source.startswith("file://"):
        p = Path(source.removeprefix("file://"))
        return p if p.is_dir() else None
    cache = get_amplifier_home() / "cache"
    pattern = re.compile(rf"^amplifier-module-{re.escape(module_id)}-[0-9a-f]{{8,}}$")
    dirs = [d for d in cache.iterdir() if d.is_dir() and pattern.match(d.name)] if cache.is_dir() else []
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def provider_schema(module_id: str, source: str | None = None) -> dict[str, Any]:
    """{fields: ConfigField-like dicts, reads: config keys the code reads, origin}."""
    fields: list[dict[str, Any]] = []
    origin = "none"
    try:  # importable: ask the provider itself
        from .provider_loader import get_provider_info

        info = get_provider_info(module_id) or {}
        fields = [dict(f) for f in info.get("config_fields") or []]
        origin = "provider" if fields else origin
    except Exception:  # noqa: BLE001 -- best effort; the source is the fallback
        fields = []
    reads: set[str] = set()
    src = _provider_source_dir(module_id, source)
    for py in src.rglob("*.py") if src else ():
        if any(part.startswith("test") for part in py.relative_to(src).parts):  # type: ignore[arg-type]
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        reads.update(re.findall(r"config\.get\(\s*[\"']([a-z_][a-z0-9_]*)[\"']", text))
        if origin == "provider" or "ConfigField(" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "ConfigField":
                f = {kw.arg: _literal(kw.value) for kw in node.keywords if kw.arg}
                if isinstance(f.get("id"), str):
                    fields.append(f)
    if fields and origin == "none":
        origin = "source"
    seen: set[str] = set()
    keep = ("id", "display_name", "field_type", "prompt", "choices", "default")
    return {
        "fields": [{k: f[k] for k in keep if f.get(k) is not None} for f in fields if not (f["id"] in seen or seen.add(f["id"]))],
        "reads": sorted(reads - {"priority", "raw", "filtered"}),
        "origin": origin,
    }


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

    have_key = {k["name"] for k in keys if k["stored"] or k["in_env"]}
    providers = []
    for i, p in enumerate(settings.get_provider_overrides()):
        cfg = p.get("config") or {}
        missing = [
            m.group(1)
            for k, v in cfg.items()
            if SECRET_FIELD.search(k) and isinstance(v, str)
            for m in [PLACEHOLDER_ONLY.match(v)]
            if m and m.group(1) not in have_key
        ]
        shadowed = [k["name"] for k in keys if k.get("shadowed") and any(f"${{{k['name']}}}" == v for v in cfg.values())]
        providers.append({"name": routing.provider_name(p), "model": cfg.get("default_model"), "default": i == 0, "missing_keys": missing, "shadowed_keys": shadowed})

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
        "unread_top_level": sorted(k for k in merged if k not in CLI_TOP_LEVEL_KEYS),
    }
