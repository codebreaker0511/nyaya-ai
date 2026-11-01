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

The frontend runs at http://localhost:5173 and proxies `/api` to the backend at http://localhost:8000. Leave `VITE_API_URL` empty in development.

## Deployment

- **Frontend (Vercel):** root directory `frontend`, build command `npm run build`, output directory `dist`. Set `VITE_API_URL` to the backend URL.
- **Backend (Render):** root directory `backend`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set `DATABASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and `FRONTEND_ORIGIN` (the exact frontend URL, no trailing slash). Make sure `backend/data/index/` is committed, because the app loads the RAG index from there at startup. The logs should show `RAG index loaded` on boot. If they show `RAG index unavailable`, the app still works but answers without retrieval.
- **Database (Render PostgreSQL):** tables are created automatically on startup.

## Environment variables

See `backend/.env.example` and `frontend/.env.example`.

Optional RAG settings (the defaults are what the project was tested with): `EMBEDDING_MODEL` (`text-embedding-3-small`; must match the model the index was built with), `RAG_ENABLED` (`true`), `RAG_TOP_K` (`4`) and `RAG_MIN_SCORE` (`0.30`, the cosine-similarity cutoff below which the LLM answers without legal context).
