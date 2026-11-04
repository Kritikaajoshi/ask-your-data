"""Turns an uploaded CSV file into a SQLite database with one table named "data".

Each dataset is stored as two files in the data directory:
    <id>.db    the SQLite database
    <id>.json  metadata (original file name, columns, row count)
"""

import csv
import io
import json
import re
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

TABLE_NAME = "data"

_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_INT_PATTERN = re.compile(r"^[+-]?\d+$")
_FLOAT_PATTERN = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
_NULL_VALUES = {"", "null", "none", "nan", "n/a"}
_SQLITE_INT_MAX = 2**63 - 1

# Common SQL words that would be awkward as column names.
_RESERVED = {
    "select", "from", "where", "group", "order", "by", "limit", "table",
    "index", "join", "on", "as", "and", "or", "not", "null", "is", "in",
    "case", "when", "then", "else", "end", "having", "union", "values",
    "insert", "update", "delete", "create", "drop", "distinct", "default",
    "check", "primary", "key", "references", "to", "with", "all", "into",
}


class DatasetError(ValueError):
    """Raised when a CSV file cannot be turned into a dataset."""


@dataclass
class Column:
    name: str
    original_name: str
    type: str


@dataclass
class DatasetInfo:
    id: str
    filename: str
    table: str
    row_count: int
    columns: list[Column] = field(default_factory=list)
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DatasetInfo":
        columns = [Column(**c) for c in data.get("columns", [])]
        return cls(
            id=data["id"],
            filename=data["filename"],
            table=data["table"],
            row_count=data["row_count"],
            columns=columns,
            created_at=data.get("created_at", 0.0),
        )


def clean_column_names(headers: list[str]) -> list[str]:
    """Make column names safe and easy to use in SQL (lowercase, underscores, unique)."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for position, header in enumerate(headers, start=1):
        name = re.sub(r"[^0-9a-zA-Z]+", "_", (header or "").strip()).strip("_").lower()
        if not name:
            name = f"column_{position}"
        if name[0].isdigit():
            name = f"col_{name}"
        if name in _RESERVED:
            name = f"{name}_value"
        base = name
        counter = 2
        while name in seen:
            name = f"{base}_{counter}"
            counter += 1
        seen.add(name)
        cleaned.append(name)
    return cleaned


def _is_null(value: str) -> bool:
    return value.strip().lower() in _NULL_VALUES


def _looks_like_int(value: str) -> bool:
    text = value.strip()
    if not _INT_PATTERN.match(text):
        return False
    digits = text.lstrip("+-")
    # Values like zip codes ("02134") keep their leading zero, so treat them as text.
    if len(digits) > 1 and digits.startswith("0"):
        return False
    return abs(int(text)) <= _SQLITE_INT_MAX


def _looks_like_float(value: str) -> bool:
    text = value.strip()
    if not _FLOAT_PATTERN.match(text):
        return False
    digits = text.lstrip("+-")
    if len(digits) > 1 and digits.startswith("0") and not digits.startswith("0."):
        return False
    return True


def infer_column_type(values: list[str]) -> str:
    """Pick INTEGER, REAL, or TEXT based on every non-empty value in the column."""
    present = [v for v in values if not _is_null(v)]
    if not present:
        return "TEXT"
    if all(_looks_like_int(v) for v in present):
        return "INTEGER"
    if all(_looks_like_int(v) or _looks_like_float(v) for v in present):
        return "REAL"
    return "TEXT"


def _convert(value: str, column_type: str):
    if _is_null(value):
        return None
    text = value.strip()
    if column_type == "INTEGER":
        return int(text)
    if column_type == "REAL":
        return float(text)
    return text


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _detect_dialect(text: str):
    sample = text[:20_000]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def parse_csv(raw: bytes, max_rows: int, max_columns: int) -> tuple[list[str], list[list[str]]]:
    """Read CSV bytes into a header row and data rows (all as strings)."""
    if not raw.strip():
        raise DatasetError("The file is empty.")
    text = _decode(raw)
    reader = csv.reader(io.StringIO(text), _detect_dialect(text))
    try:
        header = next(reader)
    except StopIteration:
        raise DatasetError("The file is empty.") from None
    except csv.Error as exc:
        raise DatasetError(f"Could not read the CSV file: {exc}") from None

    if not any(h.strip() for h in header):
        raise DatasetError("The first row must contain column names.")
    if len(header) > max_columns:
        raise DatasetError(f"The file has {len(header)} columns. The limit is {max_columns}.")

    width = len(header)
    rows: list[list[str]] = []
    try:
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            if len(rows) >= max_rows:
                raise DatasetError(f"The file has more than {max_rows:,} rows.")
            # Pad short rows and drop extra cells so every row matches the header.
            rows.append((row + [""] * width)[:width])
    except csv.Error as exc:
        raise DatasetError(f"Could not read the CSV file: {exc}") from None

    if not rows:
        raise DatasetError("The file has column names but no data rows.")
    return header, rows


def build_database(db_path: Path, header: list[str], rows: list[list[str]]) -> list[Column]:
    """Create the SQLite file with typed columns and insert every row."""
    names = clean_column_names(header)
    types = [infer_column_type([row[i] for row in rows]) for i in range(len(names))]
    columns = [Column(name=n, original_name=h, type=t) for n, h, t in zip(names, header, types)]

    column_sql = ", ".join(f'"{c.name}" {c.type}' for c in columns)
    placeholders = ", ".join("?" for _ in columns)

    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(f'CREATE TABLE "{TABLE_NAME}" ({column_sql})')
            conn.executemany(
                f'INSERT INTO "{TABLE_NAME}" VALUES ({placeholders})',
                ([_convert(row[i], columns[i].type) for i in range(len(columns))] for row in rows),
            )
    finally:
        conn.close()
    return columns


class DatasetStore:
    """Saves, finds, and cleans up datasets on disk."""

    def __init__(self, data_dir: Path, max_rows: int, max_columns: int, ttl_hours: int):
        self.data_dir = Path(data_dir)
        self.max_rows = max_rows
        self.max_columns = max_columns
        self.ttl_seconds = ttl_hours * 3600
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, dataset_id: str) -> tuple[Path, Path]:
        if not _ID_PATTERN.match(dataset_id or ""):
            raise DatasetError("Unknown dataset.")
        return self.data_dir / f"{dataset_id}.db", self.data_dir / f"{dataset_id}.json"

    def create(self, raw: bytes, filename: str) -> DatasetInfo:
        self.cleanup_expired()
        header, rows = parse_csv(raw, self.max_rows, self.max_columns)

        dataset_id = uuid.uuid4().hex
        db_path, meta_path = self._paths(dataset_id)
        tmp_path = db_path.with_suffix(".tmp")
        try:
            columns = build_database(tmp_path, header, rows)
            tmp_path.replace(db_path)
        finally:
            tmp_path.unlink(missing_ok=True)

        info = DatasetInfo(
            id=dataset_id,
            filename=Path(filename or "upload.csv").name[:200],
            table=TABLE_NAME,
            row_count=len(rows),
            columns=columns,
            created_at=time.time(),
        )
        meta_path.write_text(json.dumps(info.to_dict()), encoding="utf-8")
        return info

    def get(self, dataset_id: str) -> DatasetInfo:
        db_path, meta_path = self._paths(dataset_id)
        if not db_path.exists() or not meta_path.exists():
            raise DatasetError("Unknown dataset.")
        return DatasetInfo.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))

    def db_path(self, dataset_id: str) -> Path:
        db_path, _ = self._paths(dataset_id)
        if not db_path.exists():
            raise DatasetError("Unknown dataset.")
        return db_path

    def cleanup_expired(self) -> int:
        if self.ttl_seconds <= 0:
            return 0
        cutoff = time.time() - self.ttl_seconds
        removed = 0
        for path in self.data_dir.glob("*"):
            if path.suffix in {".db", ".json"} and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed
