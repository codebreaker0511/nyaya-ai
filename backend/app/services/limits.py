"""Abuse protection: request-rate limits, per-session daily token quotas and a global daily token ceiling.

Everything is stored in PostgreSQL so that all workers/instances share the same counters, and every
update is a single atomic SQL statement, so simultaneous requests cannot slip past a limit.

Token quota flow for one chat message:

    reserve(estimated input + max output)   short transaction, BEFORE calling the LLM
    ... LLM call(s), no database transaction open ...
    settle(actual tokens from the provider)  short transaction: reserved -> used

`reserve` only succeeds if `used + reserved + amount <= limit`, checked inside the UPDATE itself.
PostgreSQL locks the row while updating it, so two concurrent reservations are applied one after the
other and the second one sees the first. Lock order is always: reservation row -> session row -> global
row, which rules out deadlocks.

These functions use their own short-lived database sessions (not the request's), so they commit
independently and settle() still works if the request's session is in a bad state.
"""
import ipaddress
import logging
import math
import random
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app import config
from app.db.database import SessionLocal
from app.models.usage import DailyTokenUsage, RateLimitCounter, TokenReservation

logger = logging.getLogger("nyaya.limits")

GLOBAL_KEY = "global"


class LimitError(Exception):
    """A limit was hit (or could not be checked). `code` lets the frontend tell the cases apart."""

    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None, resets_at=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.resets_at = resets_at


def _unavailable() -> LimitError:
    # Fail closed: if we cannot verify the limits, we do not make paid calls.
    return LimitError(503, "limits_unavailable", "The service is temporarily unavailable. Please try again shortly.", 30)


def _now() -> datetime:
    """Current time in UTC. A function so tests can move the clock."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- days and resets


def quota_day(now: datetime | None = None) -> date:
    """The calendar day (in QUOTA_TIMEZONE) that usage is counted against."""
    return (now or _now()).astimezone(ZoneInfo(config.QUOTA_TIMEZONE)).date()


def next_reset(now: datetime | None = None) -> datetime:
    """Next midnight in QUOTA_TIMEZONE, returned in UTC."""
    tz = ZoneInfo(config.QUOTA_TIMEZONE)
    local = (now or _now()).astimezone(tz)
    return datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=tz).astimezone(timezone.utc)


def _seconds_until_reset() -> int:
    return max(1, math.ceil((next_reset() - _now()).total_seconds()))


def session_key(session_id: uuid.UUID) -> str:
    return f"session:{session_id}"


# ---------------------------------------------------------------- client IP


def client_ip(request: Request) -> str:
    """The client's IP address, used only as an extra abuse signal (many people can share one IP).

    X-Forwarded-For is a list that each proxy APPENDS to, so only the right-most entries were written by
    proxies we control; anything to their left may have been typed by the client. With
    TRUSTED_PROXY_HOPS = N we take the address N steps from the right of [X-Forwarded-For..., peer].
    With N = 0 (default) the header is ignored completely.
    """
    peer = request.client.host if request.client else "unknown"
    hops = config.TRUSTED_PROXY_HOPS
    if hops <= 0:
        return peer
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    chain = forwarded + [peer]
    candidate = chain[max(0, len(chain) - 1 - hops)]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


# ---------------------------------------------------------------- request-rate limits


def hit_rate_limit(key: str, limit: int, window_seconds: int, message: str) -> None:
    """Count one request for `key` in the current fixed window; raise 429 when over `limit`.

    The count is one atomic INSERT ... ON CONFLICT DO UPDATE, so concurrent requests are all counted.
    Fixed windows are simple but allow up to 2x the limit across a window boundary; fine for abuse control.
    """
    if limit <= 0:
        return
    now = _now()
    epoch = int(now.timestamp())
    window_start = datetime.fromtimestamp(epoch - epoch % window_seconds, timezone.utc)
    try:
        with SessionLocal() as db:
            stmt = (
                insert(RateLimitCounter)
                .values(key=key, window_start=window_start, count=1)
                .on_conflict_do_update(
                    index_elements=["key", "window_start"], set_={"count": RateLimitCounter.count + 1}
                )
                .returning(RateLimitCounter.count)
            )
            count = db.execute(stmt).scalar_one()
            if random.random() < 0.02:  # occasional cleanup of old windows
                db.execute(delete(RateLimitCounter).where(RateLimitCounter.window_start < now - timedelta(days=1)))
            db.commit()
    except SQLAlchemyError:
        logger.exception("Rate-limit check failed")
        raise _unavailable()
    if count > limit:
        raise LimitError(429, "rate_limited", message, retry_after=window_seconds - epoch % window_seconds)


def check_message_rate(session_id: uuid.UUID, ip: str) -> None:
    hit_rate_limit(
        f"send:session:{session_id}",
        config.SESSION_RATE_LIMIT_PER_MINUTE,
        60,
        "You're sending messages too quickly. Please wait a moment and try again.",
    )
    hit_rate_limit(
        f"send:ip:{ip}",
        config.IP_RATE_LIMIT_PER_MINUTE,
        60,
        "Too many messages are being sent from your network. Please wait a moment and try again.",
    )


def check_session_creation_rate(ip: str) -> None:
    hit_rate_limit(
        f"new-session:ip:{ip}",
        config.SESSION_CREATE_LIMIT_PER_IP_PER_HOUR,
        3600,
        "Too many new sessions from your network. Please try again later.",
    )


# ---------------------------------------------------------------- token quotas


def _quota_exhausted() -> LimitError:
    return LimitError(
        429,
        "daily_quota_exceeded",
        "You've used today's free allowance for Nyaya AI. It resets at midnight "
        f"{config.QUOTA_TIMEZONE}; please come back then.",
        retry_after=_seconds_until_reset(),
        resets_at=next_reset(),
    )


def _budget_exhausted() -> LimitError:
    return LimitError(
        503,
        "service_budget_exhausted",
        "Nyaya AI has reached its usage limit for today. Please try again tomorrow.",
        retry_after=_seconds_until_reset(),
        resets_at=next_reset(),
    )


def _usage_row(db, key: str, day: date) -> DailyTokenUsage | None:
    return db.get(DailyTokenUsage, (key, day), populate_existing=True)


def ensure_quota_available(session_id: uuid.UUID) -> None:
    """Cheap early check before any paid work (including the RAG embeddings call).
    Not the real guard (that is reserve()); it just avoids work for sessions that are clearly out."""
    day = quota_day()
    try:
        with SessionLocal() as db:
            s = _usage_row(db, session_key(session_id), day)
            g = _usage_row(db, GLOBAL_KEY, day)
    except SQLAlchemyError:
        logger.exception("Quota check failed")
        raise _unavailable()
    if s is not None and s.used_tokens >= config.DAILY_TOKEN_LIMIT:
        raise _quota_exhausted()
    if g is not None and g.used_tokens >= config.GLOBAL_DAILY_TOKEN_LIMIT:
        logger.warning("Global daily token limit reached")
        raise _budget_exhausted()


@dataclass
class Reservation:
    id: uuid.UUID
    key: str
    day: date
    amount: int
    input_estimate: int


def _try_reserve(db, key: str, day: date, amount: int, limit: int) -> bool:
    """Atomically add `amount` to reserved_tokens only if it still fits under `limit`."""
    row = db.execute(
        update(DailyTokenUsage)
        .where(
            DailyTokenUsage.key == key,
            DailyTokenUsage.day == day,
            DailyTokenUsage.used_tokens + DailyTokenUsage.reserved_tokens + amount <= limit,
        )
        .values(reserved_tokens=DailyTokenUsage.reserved_tokens + amount)
        .returning(DailyTokenUsage.key)
    ).first()
    return row is not None


def reserve(session_id: uuid.UUID, amount: int, input_estimate: int) -> Reservation:
    """Set aside `amount` tokens for this session AND the global budget, or raise LimitError."""
    release_stale_reservations()
    day = quota_day()
    key = session_key(session_id)
    try:
        with SessionLocal() as db:
            # Make sure today's rows exist (no-op if they already do; the primary key prevents duplicates).
            db.execute(
                insert(DailyTokenUsage)
                .values(
                    [
                        {"key": key, "day": day, "used_tokens": 0, "reserved_tokens": 0},
                        {"key": GLOBAL_KEY, "day": day, "used_tokens": 0, "reserved_tokens": 0},
                    ]
                )
                .on_conflict_do_nothing()
            )
            if not _try_reserve(db, key, day, amount, config.DAILY_TOKEN_LIMIT):
                row = _usage_row(db, key, day)
                db.rollback()
                if row is not None and row.used_tokens + amount > config.DAILY_TOKEN_LIMIT:
                    raise _quota_exhausted()
                # It would fit once the questions already being answered finish.
                raise LimitError(
                    429, "rate_limited", "Please wait for your previous question to finish, then try again.", 10
                )
            if not _try_reserve(db, GLOBAL_KEY, day, amount, config.GLOBAL_DAILY_TOKEN_LIMIT):
                db.rollback()
                logger.warning("Global daily token limit reached; refusing new LLM calls")
                raise _budget_exhausted()
            reservation = TokenReservation(
                key=key, day=day, amount=amount, input_estimate=input_estimate, created_at=_now()
            )
            db.add(reservation)
            db.commit()
            return Reservation(reservation.id, key, day, amount, input_estimate)
    except SQLAlchemyError:
        logger.exception("Token reservation failed")
        raise _unavailable()


def _apply(db, key: str, day: date, reserved_delta: int, used_delta: int) -> None:
    db.execute(
        update(DailyTokenUsage)
        .where(DailyTokenUsage.key == key, DailyTokenUsage.day == day)
        .values(
            reserved_tokens=func.greatest(DailyTokenUsage.reserved_tokens - reserved_delta, 0),
            used_tokens=DailyTokenUsage.used_tokens + used_delta,
        )
    )


def settle(reservation: Reservation, actual_tokens: int) -> None:
    """Replace the reservation with the tokens actually used. Never raises.

    Deleting the reservation row first makes this safe to race with release_stale_reservations():
    whichever deletes the row applies the change; the other one does nothing.
    """
    try:
        with SessionLocal() as db:
            deleted = db.execute(delete(TokenReservation).where(TokenReservation.id == reservation.id)).rowcount
            if not deleted:
                db.rollback()
                logger.warning("Reservation %s was already released as stale", reservation.id)
                return
            for key in (reservation.key, GLOBAL_KEY):
                _apply(db, key, reservation.day, reservation.amount, actual_tokens)
            db.commit()
    except SQLAlchemyError:
        # The reservation row is still there, so the stale cleanup will release it later
        # (charging its input estimate). Until then it keeps blocking that much quota: the safe side.
        logger.exception("Could not settle reservation %s (%s tokens used)", reservation.id, actual_tokens)


def release_stale_reservations() -> None:
    """Clean up reservations left behind by requests that died (crash, restart, killed worker).

    We don't know what those calls cost, so we charge the estimated input tokens (the prompt was most
    likely sent) and release the rest. One transaction per reservation keeps the lock order consistent.
    """
    cutoff = _now() - timedelta(seconds=config.RESERVATION_TTL_SECONDS)
    try:
        for _ in range(20):
            with SessionLocal() as db:
                r = db.scalars(
                    select(TokenReservation)
                    .where(TokenReservation.created_at < cutoff)
                    .order_by(TokenReservation.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                ).first()
                if r is None:
                    return
                charge = min(r.input_estimate, r.amount)
                logger.warning("Releasing stale reservation %s; charging estimated %s tokens", r.id, charge)
                for key in (r.key, GLOBAL_KEY):
                    _apply(db, key, r.day, r.amount, charge)
                db.delete(r)
                db.commit()
    except SQLAlchemyError:
        logger.exception("Stale reservation cleanup failed")
