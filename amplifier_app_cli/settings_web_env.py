"""Keep the settings server's view of keys and environment current, without restarts.

The page answers "what will Amplifier see the next time I run it?". Two sources
feed that answer, and they cost very different amounts to read:

  * keys.env -- a small file, re-read on every refresh (the page polls often);
  * the user's login shell (`$SHELL -ilc 'env -0'`, the trick editors use) --
    slow, so it is read once at the first refresh and again only on `force=True`
    (the page forces on window focus and before diagnosing a provider).

Amplifier's rule is applied to every variable settings or keys.env mention: a
value from the shell wins, otherwise keys.env (KeyManager never overrides a set
variable); a name neither has is removed. Without a shell snapshot (turned off,
or it failed), the environment this process started with stands in for it.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .settings_web_system import read_keys_file

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHELL_TIMEOUT_S = 12.0


def shell_env_enabled() -> bool:
    """Sandboxes turn this off so a developer's real shell keys never leak in."""
    return os.environ.get("AMPLIFIER_SETTINGS_SHELL_ENV", "").lower() not in ("0", "off", "false", "no")


def _run_shell(args: list[str], extra: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    """Run the user's login shell with a minimal, quiet environment."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    env = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "SHELL", "LANG") if k in os.environ}
    env |= {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TERM": "dumb", "DISABLE_AUTO_UPDATE": "true", **extra}
    return subprocess.run(  # noqa: S603 -- the user's own login shell, as a new terminal runs it
        [shell, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=SHELL_TIMEOUT_S,
        env=env,
        cwd=str(Path.home()),
        check=False,
    )


def login_shell_env() -> dict[str, str]:
    """Environment of a fresh login shell, as a new terminal tab would have it."""
    chunks = _run_shell(["-ilc", "env -0"], {"AMPLIFIER_SETTINGS_ENV_PROBE": "1"}).stdout.decode(errors="replace").split("\0")
    # anything a startup file printed lands in front of the first variable: drop it
    starts = [m.start() for m in re.finditer(r"(?:^|(?<=\n))[A-Za-z_][A-Za-z0-9_]*=", chunks[0])]
    chunks[0] = chunks[0][starts[-1] :] if starts else ""
    env = {name: value for name, sep, value in (c.partition("=") for c in chunks) if sep and NAME.match(name)}
    if "HOME" not in env:
        raise RuntimeError("login shell produced no environment")
    return env


def where_set(name: str) -> list[str]:
    """Startup files (file:line) where a new login shell sets `name`. Values are never returned."""
    if not NAME.match(name) or not shell_env_enabled():
        return []
    ps4 = "+%x:%I> " if (os.environ.get("SHELL") or "/bin/zsh").endswith("zsh") else "+${BASH_SOURCE}:${LINENO}> "
    try:
        trace = _run_shell(["-ilxc", "exit"], {"PS4": ps4}).stderr.decode(errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    home, found = str(Path.home()), []
    for m in re.finditer(r"^\++([^:>\n]+):(\d+)> .*\b" + re.escape(name) + "=", trace, re.MULTILINE):
        where = f"{m.group(1).replace(home, '~', 1)} line {m.group(2)}"
        if where not in found:
            found.append(where)
    return found


class LiveEnv:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._base: dict[str, str] | None = None  # shell snapshot, or the launch environment
        self._shell: dict[str, str] | None = None
        self.shell_error: str | None = None
        self.shell_read_at: float | None = None
        self.fingerprint = ""

    def refresh(self, names: set[str], force: bool = False, *, shell: bool = True) -> bool:
        """Bring os.environ up to date for `names`; return True if anything changed.

        keys.env is always re-read. The login shell is read at the first refresh
        and when `force` is set -- never when `shell` is False.
        """
        with self._lock:
            stored = read_keys_file()
            if self._base is None:
                # values equal to keys.env were most likely loaded from it at launch
                self._base = {k: v for k, v in os.environ.items() if stored.get(k) != v}
            if shell and (force or self.shell_read_at is None and self.shell_error is None):
                self._read_shell()
            for name in names | set(stored):
                value = self._base.get(name) or stored.get(name)
                if value:
                    os.environ[name] = value
                else:
                    os.environ.pop(name, None)
            digest = hashlib.sha256("\0".join(f"{n}={os.environ.get(n, '')}" for n in sorted(names | set(stored))).encode()).hexdigest()
            changed, self.fingerprint = digest != self.fingerprint, digest
            return changed

    def _read_shell(self) -> None:
        try:
            if not shell_env_enabled():
                raise RuntimeError("reading your login shell is turned off (AMPLIFIER_SETTINGS_SHELL_ENV=off)")
            self._shell = self._base = login_shell_env()
            self.shell_error, self.shell_read_at = None, time.time()
        except (OSError, subprocess.SubprocessError, RuntimeError) as e:
            self.shell_error = f"Couldn't read your login shell's environment ({e}); using the last known values."

    def shell_value(self, name: str) -> str | None:
        """What a new terminal sets `name` to (None if it doesn't, or unknown)."""
        return (self._shell or {}).get(name)

    def info(self) -> dict[str, Any]:
        return {"shell_read_at": self.shell_read_at, "shell_error": self.shell_error}
