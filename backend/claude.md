# Nyaya AI — Project Instructions for Claude Code

## 1. Project Overview

Nyaya AI is an AI-powered legal awareness assistant designed to close the gap between Indian laws and ordinary people.

The goal is to help ordinary citizens discover relevant laws, understand their legal rights, and receive clear explanations of legal provisions without needing legal expertise.

Users should be able to:
- Ask legal questions in natural language.
- Ask about specific Articles, Acts, or sections of Indian law.
- Receive understandable answers supported by authoritative legal sources.
- View citations and links to official sources.
- Create and manage multiple conversations.
- Reopen the application and retrieve previously stored conversations.
- Continue conversations with contextual memory.
- Use the application without registration, login, or passwords.

This is a 48-hour hackathon MVP. Prioritize a functional, reliable, and deployable product over unnecessary complexity.

## 2. Technology Stack

### Backend
- Python
- FastAPI
- SQLAlchemy ORM
- PostgreSQL
- Psycopg 3 PostgreSQL driver
- Pydantic
- python-dotenv
- An accessible LLM API
- Retrieval-Augmented Generation (RAG)

### Frontend
- React
- Vite
- JavaScript

The frontend and backend must be separate applications.

Do not introduce TypeScript, Next.js, Django, or another backend framework.

Keep the implementation simple enough for a developer who is actively learning backend engineering to understand, maintain, and debug.

## 3. High-Level Architecture

The application consists of four main components:

1. **Frontend:** Displays the chat interface, conversation list, messages, and citations.
2. **FastAPI backend:** Handles API requests, anonymous sessions, database operations, RAG, and LLM integration.
3. **PostgreSQL:** Persists anonymous sessions, conversations, and messages.
4. **Legal knowledge base:** Stores searchable passages from authoritative Indian legal documents, together with source metadata.

The request flow should be:

1. A user submits a question through the frontend.
2. FastAPI identifies and validates the anonymous session.
3. The backend verifies that the requested conversation belongs to that session.
4. The user's message is persisted.
5. The RAG service retrieves relevant legal provisions.
6. The LLM receives the question, relevant conversation context, and retrieved legal evidence.
7. The generated response is checked for valid source references and returned to the frontend.
8. The assistant's response is persisted in PostgreSQL.
9. The frontend displays the answer and its supporting sources.

Do not build separate microservices for these components. Keep them within the same FastAPI application.

## 4. Backend Folder Structure

The backend must follow this structure:

```text
backend/
├── app/
│   ├── main.py
│   │
│   ├── api/
│   │   └── chat.py
│   │
│   ├── db/
│   │   └── database.py
│   │
│   ├── models/
│   │   ├── session.py
│   │   ├── conversation.py
│   │   └── message.py
│   │
│   ├── schemas/
│   │   └── chat.py
│   │
│   └── services/
│       ├── rag.py
│       └── llm.py
│
├── tests/
│   ├── test_db.py
│   └── test_chat.py
│
├── .env
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

Create additional modules only when there is a clear requirement. Do not create empty files simply to match the tree.

### Responsibilities

**`app/main.py`**
- Initialize the FastAPI application.
- Configure CORS and middleware.
- Register API routers.
- Expose a health-check endpoint.

**`app/api/chat.py`**
- Implement conversation and message endpoints.
- Validate session ownership.
- Coordinate database operations and the RAG/LLM pipeline.
- Return structured API responses.

**`app/db/database.py`**
- Load environment variables.
- Configure the SQLAlchemy engine.
- Configure `SessionLocal`.
- Define the SQLAlchemy declarative base.
- Provide a database-session dependency for FastAPI endpoints.

**`app/models/session.py`**
- Define the anonymous session database model.

**`app/models/conversation.py`**
- Define conversations and their relationship with sessions.

**`app/models/message.py`**
- Define messages and their relationship with conversations.

**`app/schemas/chat.py`**
- Define Pydantic request and response models.
- Validate message content, identifiers, and response structures.

**`app/services/rag.py`**
- Retrieve relevant passages from the legal knowledge base.
- Preserve source metadata and provision references.
- Return evidence suitable for use by the LLM.

**`app/services/llm.py`**
- Call the configured LLM API.
- Supply the question, relevant conversation context, and retrieved legal evidence.
- Generate structured responses.
- Handle timeouts, API failures, and rate limits.

**`tests/test_db.py`**
- Verify database connectivity and persistence.

**`tests/test_chat.py`**
- Test API behavior, conversation persistence, session isolation, and error handling.

Keep database models, Pydantic schemas, API routing, and business logic separate.

## 5. Database and SQLAlchemy

Use PostgreSQL as the persistent database and SQLAlchemy as the Python database access layer.

Do not use Python dictionaries or SQLite as the primary storage mechanism.

### Database connection

Use `DATABASE_URL` from environment variables.

For local development, the URL will point to the local PostgreSQL database. For production, it will point to the hosted PostgreSQL database.

Use the Psycopg 3 SQLAlchemy driver.

Example local configuration:

```env
DATABASE_URL=postgresql+psycopg://postgres:YOUR_PASSWORD@localhost:5432/nyaya_ai
```

Never hardcode the connection string in Python files.

### SQLAlchemy implementation

- Use SQLAlchemy declarative models.
- Define foreign keys and relationships.
- Add appropriate indexes.
- Configure database sessions correctly.
- Use dependency injection for database sessions in FastAPI.
- Commit successful transactions and roll back failed transactions.
- Close database sessions appropriately.
- Avoid opening database connections as unintended side effects of module imports.
- Use a consistent, reproducible database table-creation or migration strategy.

Do not introduce a migration framework unless it is useful for the current stage. If schema changes become frequent, use Alembic.

## 6. Database Schema

The MVP needs three primary tables.

### A. Sessions

The `sessions` table identifies an anonymous visitor without requiring an account.

Suggested columns:

- `id`: Primary key.
- `token_hash`: Unique hash of the anonymous session token.
- `created_at`: Session creation timestamp.
- `last_active_at`: Last activity timestamp.
- `expires_at`: Session expiry timestamp.

Generate tokens using a cryptographically secure random generator. Store the token hash in PostgreSQL rather than the raw token.

### B. Conversations

The `conversations` table stores chat threads.

Suggested columns:

- `id`: Primary key.
- `session_id`: Foreign key to `sessions.id`.
- `title`: Conversation title.
- `created_at`: Creation timestamp.
- `updated_at`: Last update timestamp.

A session can own multiple conversations.

### C. Messages

The `messages` table stores the messages belonging to each conversation.

Suggested columns:

- `id`: Primary key.
- `conversation_id`: Foreign key to `conversations.id`.
- `role`: Either `user` or `assistant`.
- `content`: Message text.
- `created_at`: Message timestamp.

A conversation can contain multiple messages.

Use database-generated identifiers where appropriate. Do not depend on sequential identifiers being exposed publicly.

The three tables are sufficient for initial conversation persistence. Add separate tables for legal documents and passages when implementing the RAG knowledge base.

## 7. Anonymous Session Management

The application must not require login, registration, email verification, or passwords.

Users should be able to open the public application immediately.

### First visit

1. The frontend requests session initialization.
2. FastAPI generates a cryptographically secure random session token.
3. The backend stores the corresponding token hash in PostgreSQL.
4. The raw token is returned in a properly configured cookie.
5. The frontend can create conversations under that session.

### Subsequent visits

1. The browser sends the existing cookie.
2. FastAPI hashes and validates the token.
3. The backend verifies that the session exists and has not expired.
4. The application retrieves conversations belonging to the session.
5. The frontend displays the conversation list and saved messages.

Configure cookies appropriately:
- `HttpOnly` enabled.
- `Secure` enabled in production.
- An appropriate `SameSite` policy.
- A defined lifetime consistent with session expiry.

Use an initial inactivity expiry of seven days. Refresh session activity when appropriate.

When the session is expired or invalid, create a new session through the initialization flow.

Because the frontend and backend are separate applications, configure CORS and credentials correctly. Never use wildcard origins with credentialed requests.

### Security requirements

- Verify session ownership on every conversation-specific operation.
- Do not treat possession of a conversation ID as authorization.
- Do not allow users to access another session's conversations by modifying IDs.
- Do not log raw session tokens.
- Provide a way to delete conversations.
- Do not claim that a session can be recovered automatically across different browsers or devices.

Anonymous sessions are not a replacement for access-control checks.

## 8. API Endpoints

Implement the API incrementally.

### Health check

`GET /health`

Returns the backend health status.

### Session initialization

`POST /api/sessions`

Creates or initializes an anonymous session and sets the session cookie when necessary.

### Conversations

`POST /api/conversations`

Creates a conversation for the current session.

`GET /api/conversations`

Returns conversations belonging to the current session.

`GET /api/conversations/{conversation_id}/messages`

Returns the messages in a conversation after checking ownership.

`DELETE /api/conversations/{conversation_id}`

Deletes a conversation and its associated messages after checking ownership.

### Sending a message

`POST /api/conversations/{conversation_id}/messages`

The request should contain the user's message.

The backend must:

1. Validate the session.
2. Verify conversation ownership.
3. Validate the request using Pydantic.
4. Persist the user message.
5. Retrieve relevant legal evidence.
6. Generate a grounded answer through the LLM.
7. Validate and prepare the response.
8. Persist the assistant message.
9. Return the answer and its source references.

Use appropriate HTTP status codes and structured error responses.

Do not create unnecessary endpoints for every internal RAG operation.

## 9. RAG and Legal Knowledge Base

The central product requirement is to provide understandable legal information grounded in verifiable sources.

The LLM must not be treated as the authoritative source of law.

### Source strategy

Prioritize authoritative Indian legal sources, including:
- India Code.
- Official government publications of the Constitution of India.
- Other relevant official legal publications.

Do not assume that every law or provision has been ingested. Keep the supported legal scope explicit.

### Legal document preparation

For each ingested document or passage, preserve relevant metadata such as:

- Document or Act title.
- Article, section, or provision identifier.
- Passage text.
- Official source URL.
- Available publication, amendment, or version information.
- Document or passage identifier.

Do not fabricate missing metadata or official URLs.

Where current legal status matters, verify the source and applicable version instead of assuming that a retrieved passage reflects the current law.

### Retrieval pipeline

Implement the following flow:

1. Accept the user's question.
2. Retrieve relevant legal passages.
3. Rank the passages by relevance.
4. Preserve the associated source metadata.
5. Check whether the evidence is sufficient for an answer.
6. Supply the retrieved passages to the LLM.
7. Return the explanation with source references.
8. Acknowledge insufficient or conflicting evidence.

Start with the simplest reliable retrieval approach possible. Use keyword search first if necessary. Introduce embeddings and vector search when they provide a clear benefit.

If using PostgreSQL for vector retrieval, consider `pgvector`. Do not add it before establishing a working end-to-end pipeline.

The legal knowledge base must be separate from the conversational memory.

Never use previous assistant answers as authoritative legal evidence.

## 10. LLM Integration

Keep LLM integration in `app/services/llm.py`.

Read the API key and model configuration from environment variables.

The LLM service should accept:
- The current user question.
- Relevant recent conversation history.
- Retrieved legal passages and their metadata.

It should return a structured response that the API layer can validate.

### Answer requirements

The assistant should:
- Explain provisions in plain language.
- Cite relevant Articles, Acts, and sections when supported by the retrieved evidence.
- Link citations to authoritative sources.
- Distinguish statutory text from explanation or interpretation.
- Avoid inventing provisions, citations, quotations, or legal claims.
- Ask clarifying questions when relevant facts are missing.
- Acknowledge insufficient evidence.
- Explain uncertainty where appropriate.
- Avoid presenting general legal information as a definitive legal opinion.

A system prompt is not sufficient to guarantee correctness. Retrieval quality, source integrity, and citation verification must also be considered.

Do not fine-tune a model for the MVP. Use an accessible model API.

Do not assume a specific model name is available without verifying access.

Never expose the API key or internal provider errors to the frontend.

## 11. Conversation Memory

Conversation memory and legal retrieval are separate.

PostgreSQL stores the full conversation history. The backend retrieves a bounded amount of recent history when generating each response.

For a follow-up question such as "What if my landlord refuses?", the previous messages help interpret the context.

However, the backend must retrieve legal evidence again for each new question. Previous conversation history must not replace legal retrieval.

Do not send the entire conversation to the LLM indefinitely. Use a reasonable context limit and introduce summaries only if necessary.

## 12. Environment Variables

The backend `.env` may contain:

```env
DATABASE_URL=
LLM_API_KEY=
LLM_MODEL=
FRONTEND_ORIGIN=http://localhost:5173
```

Use the exact variable names adopted by the implementation.

Create `.env.example` containing placeholders rather than real credentials.

Ensure `.env` is listed in `.gitignore`.

Never commit API keys, passwords, database credentials, or session tokens to GitHub.

## 13. Error Handling and Reliability

- Validate incoming request bodies.
- Handle invalid or expired sessions.
- Handle unauthorized conversation access.
- Handle missing conversations and messages.
- Handle database connection failures.
- Handle LLM timeouts, rate limits, and API errors.
- Use sensible request timeouts and payload-size limits.
- Avoid duplicate message persistence if a request is retried.
- Do not return fabricated legal answers when retrieval or LLM generation fails.
- Avoid exposing stack traces and secrets to users.
- Keep application logs useful without unnecessarily recording sensitive legal queries.

Test the behavior for insufficient legal evidence and unavailable LLM services.

## 14. Testing

Prioritize practical tests for:

1. PostgreSQL connectivity.
2. Session creation and validation.
3. Conversation creation.
4. Message persistence.
5. Retrieving stored conversations after restarting the backend.
6. Session isolation and unauthorized access.
7. Invalid request payloads.
8. RAG retrieval of expected legal provisions.
9. Citation correctness.
10. Insufficient-evidence handling.
11. LLM failures.
12. End-to-end chat behavior.

For the hackathon demo, prepare a small set of legal questions whose relevant provisions have been independently checked against official sources.

Do not claim that the system is legally accurate based only on successful API calls or the presence of citations.

## 15. Deployment

The backend will be deployed on Render or another suitable hosting platform.

Use a persistent hosted PostgreSQL database in production.

The frontend will be deployed separately.

Before deployment:
- Configure environment variables.
- Configure production CORS origins.
- Configure secure cookies.
- Verify the database connection.
- Ensure database schema creation or migrations run correctly.
- Verify that conversation history survives application restarts.
- Test session behavior across page refreshes.
- Test the deployed application using its public URL.

Do not assume that local database URLs, development CORS settings, or development cookie settings will work in production.

Check the hosting provider's database pricing, persistence guarantees, connection limits, and expiry policy before selecting a service.

## 16. Development Workflow

The developer is learning Python backend engineering and wants to understand the implementation rather than blindly copy a complete codebase.

Follow these instructions:

1. Inspect the existing files before proposing or making changes.
2. Preserve working code and avoid unnecessary rewrites.
3. Implement one logical component at a time.
4. Explain the purpose of each file and important code sections.
5. Explain unfamiliar FastAPI, SQLAlchemy, Python, and PostgreSQL concepts when relevant.
6. Provide commands with the exact directory from which they should be run.
7. Keep code readable and avoid unnecessary abstractions.
8. Do not generate the entire backend in one response.
9. Do not create empty modules without a current need.
10. Do not add dependencies without a clear reason.
11. Do not assume that commands have succeeded without seeing their output.
12. Diagnose actual tracebacks and the current working directory when debugging.
13. Provide a small, testable implementation for each step.
14. Explain how to test each implementation and what successful output should look like.
15. Wait for the developer to test the current step and share the result before moving to the next major step.
16. Do not modify files without permission when the developer has requested inspection or a proposed plan only.
17. Do not claim to have executed or verified code unless it has actually been executed or verified.

The objective is to help the developer build real backend engineering skills while completing the application.

## 17. Implementation Priority

Follow this order:

1. Inspect the existing project and verify the FastAPI application.
2. Establish and test the local PostgreSQL connection.
3. Implement the SQLAlchemy models.
4. Create and verify the database tables.
5. Implement anonymous session initialization and validation.
6. Implement conversation CRUD.
7. Implement message persistence.
8. Test session isolation and persistence.
9. Acquire and prepare authoritative legal documents.
10. Implement legal retrieval.
11. Integrate the LLM using retrieved evidence.
12. Connect the React chat interface.
13. Test end-to-end behavior and citation correctness.
14. Deploy the application.
15. Prepare a reliable live demo.

Prioritize a working end-to-end product before optional features.

## Final Instruction

Act as a technical mentor and implementation partner.

Help build Nyaya AI incrementally, keeping the architecture simple and appropriate for a 48-hour hackathon.

Do not blindly generate large amounts of code. Inspect the current state, explain the next step, implement it, and verify it before proceeding.

The final product should let ordinary people ask questions about Indian law, understand the answers in plain language, and verify those answers against authoritative legal sources—without requiring an account.