"""Measures how often the agent answers correctly.

For each question in questions.json, the reference SQL and the agent's SQL are both run on the
sample dataset, and their results are compared. The agent counts as correct when it returns the
same number of rows and every reference value appears in the matching agent row. Extra columns
are allowed, because "Which product sold the most?" answered with the product AND its revenue
is still correct.

Run from the project folder (needs GEMINI_API_KEY in .env):
    python -m benchmark.run_benchmark
    python -m benchmark.run_benchmark --delay 4      # slower, for free tier rate limits
"""

import argparse
import json
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from app import agent
from app.config import BASE_DIR, load_settings
from app.datasets import DatasetStore
from app.llm import GeminiClient, LLMError
from app.sql_safety import run_select

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions.json"
SAMPLE_CSV = BASE_DIR / "sample_data" / "sales.csv"


def _normalize(value) -> str:
    if isinstance(value, float):
        value = round(value, 2)
        if value == int(value):
            value = int(value)
    return str(value).strip().lower()


def results_match(expected_rows: list[list], actual_rows: list[list]) -> bool:
    if len(expected_rows) != len(actual_rows):
        return False
    remaining = [Counter(_normalize(v) for v in row) for row in actual_rows]
    for row in expected_rows:
        needed = Counter(_normalize(v) for v in row)
        for index, candidate in enumerate(remaining):
            if all(candidate[k] >= n for k, n in needed.items()):
                remaining.pop(index)
                break
        else:
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Ask Your Data benchmark.")
    parser.add_argument("--model", help="Override GEMINI_MODEL for this run.")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to wait between questions.")
    parser.add_argument("--limit", type=int, default=0, help="Only run the first N questions.")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY is not set. Add it to your .env file first.")
    model = args.model or settings.gemini_model
    # More patient than the web app: wait out rate limits instead of failing.
    llm = GeminiClient(settings.gemini_api_key, model, max_retries=5, max_wait_seconds=60)

    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if args.limit:
        questions = questions[: args.limit]

    store = DatasetStore(Path(tempfile.mkdtemp()), max_rows=10_000, max_columns=100, ttl_hours=0)
    info = store.create(SAMPLE_CSV.read_bytes(), SAMPLE_CSV.name)
    db_path = store.db_path(info.id)

    records = []
    print(f"Running {len(questions)} questions with {model}\n")
    for item in questions:
        expected = run_select(db_path, item["sql"], row_limit=1000, timeout_ms=5000).rows
        record = {"id": item["id"], "difficulty": item["difficulty"], "question": item["question"]}
        try:
            result = agent.ask(item["question"], info, db_path, llm, explain=False)
            record.update(
                status=result.status,
                sql=result.sql,
                correct=result.status == "ok" and results_match(expected, result.rows),
                attempts=len(result.failed_attempts) + (1 if result.status == "ok" else 0),
                self_corrected=result.status == "ok" and len(result.failed_attempts) > 0,
                seconds=round(result.elapsed_ms / 1000, 2),
            )
        except LLMError as exc:
            record.update(status="error", sql=None, correct=False, attempts=0,
                          self_corrected=False, seconds=0.0, error=str(exc))

        mark = "PASS" if record["correct"] else ("ERROR" if record["status"] == "error" else "FAIL")
        print(f"{mark}  {item['id']}  [{item['difficulty']}]  {item['question']}")
        if record["status"] == "error":
            print(f"      reason: {record['error']}")
        elif not record["correct"]:
            print(f"      status: {record['status']}")
            if record.get("sql"):
                print(f"      agent SQL: {record['sql']}")
        records.append(record)
        if args.delay:
            time.sleep(args.delay)

    # Questions that hit API errors say nothing about the agent, so they are not scored.
    errors = sum(r["status"] == "error" for r in records)
    scored = [r for r in records if r["status"] != "error"]
    total = len(scored)
    correct = sum(r["correct"] for r in scored)
    by_level = defaultdict(lambda: [0, 0])
    for r in scored:
        by_level[r["difficulty"]][0] += r["correct"]
        by_level[r["difficulty"]][1] += 1
    timed = [r["seconds"] for r in scored]

    summary = {
        "model": model,
        "questions": len(records),
        "scored": total,
        "api_errors": errors,
        "correct": correct,
        "accuracy_percent": round(100 * correct / total, 1) if total else 0.0,
        "by_difficulty": {k: f"{v[0]}/{v[1]}" for k, v in by_level.items()},
        "self_corrected": sum(r["self_corrected"] for r in records),
        "median_seconds": round(statistics.median(timed), 2) if timed else None,
    }

    (HERE / "results.json").write_text(
        json.dumps({"summary": summary, "results": records}, indent=2), encoding="utf-8"
    )
    lines = [
        "# Benchmark results",
        "",
        f"Model: {model}",
        "",
        f"Accuracy: {correct}/{total} ({summary['accuracy_percent']}%)",
        "",
        "| Difficulty | Correct |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {v} |" for k, v in summary["by_difficulty"].items()]
    if errors:
        lines += ["", f"Not scored because of API errors: {errors}"]
    lines += [
        "",
        f"Questions fixed by self-correction: {summary['self_corrected']}",
        "",
        f"Median time per question: {summary['median_seconds']} s",
        "",
    ]
    (HERE / "results.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\nAccuracy: {correct}/{total} ({summary['accuracy_percent']}%)")
    if errors:
        print(f"Warning: {errors} questions hit API errors and were not scored. Run again later.")
    print(f"By difficulty: {summary['by_difficulty']}")
    print(f"Self-corrected: {summary['self_corrected']}   Median time: {summary['median_seconds']} s")
    print("Saved benchmark/results.json and benchmark/results.md")


if __name__ == "__main__":
    main()
