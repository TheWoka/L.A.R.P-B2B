"""Все эндпоинты из contracts.md, раздел 4, есть в API (видны в Swagger на /docs)."""

import pytest
from fastapi.testclient import TestClient

CONTRACT_ENDPOINTS = {
    ("post", "/api/documents"),
    ("post", "/api/queries"),
    ("get", "/api/tasks/{task_id}"),
    ("get", "/api/reports/{report_id}"),
    ("get", "/api/reports/{report_id}/text"),
    ("get", "/api/reports/{report_id}/export"),
    ("get", "/api/rules"),
    ("post", "/api/rules"),
    ("put", "/api/rules/{rule_id}"),
    ("delete", "/api/rules/{rule_id}"),
    ("get", "/api/health"),
    # контракт v2 (17.10)
    ("get", "/api/reports/{report_id}/llm-log"),
    ("get", "/api/reports/{report_id}/redacted"),
    ("get", "/api/metrics"),
}


def test_openapi_has_all_contract_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    actual = {(method, path) for path, methods in paths.items() for method in methods}
    assert CONTRACT_ENDPOINTS <= actual


def test_stub_returns_501_with_task_number(client: TestClient) -> None:
    response = client.get("/api/reports/some-id")
    assert response.status_code == 501
    assert "T-009" in response.json()["detail"]


def test_rules_stub_points_to_t010(client: TestClient) -> None:
    response = client.get("/api/rules")
    assert response.status_code == 501
    assert "T-010" in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "task"),
    [
        ("/api/reports/rep_1/llm-log", "T-013"),
        ("/api/reports/rep_1/redacted", "T-026"),
        ("/api/metrics", "T-028"),
    ],
)
def test_contract_v2_stubs_return_501_with_task_number(
    client: TestClient, path: str, task: str
) -> None:
    response = client.get(path)
    assert response.status_code == 501
    assert task in response.json()["detail"]


def test_llm_log_response_schema_is_list_of_llm_call(client: TestClient) -> None:
    """В Swagger у журнала LLM — массив LlmCall, у метрик — Metrics."""
    paths = client.get("/openapi.json").json()["paths"]
    llm_log = paths["/api/reports/{report_id}/llm-log"]["get"]["responses"]["200"]
    schema = llm_log["content"]["application/json"]["schema"]
    assert schema["type"] == "array"
    assert schema["items"]["$ref"].endswith("/LlmCall")
    metrics = paths["/api/metrics"]["get"]["responses"]["200"]
    assert metrics["content"]["application/json"]["schema"]["$ref"].endswith("/Metrics")


def test_query_validation_rejects_empty_text(client: TestClient) -> None:
    """Граничный случай: пустой текст отсекается валидацией до заглушки."""
    response = client.post("/api/queries", json={"text": "", "purpose": "internal"})
    assert response.status_code == 422
