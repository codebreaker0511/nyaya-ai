from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api import chat
from app.db.database import Base, engine
from app.models import conversation, message, session  # noqa: F401  (registers tables)
from app.services import rag


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)  # creates any missing tables on startup
    rag.load_index()  # read the legal index into memory now, not during the first user's request
    yield


app = FastAPI(title="Nyaya AI", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-Session-Token"],
)

app.include_router(chat.router)


@app.get("/health")
def health():
    return {"status": "ok"}
