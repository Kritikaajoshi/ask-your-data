"""Safety layer for SQL written by the LLM.

The model's SQL is never trusted. It goes through three independent checks:
    1. validate_sql: one statement only, and it must start with SELECT or WITH.
    2. The database file is opened in read-only mode.
    3. A SQLite authorizer rejects any operation that is not a read.
A timeout and a row limit also stop runaway queries.
"""

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


class UnsafeQueryError(ValueError):
    """The SQL was rejected before running it."""


class QueryError(RuntimeError):
    """The SQL was allowed but failed while running."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool


def _strip_comments(sql: str) -> tuple[str, bool]:
    """Remove SQL comments. Also report whether a ; appears outside of quotes."""
    out: list[str] = []
    has_separator = False
    i = 0
    quote: str | None = None
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if quote:
            out.append(ch)
            if ch == quote:
                if nxt == quote:  # escaped quote like 'It''s'
                    out.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "[":
            end = sql.find("]", i)
            end = len(sql) - 1 if end == -1 else end
            out.append(sql[i : end + 1])
            i = end + 1
            continue
        if ch == "-" and nxt == "-":
            end = sql.find("\n", i)
            i = len(sql) if end == -1 else end
            continue
        if ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            i = len(sql) if end == -1 else end + 2
            out.append(" ")
            continue
        if ch == ";":
            has_separator = True
        out.append(ch)
        i += 1
    return "".join(out), has_separator


def validate_sql(sql: str) -> str:
    """Return a cleaned single SELECT statement, or raise UnsafeQueryError."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("The query is empty.")

    cleaned = sql.strip()
    # Allow (and drop) semicolons at the very end, like "SELECT 1;".
    cleaned = re.sub(r"[;\s]+$", "", cleaned)
    cleaned, has_separator = _strip_comments(cleaned)
    cleaned = cleaned.strip()
    cleaned = re.sub(r"[;\s]+$", "", cleaned)

    if not cleaned:
        raise UnsafeQueryError("The query is empty.")
    if has_separator:
        raise UnsafeQueryError("Only one SQL statement is allowed.")

    first_word = re.match(r"^\(*\s*([A-Za-z]+)", cleaned)
    if not first_word or first_word.group(1).upper() not in {"SELECT", "WITH"}:
        raise UnsafeQueryError("Only SELECT queries are allowed.")
    return cleaned


_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
_BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _BLOCKED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _json_safe(value):
    if isinstance(value, bytes):
        return value.hex()
    return value


def run_select(db_path: Path, sql: str, row_limit: int, timeout_ms: int) -> QueryResult:
    """Validate and run a query against a dataset in read-only mode."""
    query = validate_sql(sql)
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    deadline = time.monotonic() + timeout_ms / 1000

    def stop_if_slow():
        return 1 if time.monotonic() > deadline else 0

    try:
        conn.set_authorizer(_authorizer)
        conn.set_progress_handler(stop_if_slow, 1000)
        try:
            cursor = conn.execute(query)
            columns = [d[0] for d in cursor.description or []]
            fetched = cursor.fetchmany(row_limit + 1)
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            if "interrupted" in message.lower():
                message = f"The query took longer than {timeout_ms} ms and was stopped."
            elif "not authorized" in message.lower():
                message = "The query tried an operation that is not allowed (read-only access)."
            raise QueryError(message) from None
    finally:
        conn.close()

    truncated = len(fetched) > row_limit
    rows = [[_json_safe(v) for v in row] for row in fetched[:row_limit]]
    return QueryResult(columns=columns, rows=rows, truncated=truncated)
