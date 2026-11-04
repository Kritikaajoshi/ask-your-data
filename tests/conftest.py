import dataclasses

import pytest
from fastapi.testclient import TestClient

from app.config import BASE_DIR, load_settings
from app.datasets import DatasetStore
from app.main import create_app

SAMPLE_CSV = BASE_DIR / "sample_data" / "sales.csv"


class FakeLLM:
    """Returns scripted replies in order, and records every prompt it receives."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate(self, system, prompt):
        self.calls.append((system, prompt))
        if not self.replies:
            raise AssertionError("FakeLLM ran out of scripted replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(
        load_settings(),
        gemini_api_key="",
        data_dir=tmp_path / "data",
        rate_limit_requests=1000,
    )


@pytest.fixture
def store(settings):
    return DatasetStore(settings.data_dir, settings.max_rows, settings.max_columns, 24)


@pytest.fixture
def sample(store):
    info = store.create(SAMPLE_CSV.read_bytes(), "sales.csv")
    return info, store.db_path(info.id)


@pytest.fixture
def make_client(settings):
    def _make(llm=None, **overrides):
        app = create_app(dataclasses.replace(settings, **overrides), llm=llm)
        return TestClient(app)

    return _make
