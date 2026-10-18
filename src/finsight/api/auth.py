"""Server-side API-key authentication with roles. Identity comes from the key, never from request bodies.

AUTH_KEYS="key1:viewer:ops-dashboard,key2:analyst:dana" (role: viewer < analyst).
Without AUTH_KEYS every protected route returns 503 unless AUTH_DISABLED=true (explicit local-demo escape hatch).
This is shared-secret authentication for a local/demo deployment, not user login or SSO."""

import logging
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Query, Request

from ..config import Settings, get_settings
from ..logs import log

logger = logging.getLogger("finsight.auth")
ROLES = {"viewer": 1, "analyst": 2}


@dataclass(frozen=True)
class Principal:
    identity: str
    role: str


def parse_keys(raw: str | None) -> dict[str, Principal]:
    out: dict[str, Principal] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":", 2)
        if len(bits) != 3 or bits[1] not in ROLES or not bits[0] or not bits[2]:
            raise ValueError(
                "AUTH_KEYS entries must look like key:role:identity with role viewer|analyst"
            )
        out[bits[0]] = Principal(bits[2], bits[1])
    return out


def authenticate(x_api_key: str | None, s: Settings) -> Principal:
    if s.auth_disabled:
        log(logger, logging.WARNING, "auth_disabled_local_demo")
        return Principal("local-demo", "analyst")
    keys = parse_keys(s.auth_keys)
    if not keys:
        raise HTTPException(
            503,
            "authentication is not configured (set AUTH_KEYS, or AUTH_DISABLED=true for a local demo)",
        )
    if x_api_key:
        for k, p in keys.items():
            if secrets.compare_digest(k, x_api_key):
                return p
    raise HTTPException(401, "invalid or missing API key")


def require(role: str):
    def dep(
        request: Request, x_api_key: str | None = Header(None), reveal_ids: bool = Query(False)
    ) -> Principal:
        p = authenticate(x_api_key, get_settings())
        if ROLES[p.role] < ROLES[role]:
            raise HTTPException(403, f"role '{role}' required")
        request.state.principal = p
        if reveal_ids:
            if p.role != "analyst":
                raise HTTPException(403, "revealing raw identifiers requires the analyst role")
            request.state.reveal = True  # audited by the middleware
        return p

    return dep


viewer = Depends(require("viewer"))
analyst = Depends(require("analyst"))
