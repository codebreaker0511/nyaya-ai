from fastapi.testclient import TestClient

from app.main import app


def make_client():
    c = TestClient(app)
    c.__enter__()
    assert c.post("/api/sessions").status_code == 200
    return c


def test_health():
    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok"}


def test_requires_session():
    with TestClient(app) as c:
        assert c.get("/api/conversations").status_code == 401


def test_conversation_flow_and_isolation():
    a, b = make_client(), make_client()
    conv = a.post("/api/conversations", json={}).json()
    cid = conv["id"]
    assert any(x["id"] == cid for x in a.get("/api/conversations").json())
    # other session cannot see or touch it
    assert b.get(f"/api/conversations/{cid}/messages").status_code == 404
    assert b.delete(f"/api/conversations/{cid}").status_code == 404
    assert b.post(f"/api/conversations/{cid}/messages", json={"content": "hi"}).status_code == 404
    assert a.delete(f"/api/conversations/{cid}").status_code == 204


def test_invalid_payload():
    a = make_client()
    cid = a.post("/api/conversations", json={}).json()["id"]
    assert a.post(f"/api/conversations/{cid}/messages", json={"content": "   "}).status_code == 422
    assert a.post(f"/api/conversations/{cid}/messages", json={"content": "x" * 5000}).status_code == 422


def test_message_is_saved_with_reply(monkeypatch):
    monkeypatch.setattr("app.api.chat.rag.retrieve", lambda q, h=None: [])
    monkeypatch.setattr("app.api.chat.llm.generate_answer", lambda q, h, p=None: "stub answer")
    monkeypatch.setattr("app.api.chat.llm.generate_title", lambda q: "Stub Title")
    a = make_client()
    cid = a.post("/api/conversations", json={}).json()["id"]
    r = a.post(f"/api/conversations/{cid}/messages", json={"content": "hello"})
    assert r.status_code == 200
    assert r.json()["assistant_message"]["content"] == "stub answer"
    assert [m["role"] for m in a.get(f"/api/conversations/{cid}/messages").json()] == ["user", "assistant"]
    assert any(c["id"] == cid and c["title"] == "Stub Title" for c in a.get("/api/conversations").json())


def test_llm_failure_returns_503_but_keeps_user_message(monkeypatch):
    from app.services.llm import LLMError

    def boom(q, h, p=None):
        raise LLMError("The AI service is currently unavailable.")

    monkeypatch.setattr("app.api.chat.rag.retrieve", lambda q, h=None: [])
    monkeypatch.setattr("app.api.chat.llm.generate_answer", boom)
    monkeypatch.setattr("app.api.chat.llm.generate_title", lambda q: None)
    a = make_client()
    cid = a.post("/api/conversations", json={}).json()["id"]
    r = a.post(f"/api/conversations/{cid}/messages", json={"content": "hello"})
    assert r.status_code == 503
    assert [m["role"] for m in a.get(f"/api/conversations/{cid}/messages").json()] == ["user"]


def test_retrieved_passages_are_passed_to_the_llm(monkeypatch):
    passage = {"number": "21", "text": "Article 21 text", "score": 0.6}
    seen = {}

    def fake_answer(q, h, p=None):
        seen["passages"] = p
        return "grounded answer"

    monkeypatch.setattr("app.api.chat.rag.retrieve", lambda q, h=None: [passage])
    monkeypatch.setattr("app.api.chat.llm.generate_answer", fake_answer)
    monkeypatch.setattr("app.api.chat.llm.generate_title", lambda q: None)
    a = make_client()
    cid = a.post("/api/conversations", json={}).json()["id"]
    r = a.post(f"/api/conversations/{cid}/messages", json={"content": "what is article 21?"})
    assert r.status_code == 200 and r.json()["assistant_message"]["content"] == "grounded answer"
    assert seen["passages"] == [passage]
