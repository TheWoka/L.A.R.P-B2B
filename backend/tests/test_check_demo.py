"""T-037: сравнение ключа и HTTP-сценарии без работающего API."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "data" / "demo" / "check_demo.py"
if not SCRIPT.is_file():
    pytest.skip("В образе api нет data/demo/check_demo.py", allow_module_level=True)
spec = importlib.util.spec_from_file_location("check_demo", SCRIPT)
assert spec is not None and spec.loader is not None
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@pytest.fixture
def answer() -> dict:
    return {"file": "03_demo.pdf", "purpose": "contract", "overall": "red",
            "entities": {"PERSON": 1}, "violations": ["RULE_A", "RULE_B"]}


@pytest.fixture
def report() -> dict:
    return {"overall": "red", "stats": {"PERSON": 1},
            "violations": [{"rule_id": "RULE_B"}, {"rule_id": "RULE_A"}]}


@pytest.mark.parametrize(("change", "expected"), [
    ({}, []),
    ({"violations": [{"rule_id": "RULE_A"}, {"rule_id": "RULE_B"},
                     {"rule_id": "RULE_C"}]}, ["лишнее: RULE_C"]),
    ({"violations": [{"rule_id": "RULE_A"}]}, ["нет: RULE_B"]),
    ({"stats": {"PERSON": 2}}, ["PERSON 1→2"]),
    ({"stats": {"PERSON": 1, "PHONE": 1}}, ["PHONE 0→1"]),
    ({"stats": {"PERSON": 1, "PHONE": 0}}, []),
    ({"stats": {}}, ["PERSON 1→0"]),
    ({"overall": "yellow"}, ["цвет red→yellow"]),
    ({"violations": [{"rule_id": "RULE_A"}, {"rule_id": "RULE_B"},
                     {"rule_id": "RULE_A"}]}, []),
])
def test_compare_report(answer: dict, report: dict, change: dict, expected: list[str]) -> None:
    assert demo.compare_report(answer, report | change) == expected


@pytest.mark.parametrize(("counts", "expected"), [
    ({}, "green; нарушений: 0; сущностей: 0"),
    ({"PHONE": 0, "PERSON": 2}, "green; нарушений: 0; PERSON=2"),
])
def test_summary(counts: dict, expected: str) -> None:
    assert demo.summary("green", 0, counts) == expected


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [0.0]
    monkeypatch.setattr(demo.time, "monotonic", lambda: now[0])

    def sleep(seconds: float) -> None:
        assert 0 < seconds <= 1
        now[0] += seconds

    monkeypatch.setattr(demo.time, "sleep", sleep)
    return now


@pytest.mark.parametrize(("scenario", "code", "message"), [
    ("done", 0, "Совпало 1 из 1"),
    ("mismatch", 1, "PERSON 1→2"),
    ("failed", 1, "ОШИБКА: Учебная ошибка обработки"),
    ("error", 1, "ОШИБКА: Учебная ошибка обработки"),
    ("no_error", 1, "ОШИБКА: Задача завершилась без описания ошибки"),
    ("timeout", 1, "ОШИБКА: таймаут"),
    ("health_down", 2, "API недоступен"),
    ("health_503", 2, "API недоступен"),
    ("missing_file", 1, "файл отсутствует"),
    ("query", 0, "Совпало 1 из 1"),
    ("http_500", 1, "ОШИБКА: HTTP 500"),
    ("request_error", 1, "запрос к API не выполнен"),
    ("invalid_report", 1, "некорректный ответ"),
])
def test_network(
    tmp_path: Path, answer: dict, report: dict, clock: list[float],
    capsys: pytest.CaptureFixture[str], scenario: str, code: int, message: str,
) -> None:
    if scenario == "query":
        answer.pop("file")
        answer["text"] = "Синтетический учебный текст"
    (tmp_path / "answers.yaml").write_text(yaml.safe_dump([answer]), encoding="utf-8")
    if scenario != "missing_file":
        (tmp_path / "03_demo.pdf").write_bytes(b"synthetic document")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/health":
            if scenario == "health_down":
                raise httpx.ConnectError("private response", request=request)
            return httpx.Response(503 if scenario == "health_503" else 200, json={})
        if request.method == "POST":
            if scenario == "query":
                assert json.loads(request.content) == {
                    "text": answer["text"], "purpose": "contract",
                }
                assert request.url.path == "/api/queries"
            else:
                assert request.url.path == "/api/documents"
                assert "multipart/form-data" in request.headers["content-type"]
                assert b'name="file"' in request.content
                assert b'name="purpose"\r\n\r\ncontract' in request.content
            return httpx.Response(202, json={"task_id": "task", "document_id": "doc"})
        if request.url.path == "/api/tasks/task":
            if scenario in {"failed", "error", "no_error"}:
                return httpx.Response(200, json={
                    "status": "error" if scenario == "error" else "failed",
                    "error": None if scenario == "no_error" else "Учебная ошибка обработки",
                })
            if scenario == "timeout" or clock[0] < 2:
                return httpx.Response(200, json={
                    "status": "queued" if clock[0] == 0 else "processing",
                })
            return httpx.Response(200, json={"status": "done", "report_id": "report"})
        assert request.url.path == "/api/reports/report"
        return report_response(request, report, scenario)

    with httpx.Client(base_url="http://demo", transport=httpx.MockTransport(handler)) as client:
        assert demo.run(client, tmp_path, None, 3) == code
    output = capsys.readouterr().out
    assert message in output
    assert "private response" not in output
    assert "Синтетический учебный текст" not in output
    if scenario == "timeout":
        assert clock[0] == 3 and "/api/reports/report" not in requests
    if scenario.startswith("health_"):
        assert requests == ["/api/health"]


def report_response(request: httpx.Request, report: dict, scenario: str) -> httpx.Response:
    if scenario == "request_error":
        raise httpx.ConnectError("private response", request=request)
    if scenario == "mismatch":
        report["stats"]["PERSON"] = 2
    if scenario == "http_500":
        return httpx.Response(500, text="private response")
    return httpx.Response(200, json={} if scenario == "invalid_report" else report)


@pytest.mark.parametrize(("content", "only", "expected", "message"), [
    (None, None, 2, "нет answers.yaml"),
    ("[", None, 1, "не удалось прочитать answers.yaml"),
    ("[]", None, 1, "должен содержать записи"),
    ("{}", None, 1, "должен содержать записи"),
    ("- file: 01_demo.pdf", "03", 1, "нет записей"),
])
def test_invalid_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None,
    only: str | None, expected: int, message: str,
) -> None:
    if content is not None:
        (tmp_path / "answers.yaml").write_text(content, encoding="utf-8")

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Неожиданный запрос: {request.url.path}")

    with httpx.Client(base_url="http://demo", transport=httpx.MockTransport(unexpected)) as client:
        assert demo.run(client, tmp_path, only, 3) == expected
    assert message in capsys.readouterr().out


def test_only_and_continue(
    tmp_path: Path, answer: dict, report: dict, capsys: pytest.CaptureFixture[str],
) -> None:
    answers = [answer | {"file": name} for name in ["01_skip.pdf", "03_missing.pdf", "03_ok.pdf"]]
    (tmp_path / "03_ok.pdf").write_bytes(b"synthetic")
    (tmp_path / "answers.yaml").write_text(yaml.safe_dump(answers), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            assert b"03_ok.pdf" in request.content
            return httpx.Response(202, json={"task_id": "task", "document_id": "doc"})
        if request.url.path == "/api/tasks/task":
            return httpx.Response(200, json={"status": "done", "report_id": "report"})
        return httpx.Response(200, json=report)

    with httpx.Client(base_url="http://demo", transport=httpx.MockTransport(handler)) as client:
        assert demo.run(client, tmp_path, "03", 3) == 1
    output = capsys.readouterr().out
    assert "Совпало 1 из 2" in output and "01_skip" not in output


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_cli_invalid_timeout(timeout: str) -> None:
    with pytest.raises(SystemExit) as error:
        demo.main(["--timeout", timeout])
    assert error.value.code == 2


def test_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    original_client = httpx.Client
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        raise httpx.ConnectError("offline", request=request)

    def client(**kwargs: str) -> httpx.Client:
        return original_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(demo.httpx, "Client", client)
    assert isinstance(demo, ModuleType)
    assert demo.main(["--api", "http://example.test/", "--only", "03"]) == 2
    assert seen == ["http://example.test/api/health"]
