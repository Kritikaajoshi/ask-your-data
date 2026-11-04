import sqlite3

import pytest

from app.sql_safety import QueryError, UnsafeQueryError, run_select, validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from data;",
        "  SELECT region FROM data -- trailing comment",
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        "(SELECT 1)",
        "SELECT 'semi;colon' AS text",
    ],
)
def test_valid_select_queries_pass(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize(
    "sql, message",
    [
        ("", "empty"),
        ("-- only a comment", "empty"),
        ("DROP TABLE data", "Only SELECT"),
        ("DELETE FROM data", "Only SELECT"),
        ("PRAGMA table_info(data)", "Only SELECT"),
        ("ATTACH DATABASE 'x.db' AS x", "Only SELECT"),
        ("SELECT 1; DROP TABLE data", "one SQL statement"),
        ("SELECT 1 /* hidden */; DELETE FROM data", "one SQL statement"),
    ],
)
def test_unsafe_queries_are_rejected(sql, message):
    with pytest.raises(UnsafeQueryError, match=message):
        validate_sql(sql)


def test_run_select_returns_columns_and_rows(sample):
    _, db_path = sample
    result = run_select(
        db_path,
        "SELECT region, COUNT(*) AS orders FROM data GROUP BY region ORDER BY region",
        row_limit=10,
        timeout_ms=2000,
    )
    assert result.columns == ["region", "orders"]
    assert [row[0] for row in result.rows] == ["East", "North", "South", "West"]
    assert sum(row[1] for row in result.rows) == 600
    assert result.truncated is False


def test_row_limit_truncates(sample):
    _, db_path = sample
    result = run_select(db_path, "SELECT * FROM data", row_limit=5, timeout_ms=2000)
    assert len(result.rows) == 5
    assert result.truncated is True


def test_write_hidden_inside_with_is_blocked_by_database(sample):
    _, db_path = sample
    # Passes the text check (starts with WITH) but the read-only layers still stop it.
    with pytest.raises(QueryError, match="not allowed|readonly|read-only"):
        run_select(
            db_path,
            "WITH x AS (SELECT 1) DELETE FROM data",
            row_limit=10,
            timeout_ms=2000,
        )
    count = sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM data").fetchone()[0]
    assert count == 600


def test_dangerous_functions_are_blocked(sample):
    _, db_path = sample
    with pytest.raises(QueryError, match="not allowed"):
        run_select(db_path, "SELECT load_extension('evil')", row_limit=10, timeout_ms=2000)


def test_slow_queries_time_out(sample):
    _, db_path = sample
    endless = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
        "SELECT COUNT(*) FROM n"
    )
    with pytest.raises(QueryError, match="longer than"):
        run_select(db_path, endless, row_limit=10, timeout_ms=200)


def test_bad_column_gives_clear_error(sample):
    _, db_path = sample
    with pytest.raises(QueryError, match="no such column"):
        run_select(db_path, "SELECT nope FROM data", row_limit=10, timeout_ms=2000)
