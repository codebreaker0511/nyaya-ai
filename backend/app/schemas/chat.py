import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import MAX_MESSAGE_CHARS


class Citation(BaseModel):
    passage_id: str
    document_title: str
    provision: str
    source_url: str


class SessionOut(BaseModel):
    expires_at: datetime
    token: str  # also sent as a cookie; clients that can't keep cookies send it as X-Session-Token


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(max_length=MAX_MESSAGE_CHARS)

    @field_validator("content")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty")
        return v


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    role: str
    content: str
    citations: list[Citation] | None = None
    created_at: datetime


class SendMessageOut(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut
