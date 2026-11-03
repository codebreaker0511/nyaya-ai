# Nyaya AI

An AI-powered legal awareness assistant that helps ordinary people in India understand their rights and the law, in plain language, without needing legal expertise or an account.

**Live demo:** https://nyaya-ai-phi-nine.vercel.app/

> The backend runs on a free hosting tier. If the app has been idle, the first request can take 30-60 seconds while the server wakes up.

## What it does

- Ask legal questions in natural language (rights, police, consumer, tenancy, labour, family law, the Constitution, etc.).
- Get short, practical answers: what to do next, who to contact, and relevant helplines or schemes.
- Answers about fundamental rights and the Constitution are grounded in the actual text of the Constitution of India (retrieval-augmented generation) and name the relevant Article.
- Create and manage multiple conversations, and come back to them later.
- Follow-up questions use earlier messages as context.
- No sign-up, login or password. Each browser gets an anonymous session.

Nyaya AI gives general legal information, not legal advice. Laws change, so verify important details on official sources such as [India Code](https://www.indiacode.nic.in) or with a lawyer.

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | React 19, Vite, react-markdown (hosted on Vercel) |
| Backend | Python, FastAPI, Uvicorn (hosted on Render) |
| Data access | SQLAlchemy ORM, Psycopg 3, Pydantic for validation |
| Database | PostgreSQL (hosted on Render) |
| AI | Any OpenAI-compatible chat-completions API, called with httpx |
| Retrieval (RAG) | OpenAI embeddings (`text-embedding-3-small`) and cosine search with NumPy over an in-memory index |

## Backend architecture

The backend is a single FastAPI application, split into layers so each file has one job.

```
backend/app/
├── main.py        App setup: CORS, router registration, /health, table creation on startup
├── config.py      Reads environment variables in one place
├── api/chat.py    HTTP endpoints, session auth, ownership checks
├── schemas/       Pydantic request/response models and input validation
├── models/        SQLAlchemy tables: sessions, conversations, messages
├── db/            Engine, session factory, get_db dependency
├── services/rag.py  Finds the Constitution passages relevant to a question (in-memory vector search)
└── services/llm.py  Calls the LLM API, system prompt, error handling

backend/scripts/ingest.py     Builds the search index from the Constitution PDF (run locally)
backend/scripts/eval_rag.py   Checks retrieval quality against questions with known answers
backend/data/index/           The built index (chunks.json + embeddings.npy), committed to the repo
data/constitution_of_india.pdf  Source document
```

### Request flow

```
Browser (React)
   │  X-Session-Token header (cookie as fallback)
   ▼
FastAPI router (api/chat.py)
   ├─ validate session token  → sessions table
   ├─ check conversation belongs to that session
   ├─ validate body (Pydantic)
   ├─ save user message       → messages table
   ├─ retrieve relevant Constitution passages (services/rag.py)
   ├─ build prompt: system prompt + passages + recent history (services/llm.py)
   ├─ call LLM API
   └─ save assistant message  → messages table, return both messages
```

### RAG pipeline

Answers are grounded in the Constitution of India using retrieval-augmented generation (RAG): the app finds the relevant Articles first, then asks the LLM to explain them in plain language. It does not fine-tune a model, so the legal text can be updated without retraining.

#### 1. Ingestion (run once, locally)

```
data/constitution_of_india.pdf → clean text → split by Article → embed each chunk → save index
```

- `scripts/ingest.py` reads the PDF with `pypdf` and removes the cover, table of contents, running page headers and amendment footnotes.
- It splits the text by Article, so each chunk is one legal provision. Each chunk keeps its Article number, title, Part and page range. Very long Articles and the Schedules are split further on clause boundaries. This gives about 600 chunks covering Articles 1 to 395 and the Schedules.
- Each chunk is converted to a vector with OpenAI `text-embedding-3-small`, so that search works on meaning rather than exact keywords.
- The output is two files in `backend/data/index/`: `chunks.json` (text and metadata) and `embeddings.npy` (L2-normalised vectors, so cosine similarity is a dot product). They are committed to the repo.

There is no separate vector database. The index is small, so the backend loads both files into memory at startup and searches them with NumPy in under a millisecond. Keeping the index in the repo also suits Render's free tier, where files written at runtime are lost on every restart.

#### 2. Retrieval and answer (every message)

```
question → embed → closest chunks → LLM (system prompt + chunks + history) → answer
```

1. `services/rag.py` embeds the question and scores every chunk by cosine similarity.
2. Chunks scoring at least `RAG_MIN_SCORE` (default 0.30) are kept, up to `RAG_TOP_K` (default 4).
3. For a follow-up such as "what if they refuse?", the previous user message and the new question are also embedded together, and each chunk keeps the better of the two scores. Conversation history only helps form the search query. It is never used as legal evidence.
4. The retrieved text is appended to the system prompt, telling the model to base its answer on the passages, name the Article, and not attribute anything to an Article that the passage does not say. The style, length and formatting rules are unchanged.

#### 3. When nothing relevant is found

Greetings, off-topic questions and questions the Constitution does not cover (for example an ordinary tenancy dispute) score below the threshold, so no passages are sent and the LLM answers from its general knowledge, exactly as it did before RAG. If the embeddings call or the index fails, retrieval returns nothing and the chat continues the same way, so RAG cannot take the app down.

#### Rebuilding or checking the index

From `backend/`:

```bash
python scripts/ingest.py --dry-run   # parse and chunk only; prints stats, no API calls
python scripts/ingest.py             # parse, chunk, embed, write backend/data/index/
python scripts/eval_rag.py           # check retrieval against questions with known answers
python scripts/eval_rag.py --answers # also print full LLM answers
```

Re-run `ingest.py` and commit the new files if the PDF or chunking changes. If you change `EMBEDDING_MODEL`, you must rebuild the index. The backend refuses to use an index built with a different model.

#### Limits

- Only the Constitution is indexed. Questions about other laws (the BNS and BNSS, consumer law, tenancy and so on) are answered from the model's general knowledge, which can be wrong. The assistant is told not to guess section numbers.
- Retrieval finds relevant text but cannot guarantee that the model's explanation is legally accurate. Verify important details on [India Code](https://www.indiacode.nic.in) or with a lawyer.
- The Constitution PDF is dated 10 September 2026 and was not checked for later amendments.

### Anonymous sessions

1. The frontend calls `POST /api/sessions`. The backend generates a random token with `secrets.token_urlsafe`, stores only its SHA-256 hash in PostgreSQL, and returns the raw token in the response body and as an HttpOnly cookie.
2. The frontend keeps the token in `localStorage` and sends it as an `X-Session-Token` header on every request. Because the frontend and backend are on different domains, browsers often block the cookie, so the header is checked first and the cookie is the fallback.
3. Sessions expire after 7 days of inactivity, and each request extends the expiry. If a session is invalid or expired, the frontend creates a new one and retries.

### Data model

| Table | Purpose |
|---|---|
| `sessions` | Anonymous visitor: token hash, created, last active, expiry |
| `conversations` | Chat threads, each belonging to one session |
| `messages` | Messages in a conversation, with role (`user` / `assistant`) and optional citations |
| `daily_token_usage` | Tokens used and reserved per session (and globally) per day; one row per key per day |
| `token_reservations` | LLM calls in progress, so abandoned ones can be cleaned up |
| `rate_limit_counters` | Request counts per session or IP per time window |

Every conversation-specific endpoint checks that the conversation belongs to the caller's session. Other sessions' conversations return 404, so IDs cannot be probed.

### API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check |
| POST | `/api/sessions` | Create or refresh the anonymous session |
| POST | `/api/conversations` | Create a conversation |
| GET | `/api/conversations` | List the session's conversations |
| GET | `/api/conversations/{id}/messages` | Get messages in a conversation |
| POST | `/api/conversations/{id}/messages` | Send a message and get the AI answer |
| DELETE | `/api/conversations/{id}` | Delete a conversation and its messages |

### Safety and reliability

- Explicit CORS origin (never `*`) with credentials enabled.
- Message length is limited and blank messages are rejected.
- LLM failures return a safe error message. Provider details and API keys are only logged, never shown to users.
- The system prompt restricts the assistant to Indian legal questions and tells it not to invent provisions.

### Abuse protection (rate limits and token quotas)

Code: `services/limits.py`, tables in `models/usage.py`. All counters live in PostgreSQL, so they are shared by every worker and instance (no Redis needed at this scale).

| Protection | Default | Response when hit |
|---|---|---|
| Messages per session per minute | `SESSION_RATE_LIMIT_PER_MINUTE=5` | 429 `rate_limited` + `Retry-After` |
| Messages per IP per minute | `IP_RATE_LIMIT_PER_MINUTE=10` | 429 `rate_limited` + `Retry-After` |
| New sessions per IP per hour | `SESSION_CREATE_LIMIT_PER_IP_PER_HOUR=30` | 429 `rate_limited` |
| Tokens per session per day (input + output) | `DAILY_TOKEN_LIMIT=40000` | 429 `daily_quota_exceeded` + `resets_at` |
| Tokens for all sessions together per day | `GLOBAL_DAILY_TOKEN_LIMIT=2000000` | 503 `service_budget_exhausted` |

Errors are JSON: `{"detail": {"code", "message", "retry_after_seconds", "resets_at"}}`. The frontend shows a different message for each code.

**How a message is counted**

1. Rate limits are checked, then a quick "is today's allowance already used?" check. Both happen before any paid call, including the RAG embeddings call.
2. Before calling the LLM, the backend reserves the worst case: an estimate of the prompt (system prompt, retrieved passages, history and question) plus the maximum output (`LLM_MAX_OUTPUT_TOKENS`, and the title call on a chat's first message). The reservation is one atomic `UPDATE ... WHERE used + reserved + amount <= limit` on both the session row and the global row, so simultaneous requests cannot together go over a limit. If it doesn't fit, nothing is saved and nothing is called.
3. After the call, the reservation is replaced by the real `prompt_tokens + completion_tokens` from the provider's `usage` field. No database transaction is open during the LLM call.
4. Failures: an HTTP error from the provider, or a request that never connected, costs nothing. A timeout or broken connection (outcome unknown) is charged the estimated prompt tokens only. If a request dies completely (crash or restart), its reservation is cleaned up after `RESERVATION_TTL_SECONDS` and charged the same way.
5. If the provider returns no `usage`, the cost is estimated (UTF-8 bytes / 2, which is deliberately generous) and a warning is logged.
6. If the limits cannot be checked (database error), no paid call is made (fail closed, 503).

Days reset at midnight in `QUOTA_TIMEZONE` (default `UTC`; for example `Asia/Kolkata` resets at midnight IST).

**Client IP behind a proxy.** `X-Forwarded-For` can be faked by the client, so it is ignored unless `TRUSTED_PROXY_HOPS` is set. With `TRUSTED_PROXY_HOPS=N`, the app takes the address N steps from the right of `X-Forwarded-For` plus the connecting address, which is the part written by your own proxies. On Render, set it in production after checking the real header (for example by temporarily logging `request.headers.get("x-forwarded-for")` and `request.client.host` for your own request). If it is left at `0` behind a proxy, every user shares the proxy's IP and the per-IP limit becomes one shared limit. Don't start uvicorn with `--forwarded-allow-ips="*"`.

**Limits of this approach**

- Clearing browser storage gives a new anonymous session and a new daily quota. The per-IP session-creation limit and the global ceiling bound the damage, but anonymous quotas cannot be strict per person.
- People can rotate IPs, and many legitimate users can share one IP (college or office networks, mobile carriers), so IP limits are only an extra signal.
- The global limit is a token ceiling, not a money budget: tokens are not converted to prices. Also set a spending limit and alerts in your LLM provider's dashboard.
- The RAG embeddings call is not counted in the token quotas. It is much cheaper than chat, and it is skipped when a session or the global budget is already used up.
- Rate limits use fixed one-minute windows, so a burst of up to twice the limit is possible across a window boundary.

## Project structure

```
backend/    FastAPI application, RAG service and index-building scripts
frontend/   React + Vite application
data/       Source legal documents (the Constitution of India PDF)
```

## Run locally

**Backend** (from `backend/`)

```bash
pip install -r requirements.txt
cp .env.example .env    # then fill in DATABASE_URL and LLM_API_KEY
uvicorn app.main:app --reload
```

The repo already contains the built RAG index in `backend/data/index/`, so you do not need to run the ingestion script to start the app. The same `LLM_API_KEY` is used for chat and for embeddings, so it must have access to the embeddings API.

**Frontend** (from `frontend/`)

```bash
npm install
npm run dev
```

**Tests** (from `backend/`). Database tests run only against a separate database named in `TEST_DATABASE_URL`, and are skipped otherwise, so they can never touch real data. The LLM is always mocked.

```bash
createdb -U postgres nyaya_ai_test
TEST_DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/nyaya_ai_test python -m pytest
```

The frontend runs at http://localhost:5173 and proxies `/api` to the backend at http://localhost:8000. Leave `VITE_API_URL` empty in development.

## Deployment

- **Frontend (Vercel):** root directory `frontend`, build command `npm run build`, output directory `dist`. Set `VITE_API_URL` to the backend URL.
- **Backend (Render):** root directory `backend`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set `DATABASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and `FRONTEND_ORIGIN` (the exact frontend URL, no trailing slash). Make sure `backend/data/index/` is committed, because the app loads the RAG index from there at startup. The logs should show `RAG index loaded` on boot. If they show `RAG index unavailable`, the app still works but answers without retrieval.
- **Database (Render PostgreSQL):** tables are created automatically on startup. This only creates missing tables; existing tables and data are never changed. The abuse-protection tables (`daily_token_usage`, `token_reservations`, `rate_limit_counters`) are added the same way on the first deploy.
- **Abuse protection in production:** set `TRUSTED_PROXY_HOPS` correctly (see "Abuse protection" above) and choose `GLOBAL_DAILY_TOKEN_LIMIT` to match your budget.

## Environment variables

See `backend/.env.example` and `frontend/.env.example`.

Optional RAG settings (the defaults are what the project was tested with): `EMBEDDING_MODEL` (`text-embedding-3-small`; must match the model the index was built with), `RAG_ENABLED` (`true`), `RAG_TOP_K` (`4`) and `RAG_MIN_SCORE` (`0.30`, the cosine-similarity cutoff below which the LLM answers without legal context).
