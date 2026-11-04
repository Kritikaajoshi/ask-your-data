# Ask Your Data

[![CI](https://github.com/Kritikaajoshi/ask-your-data/actions/workflows/ci.yml/badge.svg)](https://github.com/Kritikaajoshi/ask-your-data/actions/workflows/ci.yml)

Ask questions about any CSV file in plain English. An LLM agent writes a SQL query, runs it safely
against your data, and explains the result. Every answer shows the exact SQL that produced it, so
you can check the work.

## Features

- Upload a CSV (or use the built-in sample sales dataset) and it becomes a SQLite table with
  inferred column types (whole number, decimal, text)
- Natural language to SQL with Google Gemini
- Self-correction: if a query fails, the error is sent back to the model so it can fix its own SQL
- Automatic retries with backoff when Gemini is rate limited (429) or busy (503)
- Plain-English answers, the generated SQL with highlighted keywords, and a results table
- A benchmark that measures accuracy against reference SQL on 15 questions of increasing difficulty
- 66 automated tests and a GitHub Actions pipeline (tests, Docker build, container smoke test)

## How it works

1. The CSV is parsed, column names are cleaned (for example `Order Date` becomes `order_date`),
   and each column's type is inferred. The data is loaded into a SQLite table called `data`.
2. When you ask a question, the agent builds a description of the table: column names, types,
   number ranges, the exact values of category columns, and a few sample rows.
3. Gemini receives that description plus your question and replies with one SELECT query.
4. The query goes through the safety layer and runs in read-only mode.
5. If it fails, the agent retries with the error message (up to 3 attempts in total).
6. Gemini turns the result rows into a short plain-English answer.

## Safety

SQL written by an LLM is treated as untrusted input. There are several independent layers:

- **Statement check:** only a single statement starting with `SELECT` or `WITH` is accepted.
  Comments are stripped, and a second statement hidden after a `;` is rejected.
- **Read-only database:** the SQLite file is opened with `mode=ro`, so writes fail at the
  database level even if they get past the text check.
- **SQLite authorizer:** a callback allows only read operations and blocks functions like
  `load_extension`. `PRAGMA`, `ATTACH`, `INSERT`, `DELETE`, and `DROP` are all denied.
- **Limits:** a query timeout (3 seconds), a result row limit (500), an upload size limit (10 MB),
  and a per-IP rate limit on questions so a public demo cannot drain the API key.
- **Storage hygiene:** dataset IDs are random UUIDs validated against a strict pattern (no path
  traversal), and uploaded files are deleted after 24 hours.

## Tech stack

- **Backend:** Python 3.12, FastAPI, SQLite (built into Python)
- **AI:** Google Gemini through the `google-genai` SDK
- **Frontend:** plain HTML, CSS, and JavaScript served by FastAPI (no build step)
- **Testing:** pytest with a fake LLM, so tests run without an API key
- **CI/CD:** GitHub Actions, Docker

## Run it locally

You need Python 3.11 or newer and a Gemini API key from https://aistudio.google.com/apikey.

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements-dev.txt

# 3. Add your API key
cp .env.example .env
# then open .env and paste your key after GEMINI_API_KEY=

# 4. Start the server
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000 and click "Use sample sales data".

## Run the tests

```bash
pytest -v
```

The tests use a fake LLM, so they need no API key and no internet.

## Run the benchmark

```bash
python -m benchmark.run_benchmark
```

If you hit free tier rate limits, slow it down with `--delay 4`. Results are saved to
`benchmark/results.md` and `benchmark/results.json`.

A question counts as correct when the agent's query returns the same rows as the reference query.
Extra columns are allowed, since answering "Which product sold the most?" with both the product
and its revenue is still correct.

### Results

Model: gemini-3.6-flash

| Difficulty | Correct |
| --- | --- |
| Easy | 5 / 5 |
| Medium | 5 / 5 |
| Hard | 5 / 5 |

Overall: 15 / 15, median 4 seconds per question.

## Run with Docker

```bash
docker build -t ask-your-data .
docker run -p 8000:8000 --env-file .env ask-your-data
```

## API

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/health` | Server status and whether the model is configured |
| POST | `/api/datasets` | Upload a CSV (multipart form field `file`) |
| POST | `/api/datasets/sample` | Load the sample sales dataset |
| GET | `/api/datasets/{id}` | Columns, row count, and a preview |
| POST | `/api/ask` | Body `{"dataset_id": "...", "question": "..."}` |

Interactive API docs are available at `/docs` while the server is running.

## Project structure

```
app/
  main.py          FastAPI routes
  agent.py         question to SQL to answer, with self-correction
  prompts.py       table description and model instructions
  sql_safety.py    query validation and read-only execution
  datasets.py      CSV parsing, type inference, SQLite storage
  llm.py           Gemini client wrapper
  rate_limit.py    per-IP rate limiting
  config.py        settings from environment variables
static/            frontend (HTML, CSS, JavaScript)
benchmark/         questions with reference SQL, and the benchmark runner
sample_data/       sample sales dataset (generated by scripts/make_sample_data.py)
tests/             pytest suite
```

## Configuration

All settings are environment variables (see `.env.example`). The most important ones:

| Variable | Default | Meaning |
| --- | --- | --- |
| `GEMINI_API_KEY` | none | Required to answer questions |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Any Gemini model your key can use |
| `MAX_UPLOAD_MB` | 10 | Largest CSV accepted |
| `QUERY_TIMEOUT_MS` | 3000 | Queries running longer are stopped |
| `MAX_ATTEMPTS` | 3 | SQL attempts per question, including self-corrections |
| `RATE_LIMIT_REQUESTS` | 30 | Questions allowed per IP per window |
