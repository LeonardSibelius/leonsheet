from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app import __version__

app = FastAPI(title="leonsheet", version=__version__)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"
