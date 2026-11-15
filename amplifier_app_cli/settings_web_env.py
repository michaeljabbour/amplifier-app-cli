"""Keep the settings server's view of keys and environment current, without restarts.

The page answers "what will Amplifier see the next time I run it?". A long-
running server would otherwise freeze that answer at launch: values exported by
~/.secrets or a shell profile, and keys saved to keys.env by another command,
would only show up after a restart.

So, at most every few seconds (and immediately when asked):
  1. read the environment a new terminal would get, by running the user's login
     shell once (`$SHELL -ilc 'env -0'`, the same trick editors use);
  2. apply Amplifier's own rule to every variable settings or keys.env mention:
     a value from the shell wins, otherwise keys.env (KeyManager never overrides
     a variable that's already set).
If the shell can't be read (slow or noisy startup files), keys.env is still
re-read and the last good shell snapshot is kept.
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
MAX_AGE_S = 8.0
SHELL_TIMEOUT_S = 12.0


def login_shell_env() -> dict[str, str]:
    """Environment of a fresh login shell, as a new terminal tab would have it."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    base = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "SHELL", "LANG") if k in os.environ}
    base |= {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "dumb",
        "DISABLE_AUTO_UPDATE": "true",  # oh-my-zsh: never prompt to update
        "AMPLIFIER_SETTINGS_ENV_PROBE": "1",  # lets a profile skip slow work if it wants to
    }
    out = subprocess.run(  # noqa: S603 -- the user's own login shell, as a new terminal runs it
        [shell, "-ilc", "env -0"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=SHELL_TIMEOUT_S,
        env=base,
        cwd=str(Path.home()),
        check=False,
    ).stdout.decode(errors="replace")
    chunks = out.split("\0")
    # anything a startup file printed lands in front of the first variable: drop it
    starts = [m.start() for m in re.finditer(r"(?:^|(?<=\n))[A-Za-z_][A-Za-z0-9_]*=", chunks[0])]
    chunks[0] = chunks[0][starts[-1]:] if starts else ""
    env: dict[str, str] = {}
    for chunk in chunks:
        name, sep, value = chunk.partition("=")
        if sep and NAME.match(name):
            env[name] = value
    if "HOME" not in env:
        raise RuntimeError("login shell produced no environment")
    return env


class LiveEnv:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._checked = 0.0
        self._shell: dict[str, str] | None = None
        self.shell_error: str | None = None
        self.shell_read_at: float | None = None
        self.fingerprint = ""

    def refresh(self, names: set[str], force: bool = False) -> bool:
        """Bring os.environ up to date for `names`; return True if anything changed."""
        with self._lock:
            if not force and time.monotonic() - self._checked < MAX_AGE_S:
                return False
            self._checked = time.monotonic()
            try:
                self._shell = login_shell_env()
                self.shell_error, self.shell_read_at = None, time.time()
            except (OSError, subprocess.SubprocessError, RuntimeError) as e:
                self.shell_error = f"Couldn't read your login shell's environment ({e}); using the last known values."
            stored = read_keys_file()
            for name in names | set(stored):
                if self._shell is not None:
                    value = self._shell.get(name) or stored.get(name)
                else:  # no shell snapshot yet: keep what we have, fill from keys.env
                    value = os.environ.get(name) or stored.get(name)
                if value:
                    os.environ[name] = value
                else:
                    os.environ.pop(name, None)
            digest = hashlib.sha256("\0".join(f"{n}={os.environ.get(n, '')}" for n in sorted(names | set(stored))).encode()).hexdigest()
            changed, self.fingerprint = digest != self.fingerprint, digest
            return changed

    def shell_value(self, name: str) -> str | None:
        """What a new terminal sets `name` to (None if it doesn't, or unknown)."""
        return (self._shell or {}).get(name)

    def info(self) -> dict[str, Any]:
        return {"shell_read_at": self.shell_read_at, "shell_error": self.shell_error}


def where_set(name: str) -> list[str]:
    """Startup files (file:line) where a new login shell sets `name`. Values are never returned."""
    if not NAME.match(name):
        return []
    shell = os.environ.get("SHELL") or "/bin/zsh"
    ps4 = "+%x:%I> " if shell.endswith("zsh") else "+${BASH_SOURCE}:${LINENO}> "
    env = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "SHELL") if k in os.environ}
    env |= {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TERM": "dumb", "DISABLE_AUTO_UPDATE": "true", "PS4": ps4}
    try:
        trace = subprocess.run(  # noqa: S603 -- the user's own login shell, traced
            [shell, "-ilxc", "exit"], stdin=subprocess.DEVNULL, capture_output=True, timeout=SHELL_TIMEOUT_S, env=env, cwd=str(Path.home()), check=False
        ).stderr.decode(errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    home = str(Path.home())
    for line in trace.splitlines():
        m = re.match(r"^\++([^:>]+):(\d+)> .*\b" + re.escape(name) + r"=", line)
        if m:
            where = f"{m.group(1).replace(home, '~', 1)} line {m.group(2)}"
            if where not in found:
                found.append(where)
    return found
