# Nyaya AI

An AI-powered legal awareness assistant that helps ordinary people in India understand their rights and the law, in plain language, without needing legal expertise or an account.

**Live demo:** https://nyaya-ai-phi-nine.vercel.app/

> The backend runs on a free hosting tier. If the app has been idle, the first request can take 30-60 seconds while the server wakes up.

## What it does

- Ask legal questions in natural language (rights, police, consumer, tenancy, labour, family law, the Constitution, etc.).
- Get short, practical answers: what to do next, who to contact, and relevant helplines or schemes.
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
└── services/llm.py  Calls the LLM API, system prompt, error handling
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
   ├─ build prompt: system prompt + recent history (services/llm.py)
   ├─ call LLM API
   └─ save assistant message  → messages table, return both messages
```

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
backend/    FastAPI application
frontend/   React + Vite application
```

## Run locally

**Backend** (from `backend/`)

```bash
pip install -r requirements.txt
cp .env.example .env    # then fill in DATABASE_URL and LLM_API_KEY
uvicorn app.main:app --reload
```

**Frontend** (from `frontend/`)

```bash
npm install
npm run dev
```

The frontend runs at http://localhost:5173 and proxies `/api` to the backend at http://localhost:8000. Leave `VITE_API_URL` empty in development.

## Deployment

- **Frontend (Vercel):** root directory `frontend`, build command `npm run build`, output directory `dist`. Set `VITE_API_URL` to the backend URL.
- **Backend (Render):** root directory `backend`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set `DATABASE_URL`, `LLM_API_KEY`, `LLM_MODEL` and `FRONTEND_ORIGIN` (the exact frontend URL, no trailing slash).
- **Database (Render PostgreSQL):** tables are created automatically on startup.

## Environment variables

See `backend/.env.example` and `frontend/.env.example`.
