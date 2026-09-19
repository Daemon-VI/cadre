"""Users, roles and API tokens (M13, FR-23, ADR-032/033).

Cadre is single-tenant until an admin adds a second user. Every request carries a bearer token;
a token belongs to a user, and the user's role decides what the request may do. Tokens are stored
**hashed** (SHA-256) — the secret is shown once, when it is minted, and never again.

Roles are fixed:
    viewer  read only
    member  read, start/cancel runs, decide approvals
    admin   everything, plus managing users, tokens and providers, and reading the audit log

The owner's existing `CADRE_HOME/token` becomes the bootstrap admin's token the first time the
server sees a database with no users, so a single-user install keeps working untouched.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import Store

ROLES = ("viewer", "member", "admin")
#: what each role may do; a route asks for one capability
CAPABILITIES: dict[str, frozenset[str]] = {
    "viewer": frozenset({"read"}),
    "member": frozenset({"read", "run", "approve"}),
    "admin": frozenset({"read", "run", "approve", "providers", "admin"}),
}
ALL_CAPS = frozenset().union(*CAPABILITIES.values())
USER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
BOOTSTRAP_ID = "owner"


class AccountError(ValueError):
    """A bad account operation (unknown user, duplicate id, last admin, …)."""


def token_hash(secret: str) -> str:
    return hashlib.sha256(secret.strip().encode("utf-8")).hexdigest()


def can(role: str, capability: str) -> bool:
    return capability in CAPABILITIES.get(role, frozenset())


class User:
    """A resolved caller. `system` is the local process acting with no token (CLI/scheduler)."""

    def __init__(self, id: str, name: str, role: str, token_id: str | None = None):
        self.id, self.name, self.role, self.token_id = id, name, role, token_id

    @property
    def capabilities(self) -> list[str]:
        return sorted(CAPABILITIES.get(self.role, frozenset()))

    def can(self, capability: str) -> bool:
        return can(self.role, capability)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role, "capabilities": self.capabilities}


#: the local process itself, when no token is presented (a CLI command, the scheduler). It has
#: every capability — it is already running as the owner — but it is a distinct audit actor.
SYSTEM = User("system", "the local process", "admin")


class Accounts:
    """Account operations over the store. Audits every change."""

    def __init__(self, store: Store):
        self.store = store

    # -------------------------------------------------------------- bootstrap
    def bootstrap(self, owner_token: str | None) -> None:
        """Give a database with no users an admin whose token is the owner's `CADRE_HOME/token`,
        so an existing single-user install keeps authenticating."""
        if self.store.user_count() or not owner_token:
            return
        self.store.create_user(BOOTSTRAP_ID, "Owner", "admin")
        self.store.add_token("bootstrap", BOOTSTRAP_ID, token_hash(owner_token), "CADRE_HOME/token")
        self.store.audit("system", "user.bootstrapped", BOOTSTRAP_ID, {"role": "admin"})

    # -------------------------------------------------------------- resolve
    def resolve(self, secret: str | None) -> User | None:
        """The user a bearer token belongs to, or None. Updates the token's last-used time."""
        if not secret:
            return None
        row = self.store.user_for_token_hash(token_hash(secret))
        if not row:
            return None
        self.store.touch_token(row["token_id"])
        return User(row["id"], row["name"], row["role"], row["token_id"])

    # -------------------------------------------------------------- users
    def create_user(self, uid: str, name: str, role: str, actor: User | None = None) -> None:
        if not USER_ID.match(uid):
            raise AccountError(f"{uid!r} is not a valid user id (lower-case letters, digits, - and _)")
        if role not in ROLES:
            raise AccountError(f"role must be one of {', '.join(ROLES)}")
        if self.store.get_user(uid):
            raise AccountError(f"a user {uid!r} already exists")
        self.store.create_user(uid, name or uid, role)
        self.store.audit(_actor(actor), "user.created", uid, {"role": role, "name": name or uid})

    def set_role(self, uid: str, role: str, actor: User | None = None) -> None:
        user = self._require(uid)
        if role not in ROLES:
            raise AccountError(f"role must be one of {', '.join(ROLES)}")
        if user["role"] == "admin" and role != "admin":
            self._guard_last_admin(uid)
        if not self.store.set_user_role(uid, role):
            raise AccountError(f"could not change {uid}'s role")
        self.store.audit(_actor(actor), "user.role_changed", uid, {"role": role})

    def set_disabled(self, uid: str, disabled: bool, actor: User | None = None) -> None:
        self._require(uid)
        if disabled:
            self._guard_last_admin(uid)  # do not lock everyone out
        self.store.set_user_disabled(uid, disabled)
        self.store.audit(_actor(actor), "user.disabled" if disabled else "user.enabled", uid)

    def list_users(self) -> list[dict[str, Any]]:
        return [{k: u[k] for k in ("id", "name", "role", "disabled", "created")}
                for u in self.store.list_users()]

    # -------------------------------------------------------------- tokens
    def mint_token(self, uid: str, label: str = "", actor: User | None = None) -> str:
        """Create a token for a user and return the secret **once**. Only the hash is stored."""
        self._require(uid)
        secret = secrets.token_urlsafe(32)
        tid = f"tok-{secrets.token_hex(6)}"
        self.store.add_token(tid, uid, token_hash(secret), label or "")
        self.store.audit(_actor(actor), "token.created", tid, {"user": uid, "label": label})
        return secret

    def revoke_token(self, tid: str, actor: User | None = None) -> None:
        if not self.store.revoke_token(tid):
            raise AccountError(f"no live token {tid!r}")
        self.store.audit(_actor(actor), "token.revoked", tid)

    def list_tokens(self, uid: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_tokens(uid)

    # -------------------------------------------------------------- audit
    def audit(self, actor: User | None, action: str, target: str = "", detail: Any = None) -> None:
        self.store.audit(_actor(actor), action, target, detail)

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.audit_log(limit)

    # -------------------------------------------------------------- helpers
    def _require(self, uid: str) -> dict[str, Any]:
        user = self.store.get_user(uid)
        if not user:
            raise AccountError(f"no user {uid!r}")
        return user

    def _guard_last_admin(self, uid: str) -> None:
        admins = [u for u in self.store.list_users() if u["role"] == "admin" and not u["disabled"]]
        if [u["id"] for u in admins] == [uid]:
            raise AccountError("this is the only enabled admin; make another admin first")


def _actor(actor: User | None) -> str:
    return actor.id if actor else "system"
