"""App settings, read from environment variables (or a local .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    data_dir: Path
    max_upload_mb: int
    max_rows: int
    max_columns: int
    result_row_limit: int
    query_timeout_ms: int
    max_attempts: int
    dataset_ttl_hours: int
    rate_limit_requests: int
    rate_limit_window_seconds: int

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip(),
        data_dir=Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))),
        max_upload_mb=_int("MAX_UPLOAD_MB", 10),
        max_rows=_int("MAX_ROWS", 200_000),
        max_columns=_int("MAX_COLUMNS", 100),
        result_row_limit=_int("RESULT_ROW_LIMIT", 500),
        query_timeout_ms=_int("QUERY_TIMEOUT_MS", 3000),
        max_attempts=_int("MAX_ATTEMPTS", 3),
        dataset_ttl_hours=_int("DATASET_TTL_HOURS", 24),
        rate_limit_requests=_int("RATE_LIMIT_REQUESTS", 30),
        rate_limit_window_seconds=_int("RATE_LIMIT_WINDOW_SECONDS", 600),
    )
