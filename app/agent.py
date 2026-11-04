"""The agent: turns a plain-English question into SQL, runs it safely, and explains the result.

If a query fails (bad column name, syntax error, blocked operation), the error is sent back
to the model so it can correct itself, up to max_attempts times.
"""

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.datasets import DatasetInfo
from app.llm import LLMClient, LLMError
from app.prompts import (
    ANSWER_SYSTEM_PROMPT,
    SQL_SYSTEM_PROMPT,
    build_answer_prompt,
    build_sql_prompt,
    describe_dataset,
    extract_cannot_answer,
    extract_sql,
)
from app.sql_safety import QueryError, UnsafeQueryError, run_select

MAX_QUESTION_LENGTH = 500


@dataclass
class Attempt:
    sql: str
    error: str


@dataclass
class AgentResult:
    question: str
    status: str  # "ok", "cannot_answer", or "failed"
    answer: str
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    failed_attempts: list[Attempt] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _fallback_answer(columns: list[str], rows: list[list], truncated: bool) -> str:
    if not rows:
        return "No matching data was found."
    if len(rows) == 1 and len(columns) == 1:
        return f"The answer is {rows[0][0]}."
    more = " (showing the first part)" if truncated else ""
    return f"Found {len(rows)} result rows{more}. See the table below."


def ask(
    question: str,
    info: DatasetInfo,
    db_path: Path,
    llm: LLMClient,
    *,
    max_attempts: int = 3,
    row_limit: int = 500,
    timeout_ms: int = 3000,
    explain: bool = True,
) -> AgentResult:
    started = time.perf_counter()
    question = (question or "").strip()
    if not question:
        raise ValueError("Please type a question.")
    if len(question) > MAX_QUESTION_LENGTH:
        raise ValueError(f"Questions can be at most {MAX_QUESTION_LENGTH} characters.")

    schema = describe_dataset(info, db_path)
    failed: list[Attempt] = []

    def finish(result: AgentResult) -> AgentResult:
        result.failed_attempts = failed
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    for _ in range(max(1, max_attempts)):
        prompt = build_sql_prompt(schema, question, [(a.sql, a.error) for a in failed])
        reply = llm.generate(SQL_SYSTEM_PROMPT, prompt)

        reason = extract_cannot_answer(reply)
        if reason and "```" not in reply:
            return finish(
                AgentResult(
                    question=question,
                    status="cannot_answer",
                    answer=f"This data cannot answer that question. {reason}",
                )
            )

        sql = extract_sql(reply)
        try:
            result = run_select(db_path, sql, row_limit=row_limit, timeout_ms=timeout_ms)
        except (UnsafeQueryError, QueryError) as exc:
            failed.append(Attempt(sql=sql, error=str(exc)))
            continue

        answer = _fallback_answer(result.columns, result.rows, result.truncated)
        if explain:
            try:
                answer = llm.generate(
                    ANSWER_SYSTEM_PROMPT,
                    build_answer_prompt(question, result.columns, result.rows, result.truncated),
                )
            except LLMError:
                pass  # keep the simple fallback answer

        return finish(
            AgentResult(
                question=question,
                status="ok",
                answer=answer.strip(),
                sql=sql,
                columns=result.columns,
                rows=result.rows,
                row_count=len(result.rows),
                truncated=result.truncated,
            )
        )

    last_sql = failed[-1].sql if failed else None
    return finish(
        AgentResult(
            question=question,
            status="failed",
            answer="I could not write a working query for that question. Try rephrasing it "
            "or using the exact column names shown on the left.",
            sql=last_sql,
        )
    )
