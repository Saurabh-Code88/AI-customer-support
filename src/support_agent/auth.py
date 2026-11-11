"""Signed, short-lived identities for the public portfolio sandbox."""
from dataclasses import dataclass
import hashlib
import os
import time
import uuid
from typing import Literal

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

Role = Literal["customer", "agent"]
ISSUER = "support-agent-sandbox"
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    role: Role
    email: str | None
    session_id: str


def jwt_secret() -> str:
    return os.getenv("AUTH_JWT_SECRET", "").strip()


def validate_auth_configuration() -> None:
    environment = (os.getenv("ENV") or os.getenv("APP_ENV", "development")).lower()
    if environment not in {"prod", "production"}:
        return
    missing = []
    if not jwt_secret():
        raise RuntimeError("AUTH_JWT_SECRET is required in production")
    if not os.getenv("GOOGLE_API_KEY", "").strip():
        missing.append("GOOGLE_API_KEY")
    if not os.getenv("GROQ_API_KEY", "").strip():
        missing.append("GROQ_API_KEY")
    if missing:
        raise RuntimeError(f"{', '.join(missing)} are required in production")


def token_ttl() -> int:
    return min(max(60, int(os.getenv("SESSION_TOKEN_TTL_SECONDS", "1800"))), 3600)


def issue_token(role: Role, email: str | None = None) -> tuple[str, int]:
    secret = jwt_secret()
    if not secret:
        raise HTTPException(503, detail={"code": "auth_unconfigured", "message": "Sessions are temporarily unavailable."})
    ttl = token_ttl()
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": email or "support-agent", "role": role, "iat": now,
              "exp": now + ttl, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, secret, algorithm="HS256"), ttl


def require_principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if credentials is None:
        raise HTTPException(401, detail={"code": "missing_token", "message": "Choose a role to start a secure session."}, headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(credentials.credentials, jwt_secret(), algorithms=["HS256"], issuer=ISSUER, options={"require": ["sub", "role", "iat", "exp"]})
        role = claims.get("role")
        email = claims.get("sub") if role == "customer" else None
        if role not in {"customer", "agent"} or (role == "customer" and not isinstance(email, str)):
            raise jwt.InvalidTokenError("invalid role or subject")
        session_id = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        return Principal(role=role, email=email, session_id=session_id)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(401, detail={"code": "token_expired", "message": "Your session expired. Please choose a role again."}, headers={"WWW-Authenticate": "Bearer"}) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Your session is invalid. Please choose a role again."}, headers={"WWW-Authenticate": "Bearer"}) from exc


def require_role(*roles: Role):
    def dependency(principal: Principal = Depends(require_principal)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(403, detail={"code": "forbidden", "message": "This action is only available to a support agent."})
        return principal

    return dependency
