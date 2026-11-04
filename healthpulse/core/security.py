"""Roles, permissions (server-side RBAC), demo accounts, audit log and secret redaction."""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

from ..config import get_settings
from . import db
from .util import dumps, iso, new_id, utcnow

ROLES = ("viewer", "data_operator", "data_scientist", "epi_reviewer", "platform_admin")

# action -> roles allowed (spec §17.2). Everything is enforced in the API dependency, never only in the UI.
PERMISSIONS: dict[str, set[str]] = {
    "read": set(ROLES),
    "scenario.write": {"data_scientist", "epi_reviewer", "platform_admin", "data_operator"},
    "scenario.run": {"data_scientist", "epi_reviewer", "platform_admin", "data_operator"},
    "scenario.review": {"epi_reviewer", "platform_admin"},
    "capacity.write": {"data_operator", "platform_admin"},
    "alert.review": {"epi_reviewer", "platform_admin"},
    "source.write": {"data_operator", "platform_admin"},
    "source.ingest": {"data_operator", "platform_admin"},
    "feature.write": {"data_scientist", "platform_admin"},
    "dataset.build": {"data_scientist", "platform_admin"},
    "experiment.run": {"data_scientist", "platform_admin"},
    "model.propose": {"data_scientist", "platform_admin"},
    "model.review.clinical": {"epi_reviewer"},
    "model.review.platform": {"platform_admin"},
    "model.deploy": {"platform_admin"},
    "model.infer": {"platform_admin", "data_scientist"},
    "nowcast.run": {"platform_admin", "data_scientist", "data_operator"},
    "ops.report": {"data_operator", "data_scientist", "epi_reviewer", "platform_admin"},   # key in live reports / resources
    "ops.command": {"epi_reviewer", "platform_admin"},   # enforce real zones, approve allocation plans, open live incidents
    "admin.security": {"platform_admin"},
    "admin.settings": {"platform_admin"},
    "audit.read": {"platform_admin", "epi_reviewer"},
    "sivep.raw": set(),  # nobody by default: raw SARI rows are aggregated on import and never served
}

DEMO_USERS = [
    ("u_viewer", "Viewer (read-only)", "viewer"),
    ("u_operator", "Data operator", "data_operator"),
    ("u_scientist", "Data scientist", "data_scientist"),
    ("u_reviewer", "Epidemiology reviewer", "epi_reviewer"),
    ("u_admin", "Platform admin", "platform_admin"),
]


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str
    display_name: str

    def can(self, action: str) -> bool:
        return self.role in PERMISSIONS.get(action, set())


ANON = Actor("anonymous", "viewer", "Public viewer")


def token_hash(token: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def demo_token(user_id: str) -> str:
    """Deterministic demo token per seeded account (local/demo environments only)."""
    return "hp_" + hmac.new(get_settings().secret_key.encode(), f"demo:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]


def seed_users() -> None:
    now = iso(utcnow())
    for uid, name, role in DEMO_USERS:
        row = db.q1("SELECT token_hash FROM users WHERE user_id=?", uid)
        if row:
            # demo tokens derive from HP_SECRET_KEY; if the key changed (e.g. .env now loaded), re-sync the stored hash so
            # demo sign-in keeps working. Production never serves demo tokens (see /auth/demo-accounts).
            if get_settings().env in ("local", "demo", "test") and row["token_hash"] != token_hash(demo_token(uid)):
                db.update("users", {"user_id": uid}, {"token_hash": token_hash(demo_token(uid))})
            continue
        db.insert("users", {"user_id": uid, "display_name": name, "role": role,
                            "token_hash": token_hash(demo_token(uid)), "created_at": now})


def resolve_token(token: str | None) -> Actor:
    if not token:
        return ANON
    row = db.q1("SELECT user_id, role, display_name FROM users WHERE token_hash=? AND disabled=0", token_hash(token))
    if not row:
        return ANON
    return Actor(row["user_id"], row["role"], row["display_name"])


# ---------------------------------------------------------------- redaction
_SECRET_KEYS = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authorization|credential)", re.I)
_URL_CRED = re.compile(r"(https?://)[^/@\s]+:[^/@\s]+@")
_QS_SECRET = re.compile(r"([?&](?:token|key|apikey|api_key|access_token|signature)=)[^&\s]+", re.I)


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SECRET_KEYS.search(str(k)) and not str(k).endswith("_ref"):
                out[k] = "***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _QS_SECRET.sub(r"\1***", _URL_CRED.sub(r"\1***@", obj))
    return obj


def audit(actor: Actor, action: str, target_type: str | None = None, target_id: str | None = None, *,
          reason: str | None = None, old: Any = None, new: Any = None, meta: dict | None = None,
          request_id: str | None = None) -> str:
    eid = new_id("evt")
    db.insert("audit_event", {
        "event_id": eid, "actor_id": actor.user_id, "actor_role": actor.role, "action": action,
        "target_type": target_type, "target_id": target_id, "ts": iso(utcnow()), "request_id": request_id,
        "reason": reason, "old_version": None if old is None else str(old), "new_version": None if new is None else str(new),
        "metadata_redacted": dumps(redact(meta or {})),
    })
    return eid
