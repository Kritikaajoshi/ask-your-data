"""FastAPI app: REST API plus the single-page frontend in /static.

Run locally with:
    uvicorn app.main:app --reload
"""

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import agent
from app.config import BASE_DIR, Settings, load_settings
from app.datasets import DatasetError, DatasetInfo, DatasetStore
from app.llm import GeminiClient, LLMClient, LLMError
from app.rate_limit import RateLimiter
from app.sql_safety import run_select

STATIC_DIR = BASE_DIR / "static"
SAMPLE_CSV = BASE_DIR / "sample_data" / "sales.csv"
ALLOWED_EXTENSIONS = {".csv", ".tsv", ".txt"}
PREVIEW_ROWS = 8


class AskRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=agent.MAX_QUESTION_LENGTH)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = DatasetStore(
        settings.data_dir, settings.max_rows, settings.max_columns, settings.dataset_ttl_hours
    )
    limiter = RateLimiter(settings.rate_limit_requests, settings.rate_limit_window_seconds)
    if llm is None and settings.gemini_api_key:
        llm = GeminiClient(settings.gemini_api_key, settings.gemini_model)

    app = FastAPI(title="Ask Your Data", version="1.0.0")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def dataset_payload(info: DatasetInfo) -> dict:
        preview = run_select(
            store.db_path(info.id),
            f'SELECT * FROM "{info.table}" LIMIT {PREVIEW_ROWS}',
            row_limit=PREVIEW_ROWS,
            timeout_ms=settings.query_timeout_ms,
        )
        payload = info.to_dict()
        payload["preview"] = {"columns": preview.columns, "rows": preview.rows}
        return payload

    def client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "llm_configured": llm is not None,
            "model": settings.gemini_model if llm is not None else None,
        }

    @app.post("/api/datasets", status_code=201)
    async def upload_dataset(file: UploadFile = File(...)):
        filename = file.filename or "upload.csv"
        if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, "Please upload a .csv file.")

        raw = await file.read(settings.max_upload_bytes + 1)
        if len(raw) > settings.max_upload_bytes:
            raise HTTPException(413, f"The file is larger than {settings.max_upload_mb} MB.")
        try:
            info = store.create(raw, filename)
        except DatasetError as exc:
            raise HTTPException(400, str(exc)) from None
        return dataset_payload(info)

    @app.post("/api/datasets/sample", status_code=201)
    def load_sample():
        info = store.create(SAMPLE_CSV.read_bytes(), SAMPLE_CSV.name)
        return dataset_payload(info)

    @app.get("/api/datasets/{dataset_id}")
    def get_dataset(dataset_id: str):
        try:
            info = store.get(dataset_id)
        except DatasetError:
            raise HTTPException(404, "Dataset not found. It may have expired, so upload it again.")
        return dataset_payload(info)

    @app.post("/api/ask")
    def ask(body: AskRequest, request: Request):
        if llm is None:
            raise HTTPException(503, "The AI model is not configured. Set GEMINI_API_KEY.")
        if not limiter.allow(client_key(request)):
            raise HTTPException(429, "Too many questions in a short time. Wait a few minutes.")
        try:
            info = store.get(body.dataset_id)
            db_path = store.db_path(body.dataset_id)
        except DatasetError:
            raise HTTPException(404, "Dataset not found. It may have expired, so upload it again.")

        try:
            result = agent.ask(
                body.question,
                info,
                db_path,
                llm,
                max_attempts=settings.max_attempts,
                row_limit=settings.result_row_limit,
                timeout_ms=settings.query_timeout_ms,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from None
        return result.to_dict()

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
