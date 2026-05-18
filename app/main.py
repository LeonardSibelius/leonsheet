"""FastAPI app + HTTP routes.

create_app(db_path) builds a fresh app bound to the given DB. The default
app (for uvicorn entry) uses ~/.leonsheet/sheet.db; tests pass tmp paths.

Routes:
  * GET  /healthz                — liveness check
  * GET  /                        — full 26x100 grid page
  * POST /cell/{row}/{col}        — commit a cell edit; respond with
                                    per-cell OOB fragments for every affected cell

The Sheet is single-instance, accessed via app.state.sheet. A threading.Lock
serializes edits — FastAPI runs sync handlers in a thread pool, and the Sheet
itself is not thread-safe. Single-user assumption at v1.0; predicted bug #16's
per-cell version counter is a v1.1+ concern (real concurrent editing arrives
with v2.0's WebSocket multi-user).
"""

from contextlib import asynccontextmanager
from pathlib import Path
import math
import threading

from fastapi import FastAPI, Form, Path as PathParam, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

from app import __version__
from app.db.connection import DEFAULT_DB_PATH
from app.db.store import Store
from app.formula.refs import col_index_to_letters
from app.formula.values import ErrorValue, Value, _EmptyType

_TEMPLATES_DIR = Path(__file__).parent / "templates"

NUM_ROWS = 100
NUM_COLS = 26


def format_display(v: Value) -> str:
    """Render a Value as the string that goes in the cell's `value` attribute.

    Predicted bug #7 (float precision): 0.1+0.2 stores as 0.30000000000000004
    but displays as "0.3". 6 significant figures is the same precision Excel
    uses in its default display. Full precision stays in computed[] so chained
    formulas keep their accuracy.
    """
    if isinstance(v, ErrorValue):
        return v.code
    if isinstance(v, _EmptyType):
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            # Should never happen — evaluator guards inf/nan — but defensive.
            return "#NUM!"
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.6g}"
    if isinstance(v, str):
        return v
    return ""


def _is_error(v: Value) -> bool:
    return isinstance(v, ErrorValue)


def create_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    """Build a FastAPI app bound to the given DB. Tests pass tmp paths."""

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    sheet_lock = threading.Lock()

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

    @app.get("/", response_class=HTMLResponse)
    def grid(request: Request):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "num_rows": NUM_ROWS,
                "num_cols": NUM_COLS,
                "col_letter": col_index_to_letters,
                "sheet": request.app.state.sheet,
                "format_display": format_display,
                "is_error": _is_error,
            },
        )

    @app.post("/cell/{row}/{col}", response_class=HTMLResponse)
    def update_cell(
        request: Request,
        row: int = PathParam(..., ge=0, lt=NUM_ROWS),
        col: int = PathParam(..., ge=0, lt=NUM_COLS),
        raw_value: str = Form(""),
    ):
        sheet = request.app.state.sheet
        store = request.app.state.store

        with sheet_lock:
            affected = sheet.set_cell(row, col, raw_value)
            store.persist_cells(sheet, [(row, col)])

        # Render every affected cell. The edited cell is the hx-target; all
        # others ride in as hx-swap-oob="true" fragments — the design call
        # from Checkpoint 1 (per-cell swap, not per-row).
        cell_macro = templates.env.get_template("macros.html").module.cell
        fragments: list[str] = []
        for (r, c) in sorted(affected):  # sorted for deterministic response order
            value = sheet.get_value(r, c)
            fragments.append(
                str(
                    cell_macro(
                        r,
                        c,
                        sheet.get_raw(r, c),
                        format_display(value),
                        _is_error(value),
                        oob=(r != row or c != col),
                    )
                )
            )
        return HTMLResponse("\n".join(fragments))

    return app


# Default app for uvicorn entry point. Lifespan only runs on server startup,
# so importing this module does not create the DB.
app = create_app()
