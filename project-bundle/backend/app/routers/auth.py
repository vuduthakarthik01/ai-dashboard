from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.security import PERMISSIONS, create_token, current_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token")
async def token(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(
        select(User).where(User.employee_id == form.username))).scalar_one_or_none()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "That employee ID and passcode do not match.")
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    return {"access_token": create_token(user), "token_type": "bearer",
            "role": user.role.value, "name": user.name,
            "permissions": sorted(PERMISSIONS[user.role])}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {"id": str(user.id), "name": user.name, "employee_id": user.employee_id,
            "role": user.role.value, "plant_id": str(user.plant_id),
            "permissions": sorted(PERMISSIONS[user.role])}
