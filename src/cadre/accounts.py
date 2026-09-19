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
import time
from typing import TYPE_CHECKING, Any

from .clocks import DayClock

if TYPE_CHECKING:
    from .store import Store

ROLES = ("viewer", "member", "admin")
TEAM_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def model_allowed(patterns: list[str], provider: str, key: str) -> bool:
    """A model (`provider`, and `key` = provider/name) is allowed when the team lists no pattern,
    or a pattern matches its provider, its full key, or `*`."""
    return not patterns or any(p in ("*", provider, key) for p in patterns)
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

    def resolve_user(self, uid: str) -> User | None:
        """The User for a stored id (no token needed), for server-side checks like team membership."""
        row = self.store.get_user(uid)
        return User(row["id"], row["name"], row["role"]) if row and not row["disabled"] else None

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

    # -------------------------------------------------------------- teams (phase 2)
    def create_team(self, tid: str, name: str = "", actor: User | None = None) -> None:
        if not TEAM_ID.match(tid):
            raise AccountError(f"{tid!r} is not a valid team id (lower-case letters, digits, - and _)")
        if self.store.get_team(tid):
            raise AccountError(f"a team {tid!r} already exists")
        self.store.create_team(tid, name or tid)
        self.store.audit(_actor(actor), "team.created", tid, {"name": name or tid})

    def list_teams(self) -> list[dict[str, Any]]:
        out = []
        for t in self.store.list_teams():
            out.append({"id": t["id"], "name": t["name"], "members": self.store.team_members(t["id"]),
                        "budget": self.store.get_budget(t["id"]), "allow": self.store.get_allow(t["id"])})
        return out

    def team(self, tid: str) -> dict[str, Any]:
        if not self.store.get_team(tid):
            raise AccountError(f"no team {tid!r}")
        t = self.store.get_team(tid)
        return {"id": t["id"], "name": t["name"], "members": self.store.team_members(tid),
                "budget": self.store.get_budget(tid), "allow": self.store.get_allow(tid)}

    def add_member(self, tid: str, uid: str, actor: User | None = None) -> None:
        if not self.store.get_team(tid):
            raise AccountError(f"no team {tid!r}")
        self._require(uid)
        self.store.add_member(tid, uid)
        self.store.audit(_actor(actor), "team.member_added", tid, {"user": uid})

    def remove_member(self, tid: str, uid: str, actor: User | None = None) -> None:
        if not self.store.remove_member(tid, uid):
            raise AccountError(f"{uid!r} is not a member of {tid!r}")
        self.store.audit(_actor(actor), "team.member_removed", tid, {"user": uid})

    def set_budget(self, tid: str, runs_per_day: int | None, tokens_per_day: int | None,
                   max_concurrent: int | None, actor: User | None = None) -> None:
        if not self.store.get_team(tid):
            raise AccountError(f"no team {tid!r}")
        for label, v in (("runs_per_day", runs_per_day), ("tokens_per_day", tokens_per_day),
                         ("max_concurrent", max_concurrent)):
            if v is not None and v < 0:
                raise AccountError(f"{label} cannot be negative")
        self.store.set_budget(tid, runs_per_day, tokens_per_day, max_concurrent)
        self.store.audit(_actor(actor), "team.budget_set", tid,
                         {"runs_per_day": runs_per_day, "tokens_per_day": tokens_per_day,
                          "max_concurrent": max_concurrent})

    def set_allow(self, tid: str, patterns: list[str], actor: User | None = None) -> None:
        if not self.store.get_team(tid):
            raise AccountError(f"no team {tid!r}")
        clean = sorted({p.strip() for p in patterns if p.strip()})
        self.store.set_allow(tid, clean)
        self.store.audit(_actor(actor), "team.allow_set", tid, {"patterns": clean})

    def user_teams(self, uid: str) -> list[str]:
        return self.store.user_teams(uid)

    def team_for_run(self, user: User, team: str | None) -> str | None:
        """Which team a run belongs to. A named team must be one the user belongs to (admins may
        use any). With no team named, a user's single team is used; 0 or many means a personal run."""
        if team:
            if not self.store.get_team(team):
                raise AccountError(f"no team {team!r}")
            if not (user.can("admin") or self.store.is_member(team, user.id)):
                raise AccountError(f"you are not a member of {team!r}")
            return team
        mine = self.store.user_teams(user.id)
        return mine[0] if len(mine) == 1 else None

    def check_team_budget(self, tid: str | None) -> None:
        """Raise if the team has reached a start-time budget (runs/day, concurrent, tokens/day)."""
        if not tid:
            return
        b = self.store.get_budget(tid)
        if not b:
            return
        if b["max_concurrent"] is not None and self.store.count_team_active_runs(tid) >= b["max_concurrent"]:
            raise AccountError(f"team {tid} already has {b['max_concurrent']} runs going (its limit)")
        day = DayClock("UTC").bucket_start(time.time())
        if b["runs_per_day"] is not None and self.store.count_team_runs_since(tid, day) >= b["runs_per_day"]:
            raise AccountError(f"team {tid} has started its {b['runs_per_day']} runs for today (UTC)")
        if b["tokens_per_day"] is not None and self.store.team_tokens_since(tid, day) >= b["tokens_per_day"]:
            raise AccountError(f"team {tid} has used its {b['tokens_per_day']} tokens for today (UTC)")

    def team_allow(self, tid: str | None) -> list[str]:
        return self.store.get_allow(tid) if tid else []

    def may_act_on_run(self, user: User, run: dict[str, Any]) -> bool:
        """Who may cancel/resume a run or decide its approvals: an admin, the run's owner, or a
        member of the run's team. A run with no owner/team (pre-accounts, or a personal run) is
        actionable by any member+ (the audit log keeps it accountable)."""
        if user.can("admin"):
            return True
        owner, team = run.get("owner_user"), run.get("owner_team")
        if not owner and not team:
            return user.can("run")
        if owner and owner == user.id:
            return True
        return bool(team and self.store.is_member(team, user.id))

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
