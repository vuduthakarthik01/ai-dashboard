from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.security import requires
from app.services.assistant import Assistant
from app.repository import PlantRepository

router = APIRouter(prefix="/api/chat", tags=["chat"])


class Ask(BaseModel):
    question: str
    history: list[dict] = []


@router.post("")
async def ask(body: Ask, user: User = Depends(requires("chat")),
              db: AsyncSession = Depends(get_db)):
    """Server-sent stream of the assistant's answer."""
    assistant = Assistant(PlantRepository(db, user), user)

    async def events():
        async for chunk in assistant.ask(body.history[-10:], body.question):
            yield f"data: {chunk}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
