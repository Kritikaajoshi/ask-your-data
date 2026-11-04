import os
import sqlite3
import time

import pytest

from app.datasets import DatasetError, DatasetStore, clean_column_names, infer_column_type


def test_clean_column_names_makes_safe_unique_names():
    headers = ["Order ID", "Order ID", "2024 Sales", "", "select", "Price ($)"]
    assert clean_column_names(headers) == [
        "order_id",
        "order_id_2",
        "col_2024_sales",
        "column_4",
        "select_value",
        "price",
    ]


@pytest.mark.parametrize(
    "values, expected",
    [
        (["1", "2", "-3"], "INTEGER"),
        (["1", "2.5", ""], "REAL"),
        (["1e3", "4"], "REAL"),
        (["02134", "90210"], "TEXT"),  # zip codes keep their leading zero
        (["apple", "3"], "TEXT"),
        (["", "null", "N/A"], "TEXT"),
    ],
)
def test_infer_column_type(values, expected):
    assert infer_column_type(values) == expected


def test_create_dataset_stores_typed_columns(store):
    raw = b"Name,Age,Score\nAda,36,9.5\nGrace,,8\n"
    info = store.create(raw, "people.csv")

    assert info.row_count == 2
    assert [(c.name, c.type) for c in info.columns] == [
        ("name", "TEXT"),
        ("age", "INTEGER"),
        ("score", "REAL"),
    ]
    rows = sqlite3.connect(store.db_path(info.id)).execute("SELECT * FROM data").fetchall()
    assert rows == [("Ada", 36, 9.5), ("Grace", None, 8.0)]


def test_semicolon_separated_file_is_detected(store):
    info = store.create(b"city;population\nDenver;715000\nBoulder;105000\n", "cities.csv")
    assert [c.name for c in info.columns] == ["city", "population"]
    assert info.row_count == 2


def test_short_rows_are_padded_and_blank_lines_skipped(store):
    info = store.create(b"a,b,c\n1,2\n\n4,5,6\n", "ragged.csv")
    rows = sqlite3.connect(store.db_path(info.id)).execute("SELECT * FROM data").fetchall()
    assert rows == [(1, 2, None), (4, 5, 6)]


@pytest.mark.parametrize(
    "raw, message",
    [
        (b"", "empty"),
        (b"a,b\n", "no data rows"),
        (b",,\n1,2,3\n", "column names"),
    ],
)
def test_bad_files_are_rejected(store, raw, message):
    with pytest.raises(DatasetError, match=message):
        store.create(raw, "bad.csv")


def test_row_and_column_limits(tmp_path):
    small = DatasetStore(tmp_path, max_rows=2, max_columns=2, ttl_hours=24)
    with pytest.raises(DatasetError, match="more than 2 rows"):
        small.create(b"a\n1\n2\n3\n", "rows.csv")
    with pytest.raises(DatasetError, match="limit is 2"):
        small.create(b"a,b,c\n1,2,3\n", "cols.csv")


def test_unknown_and_malicious_ids_are_rejected(store):
    for bad_id in ["nope", "../../etc/passwd", "0" * 32]:
        with pytest.raises(DatasetError):
            store.get(bad_id)


def test_expired_datasets_are_cleaned_up(store):
    info = store.create(b"a\n1\n", "old.csv")
    old = time.time() - 3 * 24 * 3600
    for path in store.data_dir.glob(f"{info.id}.*"):
        os.utime(path, (old, old))

    assert store.cleanup_expired() == 2
    with pytest.raises(DatasetError):
        store.get(info.id)
