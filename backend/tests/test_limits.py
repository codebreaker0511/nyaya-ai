"""Tests for rate limits, daily token quotas and the global budget (services/limits.py).

The paid LLM API is never called: httpx.post inside services/llm.py is replaced by FakeProvider,
which returns OpenAI-style responses with `usage` metadata. RAG retrieval is stubbed out.
"""
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from starlette.requests import Request

from app import config
from app.api.chat import hash_token
from app.db.database import SessionLocal
from app.main import app
from app.models.message import Message
from app.models.session import AnonymousSession
from app.models.usage import DailyTokenUsage, TokenReservation
from app.services import limits, llm

QUESTION = "Can police arrest me without telling me the reason?"


# ---------------------------------------------------------------- helpers


class FakeProvider:
    """Stands in for the chat-completions API. Records every call."""

    def __init__(self, answer_usage=(1000, 200), title_usage=(50, 5), error=None, include_usage=True):
        self.answer_usage = answer_usage
        self.title_usage = title_usage
        self.error = error
        self.include_usage = include_usage
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, url, json=None, headers=None, timeout=None):
        with self._lock:
            self.calls.append(json)
        if isinstance(self.error, Exception):
            raise self.error
        request = httpx.Request("POST", url)
        if isinstance(self.error, int):
            return httpx.Response(self.error, json={"error": "x"}, request=request)
        is_title = json["messages"][0]["content"] == llm.TITLE_PROMPT
        prompt, completion = self.title_usage if is_title else self.answer_usage
        body = {"choices": [{"message": {"content": "Arrest Rights" if is_title else "stub answer"}}]}
        if self.include_usage:
            body["usage"] = {"prompt_tokens": prompt, "completion_tokens": completion}
        return httpx.Response(200, json=body, request=request)


@pytest.fixture
def provider(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm.httpx, "post", p)
    monkeypatch.setattr("app.api.chat.rag.retrieve", lambda q, h=None: [])
    return p


def new_client(ip="testclient"):
    """A browser with its own anonymous session. Returns (client, session_id)."""
    c = TestClient(app, client=(ip, 50000))
    c.__enter__()
    token = c.post("/api/sessions").json()["token"]
    with SessionLocal() as db:
        sid = db.scalar(select(AnonymousSession.id).where(AnonymousSession.token_hash == hash_token(token)))
    return c, sid


def new_conversation(c):
    return c.post("/api/conversations", json={}).json()["id"]


def ask(c, cid, content=QUESTION, headers=None):
    return c.post(f"/api/conversations/{cid}/messages", json={"content": content}, headers=headers)


def usage_row(key, day=None):
    with SessionLocal() as db:
        return db.get(DailyTokenUsage, (key, day or limits.quota_day()))


def used(key, day=None):
    row = usage_row(key, day)
    return 0 if row is None else row.used_tokens


def reserved(key, day=None):
    row = usage_row(key, day)
    return 0 if row is None else row.reserved_tokens


def set_used(key, tokens, day=None):
    day = day or limits.quota_day()
    with SessionLocal() as db:
        db.execute(
            insert(DailyTokenUsage)
            .values(key=key, day=day, used_tokens=tokens, reserved_tokens=0)
            .on_conflict_do_update(index_elements=["key", "day"], set_={"used_tokens": tokens})
        )
        db.commit()


def first_message_input_estimate(content=QUESTION):
    return llm.estimate_tokens(llm.build_messages(content, [], [])) + llm.estimate_tokens(
        llm.build_title_messages(content)
    )


def fake_request(peer, xff=None):
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234)})


# ---------------------------------------------------------------- 1-4: normal use and accounting


@pytest.mark.db
def test_new_session_can_ask_and_usage_comes_from_provider_metadata(provider):
    c, sid = new_client()
    cid = new_conversation(c)
    r = ask(c, cid)
    assert r.status_code == 200
    assert r.json()["assistant_message"]["content"] == "stub answer"
    # answer (1000 in + 200 out) + title (50 in + 5 out), from the provider's usage field
    key = limits.session_key(sid)
    assert used(key) == 1255
    assert used(limits.GLOBAL_KEY) == 1255
    assert reserved(key) == 0 and reserved(limits.GLOBAL_KEY) == 0
    # the output cap is sent so the reservation's "max output" is real
    assert all(call[config.LLM_MAX_TOKENS_PARAM] > 0 for call in provider.calls)
    with SessionLocal() as db:
        assert db.scalar(select(TokenReservation).where(TokenReservation.key == key)) is None


@pytest.mark.db
def test_input_and_output_tokens_both_count_and_session_below_limit_continues(provider):
    provider.answer_usage = (3000, 1000)
    c, sid = new_client()
    cid = new_conversation(c)
    assert ask(c, cid).status_code == 200
    assert ask(c, cid, "And what if they refuse?").status_code == 200  # still below the limit
    # 2 answers x (3000 input + 1000 output) + 1 title (55)
    assert used(limits.session_key(sid)) == 2 * 4000 + 55


# ---------------------------------------------------------------- 5-6: limit reached


@pytest.mark.db
def test_session_at_limit_is_rejected_before_any_paid_call(provider):
    c, sid = new_client()
    cid = new_conversation(c)
    set_used(limits.session_key(sid), config.DAILY_TOKEN_LIMIT)
    r = ask(c, cid)
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["code"] == "daily_quota_exceeded"
    assert "resets_at" in detail and int(r.headers["Retry-After"]) > 0
    assert provider.calls == []  # no LLM call
    with SessionLocal() as db:  # and the question was not saved
        assert db.scalars(select(Message).where(Message.conversation_id == uuid.UUID(cid))).all() == []


@pytest.mark.db
def test_request_that_would_exceed_remaining_quota_is_rejected(provider):
    c, sid = new_client()
    cid = new_conversation(c)
    # 500 tokens left: less than the worst case this request could use, so it must not start.
    set_used(limits.session_key(sid), config.DAILY_TOKEN_LIMIT - 500)
    r = ask(c, cid)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_quota_exceeded"
    assert provider.calls == []
    assert reserved(limits.session_key(sid)) == 0


@pytest.mark.db
def test_input_plus_output_reaching_limit_blocks_next_request(provider):
    provider.answer_usage = (30000, 9000)  # 39,000 of 40,000 used in one go
    c, sid = new_client()
    cid = new_conversation(c)
    assert ask(c, cid).status_code == 200
    assert used(limits.session_key(sid)) == 39055
    calls = len(provider.calls)
    r = ask(c, cid, "follow up")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_quota_exceeded"
    assert len(provider.calls) == calls


# ---------------------------------------------------------------- 7: concurrency


@pytest.mark.db
def test_simultaneous_reservations_cannot_exceed_the_limit():
    sid = uuid.uuid4()
    amount, threads = 7000, 20  # only 5 x 7000 fit in 40,000
    barrier = threading.Barrier(threads)
    results = []

    def worker():
        barrier.wait()
        try:
            results.append(limits.reserve(sid, amount, 100))
        except limits.LimitError as e:
            results.append(e)

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    ok = [r for r in results if isinstance(r, limits.Reservation)]
    assert len(ok) == config.DAILY_TOKEN_LIMIT // amount == 5
    assert all(isinstance(r, limits.LimitError) and r.status == 429 for r in results if r not in ok)
    assert reserved(limits.session_key(sid)) == 5 * amount <= config.DAILY_TOKEN_LIMIT
    for r in ok:  # settling releases the unused part of each reservation
        limits.settle(r, 1000)
    assert reserved(limits.session_key(sid)) == 0
    assert used(limits.session_key(sid)) == 5000


@pytest.mark.db
def test_simultaneous_http_requests_cannot_exceed_the_limit(provider, monkeypatch):
    """End to end: the limit fits exactly 2 worst-case reservations, so at most 2 of 6 parallel requests may run."""
    per_request = first_message_input_estimate() + config.LLM_MAX_OUTPUT_TOKENS + config.LLM_TITLE_MAX_OUTPUT_TOKENS
    limit = 2 * per_request + 100
    monkeypatch.setattr(config, "DAILY_TOKEN_LIMIT", limit)
    gate = threading.Event()
    real = provider.__call__

    def slow_provider(url, json=None, headers=None, timeout=None):
        gate.wait(5)  # hold every LLM call until all requests have tried to reserve
        return real(url, json=json, headers=headers, timeout=timeout)

    monkeypatch.setattr(llm.httpx, "post", slow_provider)
    c, sid = new_client()
    cids = [new_conversation(c) for _ in range(6)]
    statuses = []

    def worker(cid):
        statuses.append(ask(c, cid).status_code)

    ts = [threading.Thread(target=worker, args=(cid,)) for cid in cids]
    for t in ts:
        t.start()
    threading.Timer(2.0, gate.set).start()
    for t in ts:
        t.join()
    assert 1 <= statuses.count(200) <= 2
    assert statuses.count(200) + statuses.count(429) == 6
    assert used(limits.session_key(sid)) <= limit
    assert reserved(limits.session_key(sid)) == 0


# ---------------------------------------------------------------- 8-9: request-rate limits


@pytest.mark.db
def test_session_rate_limit(provider, monkeypatch):
    monkeypatch.setattr(config, "SESSION_RATE_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(limits, "_now", lambda: datetime(2026, 11, 2, 10, 0, 5, tzinfo=timezone.utc))
    c, _ = new_client()
    cid = new_conversation(c)
    assert ask(c, cid).status_code == 200
    assert ask(c, cid, "second").status_code == 200
    r = ask(c, cid, "third")
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"
    assert r.headers["Retry-After"] == "55"
    assert len(provider.calls) == 3  # 2 answers + 1 title; the third request made no call


def test_client_ip_ignores_forwarded_header_by_default(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 0)
    assert limits.client_ip(fake_request("198.51.100.7", "1.2.3.4")) == "198.51.100.7"


def test_client_ip_with_one_trusted_proxy_ignores_spoofed_entries(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    # The client sent "X-Forwarded-For: 1.2.3.4"; our proxy appended the real address 203.0.113.9.
    assert limits.client_ip(fake_request("10.0.0.1", "1.2.3.4, 203.0.113.9")) == "203.0.113.9"
    assert limits.client_ip(fake_request("10.0.0.1", "203.0.113.9")) == "203.0.113.9"
    assert limits.client_ip(fake_request("10.0.0.1", "not-an-ip")) == "10.0.0.1"
    assert limits.client_ip(fake_request("10.0.0.1")) == "10.0.0.1"


@pytest.mark.db
def test_ip_rate_limit_cannot_be_bypassed_with_forwarded_header(provider, monkeypatch):
    monkeypatch.setattr(config, "IP_RATE_LIMIT_PER_MINUTE", 2)
    monkeypatch.setattr(limits, "_now", lambda: datetime(2026, 11, 2, 10, 0, 5, tzinfo=timezone.utc))
    a, _ = new_client("198.51.100.20")
    b, _ = new_client("198.51.100.20")  # a second session on the same network
    assert ask(a, new_conversation(a)).status_code == 200
    assert ask(b, new_conversation(b)).status_code == 200
    r = ask(b, new_conversation(b), headers={"X-Forwarded-For": "203.0.113.99"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
    # a different network is unaffected
    other, _ = new_client("198.51.100.21")
    assert ask(other, new_conversation(other)).status_code == 200


@pytest.mark.db
def test_session_creation_is_rate_limited_per_ip(monkeypatch):
    monkeypatch.setattr(config, "SESSION_CREATE_LIMIT_PER_IP_PER_HOUR", 2)
    monkeypatch.setattr(limits, "_now", lambda: datetime(2026, 11, 2, 10, 0, 5, tzinfo=timezone.utc))
    statuses = []
    for _ in range(3):
        with TestClient(app, client=("198.51.100.30", 1)) as c:
            statuses.append(c.post("/api/sessions").status_code)
    assert statuses == [200, 200, 429]
    # refreshing an existing session is not counted
    with TestClient(app, client=("198.51.100.31", 1)) as c:
        token = c.post("/api/sessions").json()["token"]
        for _ in range(3):
            assert c.post("/api/sessions", headers={"X-Session-Token": token}).status_code == 200


# ---------------------------------------------------------------- 10: isolation


@pytest.mark.db
def test_new_session_does_not_inherit_quota_or_history(provider):
    a, sid_a = new_client()
    cid = new_conversation(a)
    assert ask(a, cid).status_code == 200
    set_used(limits.session_key(sid_a), config.DAILY_TOKEN_LIMIT)
    assert ask(a, cid, "more").status_code == 429

    b, sid_b = new_client()
    assert sid_b != sid_a
    assert b.get("/api/conversations").json() == []
    assert b.get(f"/api/conversations/{cid}/messages").status_code == 404
    assert used(limits.session_key(sid_b)) == 0
    assert ask(b, new_conversation(b)).status_code == 200


# ---------------------------------------------------------------- 11: global budget


@pytest.mark.db
def test_global_budget_stops_paid_calls(provider, monkeypatch):
    per_request = first_message_input_estimate() + config.LLM_MAX_OUTPUT_TOKENS + config.LLM_TITLE_MAX_OUTPUT_TOKENS
    # After the first chat (4055 tokens) one more worst-case reservation no longer fits.
    monkeypatch.setattr(config, "GLOBAL_DAILY_TOKEN_LIMIT", 4055 + per_request - 1)
    provider.answer_usage = (3000, 1000)
    a, _ = new_client()
    assert ask(a, new_conversation(a)).status_code == 200  # global: 4055 used
    calls = len(provider.calls)
    b, _ = new_client()  # a brand-new session does not help
    r = ask(b, new_conversation(b))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "service_budget_exhausted"
    assert len(provider.calls) == calls
    set_used(limits.GLOBAL_KEY, config.GLOBAL_DAILY_TOKEN_LIMIT)  # fully used: rejected by the early check too
    r = ask(b, new_conversation(b))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "service_budget_exhausted"
    assert len(provider.calls) == calls


@pytest.mark.db
def test_limits_fail_closed_when_database_check_fails(provider, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def broken():
        raise OperationalError("SELECT", {}, Exception("db down"))

    c, _ = new_client()
    cid = new_conversation(c)
    monkeypatch.setattr(limits, "SessionLocal", broken)
    r = ask(c, cid)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "limits_unavailable"
    assert provider.calls == []


# ---------------------------------------------------------------- 12: provider failures


@pytest.mark.db
def test_provider_http_error_charges_nothing_and_releases_reservation(provider):
    provider.error = 500
    c, sid = new_client()
    r = ask(c, new_conversation(c))
    assert r.status_code == 503
    assert used(limits.session_key(sid)) == 0
    assert reserved(limits.session_key(sid)) == 0 and reserved(limits.GLOBAL_KEY) == 0


@pytest.mark.db
def test_provider_timeout_charges_only_estimated_input(provider):
    provider.error = httpx.ReadTimeout("timed out")
    c, sid = new_client()
    r = ask(c, new_conversation(c))
    assert r.status_code == 503
    # unknown outcome: charge the estimated prompt, release the reserved output tokens
    assert used(limits.session_key(sid)) == first_message_input_estimate()
    assert reserved(limits.session_key(sid)) == 0


@pytest.mark.db
def test_connection_refused_charges_nothing(provider):
    provider.error = httpx.ConnectError("refused")
    c, sid = new_client()
    assert ask(c, new_conversation(c)).status_code == 503
    assert used(limits.session_key(sid)) == 0 and reserved(limits.session_key(sid)) == 0


@pytest.mark.db
def test_missing_usage_metadata_falls_back_to_estimate(provider):
    provider.include_usage = False
    c, sid = new_client()
    assert ask(c, new_conversation(c)).status_code == 200
    assert used(limits.session_key(sid)) > first_message_input_estimate()  # input estimate + output estimate
    assert reserved(limits.session_key(sid)) == 0


@pytest.mark.db
def test_abandoned_reservation_is_released_as_stale(monkeypatch):
    sid = uuid.uuid4()
    start = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(limits, "_now", lambda: start)
    r = limits.reserve(sid, 6000, 1500)
    # the request "dies": nothing settles it. Later, cleanup charges the input estimate only.
    monkeypatch.setattr(limits, "_now", lambda: start + timedelta(seconds=config.RESERVATION_TTL_SECONDS + 1))
    with SessionLocal() as db:  # make the row look old (created_at uses the real clock)
        db.get(TokenReservation, r.id).created_at = start
        db.commit()
    limits.release_stale_reservations()
    key = limits.session_key(sid)
    assert reserved(key, r.day) == 0 and used(key, r.day) == 1500
    limits.settle(r, 9999)  # a late settle must not count it twice
    assert used(key, r.day) == 1500


def test_llm_usage_parsing_without_database(monkeypatch):
    """generate_answer fills TokenUsage from the provider response, including on an empty answer."""
    p = FakeProvider(answer_usage=(123, 45))
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(llm.httpx, "post", p)
    u = llm.TokenUsage()
    assert llm.generate_answer("q", [], [], usage=u) == "stub answer"
    assert (u.input_tokens, u.output_tokens, u.estimated) == (123, 45, False)


# ---------------------------------------------------------------- 13: daily reset


def test_quota_day_uses_configured_timezone(monkeypatch):
    monkeypatch.setattr(config, "QUOTA_TIMEZONE", "Asia/Kolkata")  # UTC+05:30
    before = datetime(2026, 11, 1, 18, 29, tzinfo=timezone.utc)  # 23:59 IST
    after = datetime(2026, 11, 1, 18, 31, tzinfo=timezone.utc)  # 00:01 IST next day
    assert limits.quota_day(before) == date(2026, 11, 1)
    assert limits.quota_day(after) == date(2026, 11, 2)
    assert limits.next_reset(before) == datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(config, "QUOTA_TIMEZONE", "UTC")
    assert limits.quota_day(before) == date(2026, 11, 1)
    assert limits.next_reset(before) == datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)


@pytest.mark.db
def test_quota_resets_at_midnight_in_configured_timezone(provider, monkeypatch):
    monkeypatch.setattr(config, "QUOTA_TIMEZONE", "Asia/Kolkata")
    late = datetime(2026, 11, 1, 18, 20, tzinfo=timezone.utc)  # 23:50 IST, 1 Nov
    monkeypatch.setattr(limits, "_now", lambda: late)
    c, sid = new_client()
    cid = new_conversation(c)
    set_used(limits.session_key(sid), config.DAILY_TOKEN_LIMIT, day=date(2026, 11, 1))
    r = ask(c, cid)
    assert r.status_code == 429
    assert r.json()["detail"]["resets_at"] == "2026-11-01T18:30:00+00:00"
    monkeypatch.setattr(limits, "_now", lambda: late + timedelta(minutes=11))  # 00:01 IST, 2 Nov
    assert ask(c, cid).status_code == 200
    assert used(limits.session_key(sid), date(2026, 11, 2)) == 1255
    assert used(limits.session_key(sid), date(2026, 11, 1)) == config.DAILY_TOKEN_LIMIT
