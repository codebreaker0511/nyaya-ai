import hashlib
from concurrent.futures import ThreadPoolExecutor
import logging
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db.database import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.session import AnonymousSession, utcnow
from app.schemas.chat import (
    ConversationCreate,
    ConversationOut,
    MessageCreate,
    MessageOut,
    SendMessageOut,
    SessionOut,
)
from app.services import limits, llm, rag

logger = logging.getLogger("nyaya.chat")
router = APIRouter(prefix="/api")
_pool = ThreadPoolExecutor(max_workers=4)


def _limit_http(e: limits.LimitError) -> HTTPException:
    """Turn a LimitError into a JSON error the frontend can tell apart by `code`."""
    detail = {"code": e.code, "message": e.message}
    headers = None
    if e.retry_after is not None:
        detail["retry_after_seconds"] = e.retry_after
        headers = {"Retry-After": str(e.retry_after)}
    if e.resets_at is not None:
        detail["resets_at"] = e.resets_at.isoformat()
    return HTTPException(e.status, detail, headers=headers)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        config.SESSION_COOKIE_NAME,
        token,
        max_age=config.SESSION_EXPIRY_DAYS * 86400,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
    )


def _find_valid_session(db: Session, token: str | None) -> AnonymousSession | None:
    if not token:
        return None
    s = db.scalar(select(AnonymousSession).where(AnonymousSession.token_hash == hash_token(token)))
    if s is None or s.expires_at <= utcnow():
        return None
    return s


def get_current_session(
    db: Session = Depends(get_db),
    cookie_token: str | None = Cookie(default=None, alias=config.SESSION_COOKIE_NAME),
    header_token: str | None = Header(default=None, alias="X-Session-Token"),
) -> AnonymousSession:
    s = _find_valid_session(db, header_token or cookie_token)
    if s is None:
        raise HTTPException(401, "Session missing or expired. Call POST /api/sessions first.")
    # Sliding expiry: activity extends the session.
    s.last_active_at = utcnow()
    s.expires_at = utcnow() + timedelta(days=config.SESSION_EXPIRY_DAYS)
    return s


def get_owned_conversation(conversation_id: uuid.UUID, db: Session, session: AnonymousSession) -> Conversation:
    """Returns 404 (not 403) for other sessions' conversations so IDs can't be probed."""
    conv = db.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.session_id == session.id)
    )
    if conv is None:
        raise HTTPException(404, "Conversation not found")
    return conv


@router.post("/sessions", response_model=SessionOut)
def init_session(
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    cookie_token: str | None = Cookie(default=None, alias=config.SESSION_COOKIE_NAME),
    header_token: str | None = Header(default=None, alias="X-Session-Token"),
):
    token = header_token or cookie_token
    s = _find_valid_session(db, token)
    if s is None:
        # New sessions get a fresh quota, so limit how fast one network can create them.
        try:
            limits.check_session_creation_rate(limits.client_ip(request))
        except limits.LimitError as e:
            raise _limit_http(e)
        token = secrets.token_urlsafe(32)
        s = AnonymousSession(token_hash=hash_token(token), expires_at=utcnow())
        db.add(s)
    s.last_active_at = utcnow()
    s.expires_at = utcnow() + timedelta(days=config.SESSION_EXPIRY_DAYS)
    _set_cookie(response, token)
    return SessionOut(expires_at=s.expires_at, token=token)


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def create_conversation(
    body: ConversationCreate | None = None,
    db: Session = Depends(get_db),
    session: AnonymousSession = Depends(get_current_session),
):
    conv = Conversation(session_id=session.id, title=(body.title if body and body.title else "New conversation"))
    db.add(conv)
    db.flush()
    db.refresh(conv)
    return conv


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(db: Session = Depends(get_db), session: AnonymousSession = Depends(get_current_session)):
    return db.scalars(
        select(Conversation).where(Conversation.session_id == session.id).order_by(Conversation.updated_at.desc())
    ).all()


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def get_messages(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    session: AnonymousSession = Depends(get_current_session),
):
    return get_owned_conversation(conversation_id, db, session).messages


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    session: AnonymousSession = Depends(get_current_session),
):
    db.delete(get_owned_conversation(conversation_id, db, session))
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/messages", response_model=SendMessageOut)
def send_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    db: Session = Depends(get_db),
    session: AnonymousSession = Depends(get_current_session),
):
    conv = get_owned_conversation(conversation_id, db, session)

    # Abuse protection, before any paid API call (embeddings or LLM):
    # per-minute request limits, then a quick "is today's allowance already used up?" check.
    try:
        limits.check_message_rate(session.id, limits.client_ip(request))
        limits.ensure_quota_available(session.id)
    except limits.LimitError as e:
        raise _limit_http(e)

    # Recent history (before this message) is used only to interpret follow-ups.
    recent = db.scalars(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(config.HISTORY_MESSAGES)
    ).all()
    history = [{"role": m.role, "content": m.content} for m in reversed(recent)]
    is_first = not recent

    # Look up relevant constitutional text for *this* question (history only helps interpret follow-ups).
    # An empty list means nothing relevant was found, and the LLM then answers on its own.
    passages = rag.retrieve(body.content, history)

    # Reserve the worst case (estimated prompt + maximum output, for the answer and, on a chat's first
    # message, the title) BEFORE calling the LLM. If it doesn't fit, nothing is saved or called.
    input_estimate = llm.estimate_tokens(llm.build_messages(body.content, history, passages))
    reserve_amount = input_estimate + config.LLM_MAX_OUTPUT_TOKENS
    if is_first:
        title_estimate = llm.estimate_tokens(llm.build_title_messages(body.content))
        input_estimate += title_estimate
        reserve_amount += title_estimate + config.LLM_TITLE_MAX_OUTPUT_TOKENS
    try:
        reservation = limits.reserve(session.id, reserve_amount, input_estimate)
    except limits.LimitError as e:
        raise _limit_http(e)

    answer_usage, title_usage = llm.TokenUsage(), llm.TokenUsage()
    title_future = None
    try:
        user_msg = Message(conversation_id=conv.id, role="user", content=body.content)
        db.add(user_msg)
        if is_first:
            conv.title = body.content[:60]
        # Commit now so the user's message survives even if the LLM call fails. This also ends the
        # transaction, so no database transaction is held open during the slow LLM call.
        db.commit()

        # For a chat's first message, generate the sidebar title at the same time as the answer
        # (in a second thread) so it adds no waiting time.
        title_future = _pool.submit(llm.generate_title, body.content, title_usage) if is_first else None
        try:
            reply = llm.generate_answer(body.content, history, passages, usage=answer_usage)
        except llm.LLMError as e:
            raise HTTPException(503, str(e))
        title = title_future.result() if title_future is not None else None
    finally:
        # Always settle, even on errors: wait for the title call so its tokens are counted too
        # (generate_title never raises), then swap the reservation for the real usage.
        if title_future is not None:
            title_future.result()
        limits.settle(reservation, answer_usage.total + title_usage.total)

    if title:
        conv.title = title

    assistant_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
    db.add(assistant_msg)
    conv.updated_at = utcnow()
    db.commit()
    db.refresh(user_msg)
    db.refresh(assistant_msg)
    return SendMessageOut(user_message=user_msg, assistant_message=assistant_msg)
