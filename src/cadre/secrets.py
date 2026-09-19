"""API keys: where they live, and how they are kept out of everything else (ADR-011).

Lookup order for a provider whose key reference is ``groq``:

1. ``CADRE_KEY_GROQ`` in the environment
2. the preset's conventional variable, e.g. ``GROQ_API_KEY``
3. the OS credential store (Windows Credential Manager / Keychain / Secret Service),
   service ``cadre``, user ``groq``

Every value that is loaded is registered with :data:`REDACTOR`, which the store runs over
event payloads and error text before anything is written down.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

SERVICE = "cadre"
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH)", re.I)
_MIN_SECRET = 8  # shorter strings are too likely to collide with ordinary text

# Full-length provider API-key shapes: Groq / Google (two formats) / OpenRouter. This pattern is
# kept byte-for-byte identical to `tools/check_history.py`'s KEYS (a test asserts they match), so
# the same scan that guards commit history also guards anything written to memory (FR-24, ADR-036).
KEY_SHAPES = re.compile(r"gsk_[A-Za-z0-9]{40,}|AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{50}|sk-or-v1-[0-9a-f]{64}")


def looks_like_key(text: str) -> bool:
    """True if `text` contains a full-length provider API-key shape (see KEY_SHAPES)."""
    return bool(KEY_SHAPES.search(text or ""))


class Redactor:
    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str | None) -> None:
        if value and len(value) >= _MIN_SECRET:
            with self._lock:
                self._values.add(value)

    def redact(self, text: str) -> str:
        if not text:
            return text
        for v in self._values:
            if v in text:
                text = text.replace(v, "***")
        return text

    def redact_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: self.redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.redact_obj(v) for v in obj]
        return obj

    def known(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._values)


REDACTOR = Redactor()


def env_name(ref: str) -> str:
    return "CADRE_KEY_" + re.sub(r"[^A-Za-z0-9]", "_", ref).upper()


def _keyring_enabled() -> bool:
    return os.environ.get("CADRE_NO_KEYRING", "") not in ("1", "true", "yes")


class SecretStore:
    def get(self, ref: str, env_hint: str | None = None) -> str | None:
        value = self._lookup(ref, env_hint)[1]
        REDACTOR.register(value)
        return value

    def where(self, ref: str, env_hint: str | None = None) -> str | None:
        """Where the key would come from — never the key itself."""
        return self._lookup(ref, env_hint)[0]

    def _lookup(self, ref: str, env_hint: str | None) -> tuple[str | None, str | None]:
        for name in (env_name(ref), env_hint):
            if name and os.environ.get(name):
                return f"env:{name}", os.environ[name]
        if _keyring_enabled():
            try:
                import keyring

                value = keyring.get_password(SERVICE, ref)
                if value:
                    return "keyring", value
            except Exception:  # no backend on this machine: env vars still work
                pass
        return None, None

    def set(self, ref: str, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty key")
        if not _keyring_enabled():
            raise RuntimeError(
                f"the OS credential store is disabled here; set {env_name(ref)} in the environment")
        try:
            import keyring

            keyring.set_password(SERVICE, ref, value)
        except Exception as e:
            raise RuntimeError(
                f"could not use the OS credential store ({type(e).__name__}); "
                f"set {env_name(ref)} in the environment instead") from None
        REDACTOR.register(value)
        return "keyring"

    def delete(self, ref: str) -> bool:
        if not _keyring_enabled():
            return False
        try:
            import keyring

            keyring.delete_password(SERVICE, ref)
            return True
        except Exception:
            return False


def looks_secret(name: str) -> bool:
    """A variable *named* like a credential; never passed to a check."""
    return bool(_SECRET_NAME.search(name))


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a check subprocess gets: nothing that looks like a credential.

    Drops variables whose *name* looks secret and any whose *value* is a key Cadre has loaded,
    so model-written code under test cannot read the keys that paid for it.
    """
    known = REDACTOR.known()
    env = {k: v for k, v in os.environ.items()
           if not _SECRET_NAME.search(k) and v not in known}
    env.update(extra or {})
    return env
