"""Step-by-step connection diagnosis for one provider (the page's "Diagnose").

Answers "why can't Amplifier reach this provider?" the way a person would
check by hand, and shows its working:

  1. provider code   -- is the module installed and loadable here?
  2. settings        -- what config will it run with, with ${VAR}s resolved?
  3. API key         -- which variable, where its value comes from (keys.env vs
                        the environment), and whether the two disagree
  4. direct check    -- call the provider's model-list endpoint ourselves with
                        each candidate key: HTTP status and the provider's message
  5. via Amplifier   -- the same call `amplifier provider test` makes, with a
                        longer timeout and the provider/HTTP logs captured
                        (retries, status codes) instead of discarded
  6. what to try     -- suggestions derived from 1-5

Secrets never leave masked: key values, Authorization headers and anything
key-shaped in captured logs are replaced before the report is returned.
"""

from __future__ import annotations

import concurrent.futures
import importlib.metadata
import importlib.util
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .lib.settings import AppSettings
from .provider_manager import resolve_provider_entry
from .settings_web_system import SECRET_FIELD, mask, read_keys_file

VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
KEYLIKE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9_\-\.]{8,}|x-api-key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{8,})")
# What to listen to while the provider runs, and which lines are worth showing.
# httpx2/httpcore2 are the copies of httpx/httpcore bundled with Amplifier.
LOG_SOURCES = {
    "httpx": logging.INFO, "httpx2": logging.INFO,  # one line per HTTP request, with its status
    "openai": logging.DEBUG, "anthropic": logging.DEBUG, "google_genai": logging.DEBUG, "ollama": logging.DEBUG,
    "httpcore.connection": logging.DEBUG, "httpcore2.connection": logging.DEBUG,
}
WORTH_SHOWING = re.compile(r"HTTP Request|retr|status|error|fail|timeout|exception|connect_tcp\.(?:complete|failed)|start_tls\.(?:complete|failed)", re.IGNORECASE)
LIST_TIMEOUT_S = 30.0

# How to call each family's model list directly: (default base URL, path, auth header style)
DIRECT = {
    "provider-openai": ("https://api.openai.com/v1", "/models", "bearer"),
    "provider-azure-openai": (None, "/models", "bearer"),
    "provider-vllm": (None, "/models", "bearer"),
    "provider-chat-completions": (None, "/models", "bearer"),
    "provider-anthropic": ("https://api.anthropic.com", "/v1/models", "anthropic"),
    "provider-gemini": ("https://generativelanguage.googleapis.com/v1beta", "/models", "google"),
    "provider-ollama": ("http://localhost:11434", "/api/tags", None),
}


def _scrub(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, mask(s))
    return KEYLIKE.sub(lambda m: mask(m.group(0)), text)


def _resolve(value: Any) -> tuple[Any, list[str], list[str]]:
    """Expand ${VAR} the way the provider will (from the environment)."""
    if not isinstance(value, str):
        return value, [], []
    names = VAR.findall(value)
    missing = [n for n in names if not os.environ.get(n)]
    return VAR.sub(lambda m: os.environ.get(m.group(1), ""), value), names, missing


def _step(name: str, status: str, summary: str, **detail: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "summary": summary, **detail}


def _direct_call(url: str, style: str | None, key: str | None) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        return {"http": None, "seconds": 0, "message": f"Not an http(s) URL: {url}"}
    headers = {"User-Agent": "amplifier-settings-diagnose"}
    if key and style == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif key and style == "anthropic":
        headers |= {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif key and style == "google":
        headers["x-goog-api-key"] = key
    started = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:  # noqa: S310 -- http(s) URL from settings
            body = r.read(200_000)
            count = None
            try:
                data = json.loads(body)
                items = data.get("data") or data.get("models") or []
                count = len(items) if isinstance(items, list) else None
            except ValueError:
                pass
            return {"http": r.status, "seconds": round(time.monotonic() - started, 2), "models": count}
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            err = json.loads(e.read(20_000) or b"{}")
            msg = str((err.get("error") or {}).get("message") if isinstance(err.get("error"), dict) else err.get("error") or err.get("message") or "")
        except ValueError:
            pass
        return {"http": e.code, "seconds": round(time.monotonic() - started, 2), "message": msg[:300]}
    except urllib.error.URLError as e:
        return {"http": None, "seconds": round(time.monotonic() - started, 2), "message": f"Couldn't connect: {e.reason}"}
    except TimeoutError:
        return {"http": None, "seconds": round(time.monotonic() - started, 2), "message": "No answer within 15 seconds."}


def diagnose(settings: AppSettings, name: str, live_env: Any = None) -> dict[str, Any]:
    started = time.monotonic()
    steps: list[dict[str, Any]] = []
    tips: list[str] = []
    secrets: list[str] = []

    entry = resolve_provider_entry(settings.get_provider_overrides(), name)
    if entry is None:
        return {"provider": name, "steps": [_step("Provider", "fail", f"No provider called {name} in your settings.")], "tips": [], "seconds": 0}
    module = str(entry.get("module", ""))
    if not module.startswith("provider-"):
        module = f"provider-{module}"
    cfg = dict(entry.get("config") or {})

    # 1. provider code ---------------------------------------------------------
    pkg = "amplifier_module_" + module.replace("-", "_")
    try:
        version = importlib.metadata.version(pkg.replace("_", "-"))
    except importlib.metadata.PackageNotFoundError:
        version = None
    found = importlib.util.find_spec(pkg) is not None
    from .provider_loader import load_provider_class

    cls = load_provider_class(module) if found else None
    if cls is None:
        steps.append(_step("Provider code", "fail", f"{module} isn't installed in this Amplifier, so it can't be used here.", module=module, version=version, source=entry.get("source")))
        tips.append(f"Run `amplifier provider install {module.removeprefix('provider-')}`, or start a session once; Amplifier reinstalls missing providers on start.")
    else:
        steps.append(_step("Provider code", "ok", f"{module}{f' {version}' if version else ''} is installed and loads.", module=module, version=version, source=entry.get("source")))

    # 2. settings ---------------------------------------------------------------
    shown, unset = {}, []
    for k, v in sorted(cfg.items()):
        if SECRET_FIELD.search(k):
            continue
        resolved, names, missing = _resolve(v)
        shown[k] = {"value": v, "resolved": resolved if names else None}
        unset += missing
    base_default, path, style = DIRECT.get(module, (None, None, None))
    base_raw = cfg.get("base_url")
    base, _, base_missing = _resolve(base_raw) if base_raw else (None, [], [])
    base = (base or "").strip() or base_default
    summary = f"Calls {base}." if base else "No base URL; the provider's built-in default is used."
    if unset:
        summary += f" Not set in the environment: {', '.join(sorted(set(unset)))} (the provider falls back to its default)."
    steps.append(_step("Settings", "ok" if base or not base_missing else "warn", summary, config=shown))

    # 3. API key ------------------------------------------------------------------
    key_field = next((k for k in cfg if SECRET_FIELD.search(k)), None)
    stored = read_keys_file()
    candidates: list[tuple[str, str]] = []
    if key_field is None:
        steps.append(_step("API key", "skip", "This provider has no API key setting."))
    else:
        raw = cfg[key_field]
        names = VAR.findall(raw) if isinstance(raw, str) else []
        var = names[0] if names else None
        env_val = os.environ.get(var) if var else None
        file_val = stored.get(var) if var else None
        if not var:
            candidates = [("in your settings file", str(raw))] if raw else []
            steps.append(_step("API key", "warn" if raw else "fail", "The key is written directly in settings. Saving on this page moves it to keys.env." if raw else "No key set.", field=key_field))
        else:
            sources = []
            if file_val:
                sources.append({"where": "keys.env", "preview": mask(file_val), "length": len(file_val)})
            set_by: list[str] = []
            if env_val and env_val != file_val:
                from .settings_web_env import where_set

                set_by = where_set(var)
                from_shell = live_env is not None and live_env.shell_value(var) == env_val
                where = f"your shell ({', '.join(set_by)})" if set_by else "your shell startup files" if from_shell else "your environment (launchctl or the program that started this page)"
                sources.append({"where": where, "preview": mask(env_val), "length": len(env_val)})
            used = env_val or file_val
            if not used:
                steps.append(_step("API key", "fail", f"`{var}` is empty: it's not in keys.env or your environment.", variable=var, sources=sources))
                tips.append(f"Add `{var}` on the API keys page.")
            elif env_val and file_val and env_val != file_val:
                steps.append(_step("API key", "warn", f"`{var}` has two different values. Amplifier uses the one from your environment, because it never overrides an existing variable.", variable=var, sources=sources, used="your environment", set_by=set_by))
                candidates = [("from your environment (the one in use)", env_val), ("from keys.env", file_val)]
            else:
                where = "keys.env" if file_val else "your environment"
                steps.append(_step("API key", "ok", f"`{var}` comes from {where} ({mask(used)}, {len(used)} characters).", variable=var, sources=sources, used=where))
                candidates = [(f"from {where}", used)]
        secrets += [v for _, v in candidates]

    # 4. direct check -------------------------------------------------------------
    direct_results = []
    if base and path:
        url = base.rstrip("/") + path
        for label, key in candidates or [("without a key", None)]:
            res = _direct_call(url, style, key)
            direct_results.append({"key": label, **res})
        ok = [r for r in direct_results if r["http"] and 200 <= r["http"] < 300]
        status = "ok" if direct_results and direct_results[0]["http"] and 200 <= direct_results[0]["http"] < 300 else "fail" if not ok else "warn"
        first = direct_results[0]
        if status == "ok":
            summary = f"{url} answered {first['http']} in {first['seconds']}s" + (f" with {first['models']} models." if first.get("models") is not None else ".")
        elif first["http"] in (401, 403):
            summary = f"{url} rejected the key in use ({first['http']}): {first.get('message') or 'unauthorized'}"
        elif first["http"] == 404:
            root = _direct_call(base.split("/v1")[0].rstrip("/") + "/", None, None)
            if root.get("http") == 404:
                summary = f"{url} returned 404, and so does the server's root. Nothing is running behind this URL: the server, pod or tunnel is probably stopped."
                first["nothing_running"] = True
            else:
                summary = f"{url} returned 404, but the server itself answers. The base URL path is probably wrong."
        elif first["http"]:
            summary = f"{url} answered {first['http']}: {first.get('message') or 'unexpected response'}"
        else:
            summary = f"{url}: {first.get('message')}"
        steps.append(_step("Direct API check", status, summary, url=url, results=direct_results))
        if len(direct_results) > 1 and first["http"] in (401, 403) and any(r["http"] == 200 for r in direct_results[1:]):
            var = steps[2].get("variable", "the key")
            set_by = steps[2].get("set_by") or []
            if set_by:
                tips.insert(0, f"The saved key in keys.env works; the one set in {', '.join(set_by)} doesn't. Update or remove `export {var}=` there. This page picks the change up by itself within a few seconds.")
            else:
                tips.insert(0, f"The saved key in keys.env works; the one in your environment doesn't. Run `launchctl unsetenv {var}` (macOS), then restart `amplifier settings` from a new terminal.")
        elif first["http"] in (401, 403):
            tips.append("The provider rejected the key. Create a new key in the provider's dashboard and save it on the API keys page.")
        elif first.get("nothing_running"):
            tips.append("Start the server (or pod) again, or point this provider at one that's running. If its address changed, update the base URL.")
        elif first["http"] == 404:
            tips.append("Check the base URL on the Providers page; for OpenAI-compatible servers it usually ends in `/v1`.")
        elif first["http"] is None:
            tips.append("Couldn't reach the server. Check your network, VPN or proxy, and the base URL.")
    else:
        steps.append(_step("Direct API check", "skip", "No direct check for this provider type, or no base URL to call."))

    # 5. through Amplifier, with logs ---------------------------------------------
    if cls is not None:
        records: list[str] = []

        t_start = time.monotonic()

        class Collect(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                msg = record.getMessage()
                provider_own = record.name.startswith(pkg)
                if provider_own or record.levelno >= logging.WARNING or WORTH_SHOWING.search(msg):
                    records.append(f"+{time.monotonic() - t_start:5.1f}s {record.name}: {msg}")

        sources = {pkg: logging.INFO, **LOG_SOURCES}
        loggers = [logging.getLogger(n) for n in sources]
        saved = [(lg, lg.level, lg.propagate) for lg in loggers]
        handler = Collect(logging.DEBUG)
        for lg, level in zip(loggers, sources.values()):
            lg.addHandler(handler)
            lg.setLevel(level)
            lg.propagate = False
        from .provider_loader import get_provider_models

        t0 = time.monotonic()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            models = pool.submit(get_provider_models, module, collected_config=cfg, require_catalog=True).result(timeout=LIST_TIMEOUT_S)
            ids = [str(getattr(m, "id", None) or m) for m in models]
            steps.append(_step("Through Amplifier", "ok", f"Listed {len(ids)} models in {time.monotonic() - t0:.1f}s, the same call `amplifier provider test` makes.", models=ids[:50]))
        except concurrent.futures.TimeoutError:
            retries = sum("retrying" in r.lower() for r in records)
            steps.append(_step("Through Amplifier", "fail", f"No answer after {LIST_TIMEOUT_S:.0f}s{f'; the provider retried {retries} times' if retries else ''}. The log below shows what it was doing."))
            if retries:
                tips.append("The provider keeps retrying a failing request, so the check runs out of time. The direct check above shows the underlying error.")
        except Exception as e:  # noqa: BLE001 -- this is the error we're here to show
            cause = e.__cause__ or e.__context__
            why = f" Caused by {type(cause).__name__}: {cause}" if cause and str(cause) not in str(e) else ""
            statuses = re.findall(r'HTTP Request: (\w+ \S+) "HTTP/[\d.]+ (\d{3}[^"]*)"', "\n".join(records))
            seen = f" Requests made: {'; '.join(f'{u} -> {s}' for u, s in statuses[-4:])}." if statuses else ""
            steps.append(_step("Through Amplifier", "fail", f"{type(e).__name__} after {time.monotonic() - t0:.1f}s: {e}.{why}{seen}", error_type=type(e).__name__))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            for lg, level, propagate in saved:
                lg.removeHandler(handler)
                lg.setLevel(level)
                lg.propagate = propagate
        steps[-1]["log"] = [_scrub(r, secrets)[:400] for r in records[-60:]]
        for r in records:
            m = re.search(r"Config key '(\w+)' is a DEPRECATED alias for '(\w+)'", r)
            if m and not any(m.group(1) in t for t in tips):
                tips.append(f"Rename `{m.group(1)}` to `{m.group(2)}` in this provider's settings (there's a button on Home).")
    else:
        steps.append(_step("Through Amplifier", "skip", "Skipped: the provider code isn't available."))

    if not tips and all(s["status"] in ("ok", "skip") for s in steps):
        tips.append("Everything checks out. If sessions still fail, the problem is with a specific model or request; run a session with `--verbose`.")
    report = {"provider": name, "module": module, "steps": steps, "tips": tips, "seconds": round(time.monotonic() - started, 1)}
    return json.loads(_scrub(json.dumps(report, default=str), secrets))
