"""Keep the settings server's view of keys and environment current, without restarts.

The page answers "what will Amplifier see when it runs from here?". Amplifier
only reads its process environment, filled in by KeyManager from keys.env for
names the environment doesn't already have. So:

  * the environment this server was launched with is the truth. A name in it
    (even exported as "") keeps its launch value;
  * keys.env is re-read on every refresh (the page polls), so a key saved
    elsewhere needs no restart; a name that leaves keys.env and was not in the
    launch environment is removed.

KeyManager has already copied keys.env into os.environ by the time the server
starts, so a launch value equal to keys.env's is taken to have come from it.
"""

from __future__ import annotations

import hashlib
import os
import threading

from .settings_web_system import read_keys_file


class LiveEnv:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        stored = read_keys_file()
        self._launch = {k: v for k, v in os.environ.items() if stored.get(k) != v}
        self._names: set[str] = set()  # every name refresh() has managed
        self.fingerprint = ""

    def refresh(self, names: set[str]) -> bool:
        """Bring os.environ up to date for `names` and keys.env; return True if anything changed."""
        with self._lock:
            stored = read_keys_file()
            self._names |= names | set(stored)
            for name in self._names:
                if name in self._launch:
                    os.environ[name] = self._launch[name]
                elif name in stored:
                    os.environ[name] = stored[name]
                else:
                    os.environ.pop(name, None)
            digest = hashlib.sha256("\0".join(f"{n}={os.environ.get(n)}" for n in sorted(self._names)).encode()).hexdigest()
            changed, self.fingerprint = digest != self.fingerprint, digest
            return changed
