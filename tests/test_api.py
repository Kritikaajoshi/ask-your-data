from tests.conftest import SAMPLE_CSV, FakeLLM


def test_health_reports_missing_llm(make_client):
    response = make_client().get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "llm_configured": False, "model": None}


def test_homepage_is_served(make_client):
    response = make_client().get("/")
    assert response.status_code == 200
    assert "Ask Your Data" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


def test_upload_csv_returns_schema_and_preview(make_client):
    client = make_client()
    response = client.post(
        "/api/datasets", files={"file": ("sales.csv", SAMPLE_CSV.read_bytes(), "text/csv")}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["row_count"] == 600
    assert body["columns"][0] == {"name": "order_id", "original_name": "Order ID", "type": "INTEGER"}
    assert len(body["preview"]["rows"]) == 8

    again = client.get(f"/api/datasets/{body['id']}")
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


def test_sample_dataset_endpoint(make_client):
    response = make_client().post("/api/datasets/sample")
    assert response.status_code == 201
    assert response.json()["filename"] == "sales.csv"


def test_upload_rejects_wrong_type_bad_csv_and_big_files(make_client):
    client = make_client(max_upload_mb=1)
    wrong_type = client.post("/api/datasets", files={"file": ("photo.png", b"abc", "image/png")})
    assert wrong_type.status_code == 400

    empty = client.post("/api/datasets", files={"file": ("empty.csv", b"", "text/csv")})
    assert empty.status_code == 400
    assert "empty" in empty.json()["detail"]

    big = b"a\n" + b"1\n" * (600 * 1024)
    too_big = client.post("/api/datasets", files={"file": ("big.csv", big, "text/csv")})
    assert too_big.status_code == 413


def test_unknown_dataset_is_404(make_client):
    assert make_client().get("/api/datasets/" + "a" * 32).status_code == 404


def test_ask_without_api_key_is_503(make_client):
    client = make_client()
    dataset_id = client.post("/api/datasets/sample").json()["id"]
    response = client.post("/api/ask", json={"dataset_id": dataset_id, "question": "How many?"})
    assert response.status_code == 503


def test_ask_end_to_end(make_client):
    llm = FakeLLM(
        [
            "```sql\nSELECT region, COUNT(*) AS orders FROM data GROUP BY region ORDER BY orders DESC\n```",
            "North had the most orders.",
        ]
    )
    client = make_client(llm=llm)
    dataset_id = client.post("/api/datasets/sample").json()["id"]

    response = client.post(
        "/api/ask", json={"dataset_id": dataset_id, "question": "Which region has the most orders?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["columns"] == ["region", "orders"]
    assert body["rows"][0][0] == "North"
    assert body["answer"] == "North had the most orders."
    assert body["sql"].startswith("SELECT")


def test_ask_validates_input(make_client):
    client = make_client(llm=FakeLLM([]))
    assert client.post("/api/ask", json={"dataset_id": "x", "question": ""}).status_code == 422
    missing = client.post("/api/ask", json={"dataset_id": "a" * 32, "question": "Hi"})
    assert missing.status_code == 404


def test_rate_limit(make_client):
    client = make_client(llm=FakeLLM(["CANNOT_ANSWER: no."] * 5), rate_limit_requests=2)
    dataset_id = client.post("/api/datasets/sample").json()["id"]
    payload = {"dataset_id": dataset_id, "question": "Anything?"}
    assert client.post("/api/ask", json=payload).status_code == 200
    assert client.post("/api/ask", json=payload).status_code == 200
    assert client.post("/api/ask", json=payload).status_code == 429
