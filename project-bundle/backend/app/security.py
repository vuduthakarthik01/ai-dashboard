"""Auth and role-based access control.

Permissions are attached to roles, not to users, so adding a plant manager
later means editing one dict rather than migrating rows.
"""
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Role, User

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

PERMISSIONS: dict[Role, set[str]] = {
    Role.admin: {"view.all", "alert.ack", "alert.resolve", "wo.create", "wo.close",
                 "po.raise", "report.export", "chat", "settings", "users.manage"},
    Role.owner: {"view.all", "alert.ack", "alert.resolve", "wo.create", "wo.close",
                 "po.raise", "report.export", "chat"},
    Role.supervisor: {"view.production", "view.machines", "view.cctv", "view.quality",
                      "view.inventory", "view.energy", "view.alerts", "view.reports",
                      "alert.ack", "alert.resolve", "wo.create", "chat"},
    Role.operator: {"view.production", "view.machines", "view.quality", "view.alerts",
                    "alert.ack", "chat"},
}


def hash_password(raw: str) -> str:
    return pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    return pwd.verify(raw, hashed)


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "plant": str(user.plant_id),
        "role": user.role.value,
        "name": user.name,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again to continue.")


async def current_user(token: str = Depends(oauth2), db: AsyncSession = Depends(get_db)) -> User:
    claims = decode_token(token)
    user = (await db.execute(select(User).where(User.id == claims["sub"]))).scalar_one_or_none()
    if user is None or not user.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account is no longer active.")
    return user


def requires(permission: str):
    """Route dependency. `Depends(requires("wo.create"))`."""
    async def guard(user: User = Depends(current_user)) -> User:
        allowed = PERMISSIONS.get(user.role, set())
        if "view.all" in allowed and permission.startswith("view."):
            return user
        if permission not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"The {user.role.value} role cannot {permission.replace('.', ' ')}.",
            )
        return user
    return guard
