from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import config
from app.api import chat
from app.db.database import Base, engine
from app.models import conversation, message, session  # noqa: F401  (registers tables)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)  # creates any missing tables on startup
    yield


app = FastAPI(title="Nyaya AI", lifespan=lifespan)

# Credentialed requests require explicit origins (never "*").
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)


@app.get("/health")
def health():
    return {"status": "ok"}
