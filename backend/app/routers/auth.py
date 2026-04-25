from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers import current_user
from app.schemas.auth import LoginRequest, Token, UserRead
from app.services.auth_service import authenticate_user, create_access_token
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=Token)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, body.email, body.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_access_token(str(user.id))
    return Token(access_token=token)


@router.post("/logout")
async def logout():
    return {"message": "Logged out"}


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(current_user)):
    return user
