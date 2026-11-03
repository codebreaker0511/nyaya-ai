"""Tables for abuse protection: daily token usage, in-flight reservations and rate-limit counters.

They live in PostgreSQL (not in Python memory) so every worker and instance sees the same numbers.
They are separate from sessions on purpose: deleting a session or conversation never resets usage.
"""
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.session import utcnow


class DailyTokenUsage(Base):
    """One row per (key, day). key is "session:<session id>" or "global" (all sessions together).

    used_tokens     tokens the provider actually reported (input + output).
    reserved_tokens tokens set aside for LLM calls that are still running.
    The primary key (key, day) guarantees there is never more than one row per session per day.
    """

    __tablename__ = "daily_token_usage"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    used_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TokenReservation(Base):
    """An LLM call in progress. Deleted when the call is settled. A row that is still here long after
    the LLM timeout belongs to a request that died (crash, restart) and is cleaned up as "stale"."""

    __tablename__ = "token_reservations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String(64))
    day: Mapped[date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(BigInteger)  # tokens reserved (estimated input + max output)
    input_estimate: Mapped[int] = mapped_column(BigInteger)  # charged if the outcome is unknown
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class RateLimitCounter(Base):
    """Requests counted per key (e.g. "send:session:<id>", "send:ip:<ip>") per fixed time window."""

    __tablename__ = "rate_limit_counters"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
