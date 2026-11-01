"""RAG tests. They use the real committed index but never call the network: query embeddings are faked."""
import numpy as np
import pytest

from app import config
from app.services import llm, rag


@pytest.fixture(autouse=True)
def _fresh_index(monkeypatch):
    monkeypatch.setattr(rag, "_index", None)
    monkeypatch.setattr(rag, "_load_failed", False)
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key")  # retrieve() skips work when no key is set


def vector_of(article_number: str) -> np.ndarray:
    """The stored embedding of an Article, used as a perfect 'question' about it."""
    assert rag.load_index()
    vectors, chunks = rag._index
    i = next(i for i, c in enumerate(chunks) if c["kind"] == "article" and c["number"] == article_number)
    return vectors[i : i + 1]


def test_index_loads_and_matches_embedding_model():
    assert rag.load_index()
    vectors, chunks = rag._index
    assert len(vectors) == len(chunks) > 400
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3)


@pytest.mark.parametrize("number", ["14", "21", "21A", "32", "300A", "370"])
def test_retrieves_the_article_whose_vector_it_is_given(monkeypatch, number):
    monkeypatch.setattr(rag, "_embed", lambda texts: np.repeat(vector_of(number), len(texts), axis=0))
    found = rag.retrieve("anything")
    assert found and found[0]["number"] == number
    assert found[0]["score"] > 0.99 and f"Article {number}" in found[0]["text"]


def test_nothing_relevant_returns_empty(monkeypatch):
    assert rag.load_index()
    orthogonal = np.zeros((1, rag._index[0].shape[1]), dtype=np.float32)
    orthogonal[0, 0] = 1.0  # an arbitrary direction no legal passage points along strongly
    monkeypatch.setattr(config, "RAG_MIN_SCORE", 0.95)
    monkeypatch.setattr(rag, "_embed", lambda texts: np.repeat(orthogonal, len(texts), axis=0))
    assert rag.retrieve("what is the recipe for biryani") == []


def test_followup_also_embeds_previous_user_question(monkeypatch):
    calls = []

    def fake_embed(texts):
        calls.append(texts)
        return np.repeat(vector_of("21"), len(texts), axis=0)

    monkeypatch.setattr(rag, "_embed", fake_embed)
    history = [{"role": "user", "content": "tell me about my landlord"}, {"role": "assistant", "content": "ok"}]
    rag.retrieve("what if he refuses?", history)
    assert calls[0] == ["what if he refuses?", "tell me about my landlord\nwhat if he refuses?"]


def test_embedding_failure_falls_back_to_no_context(monkeypatch):
    import httpx

    def boom(texts):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(rag, "_embed", boom)
    assert rag.retrieve("Can police arrest me?") == []


def test_disabled_rag_returns_empty(monkeypatch):
    monkeypatch.setattr(config, "RAG_ENABLED", False)
    assert rag.retrieve("Can police arrest me?") == []


def test_missing_index_is_handled(monkeypatch, tmp_path):
    monkeypatch.setattr(rag, "INDEX_DIR", tmp_path)  # empty directory
    assert rag.load_index() is False
    assert rag.retrieve("anything") == []


def test_prompt_includes_passages_only_when_present(monkeypatch):
    sent = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, json, headers, timeout):
        sent["system"] = json["messages"][0]["content"]
        return FakeResp()

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    llm.generate_answer("q", [], None)
    assert sent["system"] == llm.SYSTEM_PROMPT  # exactly the pre-RAG prompt
    llm.generate_answer("q", [], [{"text": "Article 21 - Protection of life"}])
    assert sent["system"].startswith(llm.SYSTEM_PROMPT) and "[1] Article 21 - Protection of life" in sent["system"]
