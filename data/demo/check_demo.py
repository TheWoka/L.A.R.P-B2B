"""Сквозная проверка демо по answers.yaml; выводит только сводные результаты."""

import argparse
import time
from pathlib import Path
from typing import NotRequired, TypedDict

import httpx
import yaml


class Answer(TypedDict):
    file: NotRequired[str]
    text: NotRequired[str]
    purpose: str
    overall: str
    entities: dict[str, int]
    violations: list[str]


class RuleViolation(TypedDict):
    rule_id: str


class Report(TypedDict):
    overall: str
    stats: dict[str, int]
    violations: list[RuleViolation]


def compare_report(answer: Answer, report: Report) -> list[str]:
    """Сравнивает цвет, множество правил и все счётчики, без чтения значений ПДн."""
    differences: list[str] = []
    if answer["overall"] != report["overall"]:
        differences.append(f"цвет {answer['overall']}→{report['overall']}")
    expected_rules = set(answer["violations"])
    actual_rules = {item["rule_id"] for item in report["violations"]}
    if actual_rules - expected_rules:
        differences.append("лишнее: " + ", ".join(sorted(actual_rules - expected_rules)))
    if expected_rules - actual_rules:
        differences.append("нет: " + ", ".join(sorted(expected_rules - actual_rules)))
    expected, actual = answer["entities"], report["stats"]
    for kind in sorted(expected.keys() | actual.keys()):
        before, after = expected.get(kind, 0), actual.get(kind, 0)
        if before != after:
            differences.append(f"{kind} {before}→{after}")
    return differences


def summary(overall: str, rules: int, counts: dict[str, int]) -> str:
    """Краткая сводка для одной ячейки таблицы."""
    entities = ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()) if count)
    return f"{overall}; нарушений: {rules}; {entities or 'сущностей: 0'}"


def request_json(
    client: httpx.Client, method: str, path: str, deadline: float,
) -> dict:
    """Читает JSON, ограничивая запрос оставшимся временем проверки."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError
    response = client.request(method, path, timeout=remaining)
    response.raise_for_status()
    return response.json()


def fetch_report(client: httpx.Client, task_id: str, deadline: float) -> Report:
    """Ждёт завершения задачи, опрашивая API раз в секунду."""
    while True:
        task = request_json(client, "GET", f"/api/tasks/{task_id}", deadline)
        if task["status"] in {"failed", "error"}:
            raise RuntimeError(task.get("error") or "Задача завершилась без описания ошибки")
        if task["status"] == "done":
            return request_json(client, "GET", f"/api/reports/{task['report_id']}", deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        time.sleep(min(1.0, remaining))


def submit(client: httpx.Client, answer: Answer, directory: Path, timeout: float) -> str:
    """Создаёт задачу из файла либо текстовой записи ключа."""
    if "text" in answer:
        response = client.post(
            "/api/queries", json={"text": answer["text"], "purpose": answer["purpose"]},
            timeout=timeout,
        )
    else:
        path = directory / answer["file"]
        with path.open("rb") as source:
            response = client.post(
                "/api/documents", files={"file": (path.name, source)},
                data={"purpose": answer["purpose"]}, timeout=timeout,
            )
    response.raise_for_status()
    return response.json()["task_id"]


def check_one(client: httpx.Client, answer: Answer, directory: Path, timeout: float) -> bool:
    """Печатает одну строку, не раскрывая тело отчёта или HTTP-ошибки."""
    started = time.monotonic()
    expected = summary(answer["overall"], len(set(answer["violations"])), answer["entities"])
    actual, details, matches = "—", "", False
    try:
        task_id = submit(client, answer, directory, timeout)
        report = fetch_report(client, task_id, started + timeout)
        differences = compare_report(answer, report)
        actual = summary(report["overall"], len({v["rule_id"] for v in report["violations"]}),
                         report["stats"])
        matches = not differences
        details = "; ".join(differences) if differences else "совпало"
    except (TimeoutError, httpx.TimeoutException):
        details = "ОШИБКА: таймаут"
    except RuntimeError as error:
        details = "ОШИБКА: " + " ".join(str(error).split())
    except FileNotFoundError:
        details = "ОШИБКА: файл отсутствует"
    except httpx.HTTPStatusError as error:
        details = f"ОШИБКА: HTTP {error.response.status_code}"
    except httpx.RequestError:
        details = "ОШИБКА: запрос к API не выполнен"
    except (KeyError, TypeError, ValueError, OSError):
        details = "ОШИБКА: некорректный ответ или недоступный файл"
    label = answer.get("file", "текстовый запрос")
    print(f"{label} | {expected} | {actual} | {details} | {time.monotonic() - started:.2f} с")
    return matches


def run(client: httpx.Client, directory: Path, only: str | None, timeout: float) -> int:
    """Проверяет доступность API и запускает выбранные записи ключа."""
    try:
        answers: list[Answer] = yaml.safe_load((directory / "answers.yaml").read_text("utf-8"))
    except FileNotFoundError:
        print("ОШИБКА: нет answers.yaml")
        return 2
    except (OSError, yaml.YAMLError):
        print("ОШИБКА: не удалось прочитать answers.yaml")
        return 1
    if not isinstance(answers, list) or not answers:
        print("ОШИБКА: ключ ответов должен содержать записи")
        return 1
    selected = [a for a in answers if only is None or a.get("file", "").startswith(only)]
    if not selected:
        print("ОШИБКА: нет записей для выбранного --only")
        return 1
    try:
        client.get("/api/health", timeout=min(timeout, 5.0)).raise_for_status()
    except httpx.HTTPError:
        print("ОШИБКА: API недоступен (/api/health)")
        return 2
    print("файл | ожидали | получили | совпало / что отличается | время до отчёта")
    matched = sum(check_one(client, answer, directory, timeout) for answer in selected)
    print(f"Совпало {matched} из {len(selected)}")
    return 0 if matched == len(selected) else 1


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI; клиент закрывается после завершения проверки."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--only", help="Префикс имени файла, например 03")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout должен быть конечным положительным числом")
    with httpx.Client(base_url=args.api.rstrip("/")) as client:
        return run(client, Path(__file__).resolve().parent, args.only, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
