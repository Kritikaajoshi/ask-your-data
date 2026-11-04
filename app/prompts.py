"""Everything the LLM sees: the table description and the instructions."""

import re
import sqlite3
from pathlib import Path

from app.datasets import DatasetInfo

SAMPLE_ROWS = 5
MAX_CATEGORY_VALUES = 25

SQL_SYSTEM_PROMPT = """You are a careful data analyst who writes SQLite queries.

You answer questions about a single table by writing exactly one SQLite SELECT query.

Rules:
1. Reply with the query inside a ```sql code block and nothing else.
2. Use only the table and columns listed in the schema. Wrap every column name in double quotes.
3. Only read data. Never write INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, PRAGMA, or ATTACH.
4. Text matching is case sensitive in SQLite. When the user mentions a category, use the exact spelling from the listed values.
5. Dates are stored as text like 2025-03-14. Use strftime() or substr() to group by month or year.
6. For averages, percentages, and ratios, use decimal math (multiply by 100.0, not 100) and ROUND(value, 2).
7. Give every computed column a short, readable alias, for example total_revenue.
8. For "top", "most", "highest", or "lowest" questions, use ORDER BY with LIMIT.
9. If the question cannot be answered from this table, reply with one line that starts with CANNOT_ANSWER: followed by a short reason.
"""

ANSWER_SYSTEM_PROMPT = """You explain query results to a non-technical person.

Rules:
1. Answer the question in one to three plain sentences.
2. Use only numbers that appear in the results. Never guess or invent values.
3. Format large numbers with commas and keep at most two decimal places.
4. Do not mention SQL, queries, tables, or rows.
5. Plain text only. No markdown, no bullet points.
6. If the results are empty, say that no matching data was found.
"""


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def describe_dataset(info: DatasetInfo, db_path: Path) -> str:
    """Build a compact text description of the table: columns, types, ranges, sample rows."""
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        lines = [f'Table "{info.table}" has {info.row_count:,} rows and these columns:']
        for column in info.columns:
            col = _quote(column.name)
            detail = f"- {col} {column.type}"
            if column.original_name and column.original_name != column.name:
                detail += f' (original header: "{column.original_name}")'

            if column.type in ("INTEGER", "REAL"):
                low, high = conn.execute(
                    f"SELECT MIN({col}), MAX({col}) FROM {_quote(info.table)}"
                ).fetchone()
                if low is not None:
                    detail += f", ranges from {low} to {high}"
            else:
                distinct = conn.execute(
                    f"SELECT COUNT(DISTINCT {col}) FROM {_quote(info.table)}"
                ).fetchone()[0]
                if 0 < distinct <= MAX_CATEGORY_VALUES:
                    values = [
                        row[0]
                        for row in conn.execute(
                            f"SELECT DISTINCT {col} FROM {_quote(info.table)} "
                            f"WHERE {col} IS NOT NULL ORDER BY {col}"
                        )
                    ]
                    detail += ", values: " + ", ".join(repr(v) for v in values)
                else:
                    detail += f", {distinct:,} distinct values"
            lines.append(detail)

        cursor = conn.execute(f"SELECT * FROM {_quote(info.table)} LIMIT {SAMPLE_ROWS}")
        header = [d[0] for d in cursor.description]
        lines.append("")
        lines.append(f"First {SAMPLE_ROWS} rows:")
        lines.append(" | ".join(header))
        for row in cursor.fetchall():
            lines.append(" | ".join("NULL" if v is None else str(v) for v in row))
        return "\n".join(lines)
    finally:
        conn.close()


def build_sql_prompt(schema: str, question: str, previous_attempts: list[tuple[str, str]]) -> str:
    parts = [schema, "", f"Question: {question.strip()}"]
    if previous_attempts:
        parts.append("")
        parts.append("Your earlier attempts failed. Fix the problem and try again.")
        for sql, error in previous_attempts:
            parts.append(f"Query:\n{sql}\nError: {error}")
    return "\n".join(parts)


def build_answer_prompt(question: str, columns: list[str], rows: list[list], truncated: bool) -> str:
    shown = rows[:30]
    lines = [f"Question: {question.strip()}", "", "Results:"]
    lines.append(" | ".join(columns))
    for row in shown:
        lines.append(" | ".join("NULL" if v is None else str(v) for v in row))
    if not rows:
        lines.append("(no results)")
    elif len(rows) > len(shown) or truncated:
        lines.append(f"(showing the first {len(shown)} rows of a longer result)")
    return "\n".join(lines)


_FENCE = re.compile(r"```(?:sql|sqlite)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def extract_sql(text: str) -> str:
    """Pull the SQL out of the model reply (it usually comes in a code block)."""
    match = _FENCE.search(text or "")
    if match:
        return match.group(1).strip()
    return (text or "").strip().strip("`").strip()


def extract_cannot_answer(text: str) -> str | None:
    match = re.search(r"CANNOT_ANSWER:\s*(.+)", text or "")
    if match:
        return match.group(1).strip()
    return None
