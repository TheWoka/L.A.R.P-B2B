"""Host-only tests: python -m pytest eval/test_load_test.py."""

import argparse
import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from eval.load_test import (
    Measurement,
    demo_files,
    main,
    measure,
    memory_bytes,
    run_batch,
    sample_memory,
    worker_container,
    worker_memory,
)


def test_demo_cycle(tmp_path: Path) -> None:
    for index in range(1, 13):
        (tmp_path / f"{index:02d}_demo.pdf").touch()
    (tmp_path / "answers.yaml").touch()
    files = demo_files(tmp_path)
    assert len(files) == 20
    assert files[:8] == files[12:]
    (tmp_path / "12_demo.pdf").unlink()
    with pytest.raises(ValueError, match="12"):
        demo_files(tmp_path)


@pytest.mark.parametrize("value,expected", [("1.5GiB", 1610612736), ("2 MB", 2000000), ("0B", 0)])
def test_memory_units(value: str, expected: int) -> None:
    assert memory_bytes(value) == expected


def test_bad_memory_unit() -> None:
    with pytest.raises(ValueError):
        memory_bytes("unavailable")


def test_docker_commands() -> None:
    with patch(
        "eval.load_test.subprocess.check_output",
        side_effect=[
            "container\n",
            '["celery", "--concurrency=2"]',
            "123MiB / 15GiB\n",
        ],
    ) as command:
        assert worker_container(2) == "container"
        assert worker_memory("container") == 123 * 1024**2
        assert command.call_args.args[0] == [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "{{.MemUsage}}",
            "container",
        ]
    with (
        patch(
            "eval.load_test.subprocess.check_output",
            side_effect=[
                "container",
                '["celery", "--concurrency=4"]',
            ],
        ),
        pytest.raises(ValueError, match="recreate"),
    ):
        worker_container(2)


@pytest.mark.parametrize("failure", [False, True])
def test_batch_completion_and_upload_failure(tmp_path: Path, failure: bool) -> None:
    path = tmp_path / "01_demo.pdf"
    path.write_bytes(b"synthetic")
    uploads = 0
    polls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal uploads, polls
        if request.url.path == "/api/batches":
            assert json.loads(request.content)["purpose"] == "publication"
            return httpx.Response(201, json={"batch_id": "batch"})
        if request.url.path == "/api/documents":
            assert b"mock" in request.content
            uploads += 1
            if failure and uploads == 20:
                return httpx.Response(400, json={"detail": "must not be logged"})
            return httpx.Response(202, json={"task_id": str(uploads), "document_id": str(uploads)})
        polls += 1
        # Empty batch is complete; polling must continue until all uploads finish.
        await asyncio.sleep(0.001)
        total = uploads - int(failure and uploads == 20)
        return httpx.Response(
            200,
            json={
                "complete": polls != 2,
                "documents_total": total,
                "documents_done": total,
                "documents_failed": 0,
                "documents": [],
            },
        )

    async def run() -> Measurement:
        result = Measurement(2)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ) as c:
            await run_batch(c, [path] * 20, result, timeout=2, poll_interval=0.001)
        return result

    result = asyncio.run(run())
    assert uploads == 20
    assert result.first_report_s is not None
    assert result.complete_s >= result.first_report_s
    assert result.documents_done == (19 if failure else 20)
    assert bool(result.errors) == failure
    assert "must not be logged" not in str(result.errors)


def test_timeout_and_failed_task(tmp_path: Path) -> None:
    path = tmp_path / "01_demo.pdf"
    path.touch()

    async def run(complete: bool) -> Measurement:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/batches":
                return httpx.Response(201, json={"batch_id": "batch"})
            if request.url.path == "/api/documents":
                return httpx.Response(202, json={})
            return httpx.Response(
                200,
                json={
                    "complete": complete,
                    "documents_total": 20,
                    "documents_done": 19,
                    "documents_failed": 1,
                    "documents": [{"status": "failed", "task_id": "bad", "error": "private"}],
                },
            )

        result = Measurement(1)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        ) as c:
            await run_batch(c, [path] * 20, result, timeout=0.05, poll_interval=0.001)
        return result

    assert asyncio.run(run(True)).errors == ["task bad: failed"]
    with pytest.raises(TimeoutError):
        asyncio.run(run(False))


def test_memory_sampler() -> None:
    async def run() -> Measurement:
        result = Measurement(1, memory_peak_sampled_bytes=100)
        stop = asyncio.Event()

        def read() -> int:
            stop.set()
            return 200

        await sample_memory(result, read, stop)
        return result

    result = asyncio.run(run())
    assert result.memory_peak_sampled_bytes == 200
    assert result.memory_samples == 1


def test_measure_and_sampler_failure() -> None:
    async def fake_run(
        client: httpx.AsyncClient, files: list[Path], result: Measurement, **kwargs: float
    ) -> None:
        result.documents_done = 20

    args = argparse.Namespace(concurrency=2, base_url="http://test", timeout=1)
    with (
        patch("eval.load_test.demo_files", return_value=[]),
        patch("eval.load_test.worker_container", return_value="worker"),
        patch("eval.load_test.run_batch", side_effect=fake_run),
        patch("eval.load_test.worker_memory", return_value=123),
    ):
        result = asyncio.run(measure(args))
    assert result.memory_before_bytes == result.memory_after_bytes == 123
    assert result.errors == []
    with (
        patch("eval.load_test.demo_files", return_value=[]),
        patch("eval.load_test.worker_container", return_value="worker"),
        patch("eval.load_test.run_batch", side_effect=fake_run),
        patch(
            "eval.load_test.worker_memory",
            side_effect=[123, subprocess.TimeoutExpired("stats", 20)],
        ),
    ):
        result = asyncio.run(measure(args))
    assert result.errors == ["memory: TimeoutExpired"]


def test_cli_output_and_startup_failure(tmp_path: Path) -> None:
    output = tmp_path / "load.json"

    async def success(args: argparse.Namespace) -> Measurement:
        return Measurement(args.concurrency, documents_done=20)

    with (
        patch("sys.argv", ["load_test.py", "--concurrency", "2", "--out", str(output)]),
        patch("eval.load_test.measure", side_effect=success),
    ):
        assert main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["documents_done"] == 20
    with (
        patch("sys.argv", ["load_test.py", "--concurrency", "2"]),
        patch("eval.load_test.measure", side_effect=ValueError("unavailable")),
    ):
        assert main() == 1


def test_demo_cycle_for_100_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(1, 13):
        (tmp_path / f"{index:02d}_demo.pdf").touch()
    monkeypatch.setattr("eval.load_test.FILE_COUNT", 100)
    files = demo_files(tmp_path)
    assert len(files) == 100
    assert files[96:] == files[:4]
