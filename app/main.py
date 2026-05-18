from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app import __version__
from app.db.connection import DEFAULT_DB_PATH
from app.db.store import Store


def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    """Build a FastAPI app bound to `db_path`. Tests pass tmp paths here."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(db_path)
        app.state.store = store
        app.state.sheet = store.load()
        yield

    app = FastAPI(title="leonsheet", version=__version__, lifespan=lifespan)

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> str:
        return "ok"

    return app


# Default app for uvicorn entry point. Lifespan only runs on server startup,
# so importing this module does not create the DB.
app = create_app()
