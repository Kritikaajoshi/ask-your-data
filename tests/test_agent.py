import pytest

from app import agent
from app.llm import LLMError
from app.prompts import extract_cannot_answer, extract_sql
from tests.conftest import FakeLLM


def test_extract_sql_from_code_block():
    reply = "Here you go:\n```sql\nSELECT 1\n```\nThanks"
    assert extract_sql(reply) == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"


def test_extract_cannot_answer():
    assert extract_cannot_answer("CANNOT_ANSWER: no profit column") == "no profit column"
    assert extract_cannot_answer("```sql\nSELECT 1\n```") is None


def test_happy_path_runs_sql_and_explains(sample):
    info, db_path = sample
    llm = FakeLLM(
        [
            '```sql\nSELECT COUNT(*) AS orders FROM data WHERE "region" = \'North\'\n```',
            "There were 166 orders from the North region.",
        ]
    )
    result = agent.ask("How many orders came from the North?", info, db_path, llm)

    assert result.status == "ok"
    assert result.columns == ["orders"]
    assert result.rows[0][0] > 0
    assert result.answer == "There were 166 orders from the North region."
    assert result.failed_attempts == []
    # The model was shown the table description, including category values.
    system, prompt = llm.calls[0]
    assert "SELECT" in system
    assert "'North'" in prompt and "How many orders came from the North?" in prompt


def test_agent_corrects_itself_after_an_error(sample):
    info, db_path = sample
    llm = FakeLLM(
        [
            "```sql\nSELECT SUM(sales) FROM data\n```",  # wrong column name
            "```sql\nSELECT ROUND(SUM(revenue), 2) AS total FROM data\n```",
            "Total revenue was 86,134.27.",
        ]
    )
    result = agent.ask("What is total revenue?", info, db_path, llm)

    assert result.status == "ok"
    assert result.rows == [[86134.27]]
    assert len(result.failed_attempts) == 1
    assert "no such column" in result.failed_attempts[0].error
    # The second prompt included the failed query and its error.
    assert "SUM(sales)" in llm.calls[1][1] and "no such column" in llm.calls[1][1]


def test_unsafe_sql_from_model_is_never_run(sample):
    info, db_path = sample
    llm = FakeLLM(["```sql\nDROP TABLE data\n```"] * 3)
    result = agent.ask("Delete everything", info, db_path, llm, max_attempts=3)

    assert result.status == "failed"
    assert len(result.failed_attempts) == 3
    assert all("Only SELECT" in a.error for a in result.failed_attempts)


def test_cannot_answer(sample):
    info, db_path = sample
    llm = FakeLLM(["CANNOT_ANSWER: the data has no information about weather."])
    result = agent.ask("What was the weather?", info, db_path, llm)

    assert result.status == "cannot_answer"
    assert "weather" in result.answer
    assert result.sql is None


def test_explanation_failure_falls_back_to_simple_answer(sample):
    info, db_path = sample
    llm = FakeLLM(["```sql\nSELECT COUNT(*) FROM data\n```", LLMError("timeout")])
    result = agent.ask("How many orders?", info, db_path, llm)

    assert result.status == "ok"
    assert result.answer == "The answer is 600."


def test_explain_false_skips_second_call(sample):
    info, db_path = sample
    llm = FakeLLM(["```sql\nSELECT COUNT(*) FROM data\n```"])
    result = agent.ask("How many orders?", info, db_path, llm, explain=False)

    assert result.answer == "The answer is 600."
    assert len(llm.calls) == 1


@pytest.mark.parametrize("question", ["", "   ", "x" * 501])
def test_bad_questions_are_rejected(sample, question):
    info, db_path = sample
    with pytest.raises(ValueError):
        agent.ask(question, info, db_path, FakeLLM([]))
