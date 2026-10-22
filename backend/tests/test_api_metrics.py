"""Готовый файл метрик: приоритет путей, схема, отсутствие и безопасные ошибки."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import metrics
from app.schemas import Metrics


@pytest.fixture
def metrics_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    paths = (tmp_path / "docker" / "metrics.json", tmp_path / "repository" / "metrics.json")
    for path in paths:
        path.parent.mkdir()
    monkeypatch.setattr(metrics, "METRICS_PATHS", paths)
    return paths


def synthetic_metrics(recall: float = 0.94) -> dict[str, object]:
    return {
        "corpus": "synthetic-test", "documents": 2, "created_at": "2026-10-21T12:00:00Z",
        "by_type": [{"type": "PERSON", "precision": 0.95, "recall": recall,
                     "f1": 0.944, "support": 10}],
        "pd_recall": recall, "trade_secret_recall": 0.9,
        "matching": "overlap ≥ 50% и тот же type",
    }


@pytest.mark.parametrize("index", [0, 1])
def test_metrics_file_matches_contract(
    client: TestClient, metrics_paths: tuple[Path, Path], index: int,
) -> None:
    payload = synthetic_metrics()
    path = metrics_paths[index]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    response = client.get("/api/metrics")
    assert response.status_code == 200
    assert Metrics.model_validate(response.json()) == Metrics.model_validate(payload)
    assert path.read_bytes() == before


def test_metrics_prefers_docker_path(client: TestClient, metrics_paths: tuple[Path, Path]) -> None:
    for path, recall in zip(metrics_paths, [0.94, 0.5], strict=True):
        path.write_text(json.dumps(synthetic_metrics(recall)), encoding="utf-8")
    assert client.get("/api/metrics").json()["pd_recall"] == 0.94


def test_missing_metrics(client: TestClient, metrics_paths: tuple[Path, Path]) -> None:
    response = client.get("/api/metrics")
    assert response.status_code == 404
    assert response.json() == {"detail": "Метрики ещё не посчитаны"}


@pytest.mark.parametrize("content", [
    b"", b"{BROKEN_SYNTHETIC_CONTENT", b"\xff\xfe", b"null", b"[]",
    json.dumps({"corpus": "BROKEN_SYNTHETIC_CONTENT"}).encode(),
    json.dumps({**synthetic_metrics(), "pd_recall": 1.1}).encode(),
    json.dumps({**synthetic_metrics(), "by_type": [{"type": "PERSON", "support": -1}]}).encode(),
])
def test_corrupt_metrics_returns_safe_500_without_fallback(
    client: TestClient, metrics_paths: tuple[Path, Path], content: bytes,
    caplog: pytest.LogCaptureFixture,
) -> None:
    metrics_paths[0].write_bytes(content)
    metrics_paths[1].write_text(json.dumps(synthetic_metrics()), encoding="utf-8")
    response = client.get("/api/metrics")
    assert response.status_code == 500
    assert response.json() == {"detail": "Не удалось прочитать метрики"}
    assert "BROKEN_SYNTHETIC_CONTENT" not in response.text + caplog.text
    assert str(metrics_paths[0]) not in response.text


def test_unreadable_metrics_returns_safe_500(
    client: TestClient, metrics_paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == metrics_paths[0]:
            raise PermissionError("BROKEN_SYNTHETIC_CONTENT")
        return reader(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    response = client.get("/api/metrics")
    assert response.status_code == 500
    assert response.json() == {"detail": "Не удалось прочитать метрики"}


def test_metrics_refreshes_from_file(client: TestClient, metrics_paths: tuple[Path, Path]) -> None:
    path = metrics_paths[0]
    for recall in [0.94, 0.97]:
        path.write_text(json.dumps(synthetic_metrics(recall)), encoding="utf-8")
        assert client.get("/api/metrics").json()["pd_recall"] == recall
    path.unlink()
    assert client.get("/api/metrics").status_code == 404
