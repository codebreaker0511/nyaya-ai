"""Retrieval over the pre-built Constitution of India index.

The index (backend/data/index/) is built offline by scripts/ingest.py and committed to the repo:
    chunks.json      chunk text + metadata (article number, title, part, pages)
    embeddings.npy   one L2-normalised vector per chunk, so cosine similarity is a plain dot product

At runtime we load both files into memory once, embed the user's question with the same model, and
return the best-matching chunks. Retrieval never raises: if anything goes wrong we return [] and the
caller answers without legal context, exactly as the app did before RAG.
"""
import json
import logging
import threading
from pathlib import Path

import httpx
import numpy as np

from app import config

logger = logging.getLogger("nyaya.rag")

INDEX_DIR = Path(__file__).resolve().parents[2] / "data" / "index"

_lock = threading.Lock()
_index: tuple[np.ndarray, list[dict]] | None = None
_load_failed = False


def load_index() -> bool:
    """Load the index into memory (once). Returns True if retrieval is available."""
    global _index, _load_failed
    if _index is not None:
        return True
    if _load_failed:
        return False
    with _lock:
        if _index is not None:
            return True
        try:
            meta = json.loads((INDEX_DIR / "chunks.json").read_text(encoding="utf-8"))
            vectors = np.load(INDEX_DIR / "embeddings.npy")
            chunks = meta["chunks"]
            if len(chunks) != len(vectors):
                raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors")
            if meta.get("model") != config.EMBEDDING_MODEL:
                # Vectors from different models are not comparable, so searching would give nonsense.
                raise ValueError(f"index built with {meta.get('model')}, but EMBEDDING_MODEL={config.EMBEDDING_MODEL}")
            _index = (vectors, chunks)
            logger.info("RAG index loaded: %d chunks", len(chunks))
            return True
        except Exception as e:
            _load_failed = True
            logger.error("RAG index unavailable, answering without retrieval: %s", e)
            return False


def _embed(texts: list[str]) -> np.ndarray:
    """Embed texts and L2-normalise. Raises httpx errors / KeyError on failure (caller handles)."""
    resp = httpx.post(
        f"{config.LLM_BASE_URL}/embeddings",
        json={"model": config.EMBEDDING_MODEL, "input": texts},
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        timeout=config.RAG_EMBED_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda d: d["index"])
    vecs = np.asarray([d["embedding"] for d in data], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _search(query_vectors: np.ndarray) -> list[dict]:
    """Score every chunk against each query vector (keeping the best score), return the top matches."""
    vectors, chunks = _index
    scores = (vectors @ query_vectors.T).max(axis=1)  # shape: (num_chunks,)
    order = np.argsort(-scores)[: config.RAG_TOP_K]
    return [
        {
            "id": chunks[i]["id"],
            "number": chunks[i]["number"],
            "kind": chunks[i]["kind"],
            "title": chunks[i]["title"],
            "part": chunks[i]["part"],
            "pages": chunks[i]["pages"],
            "text": chunks[i]["text"],
            "score": float(scores[i]),
        }
        for i in order
        if scores[i] >= config.RAG_MIN_SCORE
    ]


def retrieve(question: str, history: list[dict] | None = None) -> list[dict]:
    """Best passages for this question (highest score first), or [] if nothing relevant is found.

    Follow-ups such as "what if my landlord refuses?" mean little on their own, so when there is
    earlier conversation we also embed (previous user message + this question) and keep, for each
    chunk, whichever of the two queries matched it better. History only helps *form the query*; the
    evidence always comes from the index, never from earlier answers.
    """
    if not config.RAG_ENABLED or not config.LLM_API_KEY or not load_index():
        return []

    queries = [question]
    previous_user = next((m["content"] for m in reversed(history or []) if m["role"] == "user"), None)
    if previous_user:
        queries.append(f"{previous_user}\n{question}")

    try:
        found = _search(_embed(queries))
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("Retrieval failed, answering without legal context: %s", type(e).__name__)
        return []
    logger.info("RAG: %d passage(s) above %.2f", len(found), config.RAG_MIN_SCORE)
    return found
