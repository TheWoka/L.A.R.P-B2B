"""T-082: 20 synthetic demo files, mock LLM, host-side Docker memory sampling.

Run from the repository root: python eval/load_test.py --concurrency 2
The caller recreates worker with WORKER_CONCURRENCY first (see docs/local-llm.md).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
FILE_COUNT = 20


@dataclass
class Measurement:
    concurrency: int
    batch_id: str = ""
    first_report_s: float | None = None
    complete_s: float | None = None
    documents_done: int = 0
    documents_failed: int = 0
    errors: list[str] = field(default_factory=list)
    memory_before_bytes: int | None = None
    memory_after_bytes: int | None = None
    memory_peak_sampled_bytes: int | None = None
    memory_samples: int = 0


def demo_files(directory: Path) -> list[Path]:
    """Require exactly one document per number, excluding helpers and answer keys."""
    files = []
    for number in range(1, 13):
        matches = sorted(
            p
            for p in directory.glob(f"{number:02d}_*")
            if p.is_file() and p.suffix in {".pdf", ".docx", ".xlsx", ".xml", ".jpg"}
        )
        if len(matches) != 1:
            raise ValueError(f"Expected one demo document for number {number:02d}")
        files.append(matches[0])
    return [files[index % 12] for index in range(FILE_COUNT)]


def memory_bytes(value: str) -> int:
    match = re.fullmatch(r"\s*([0-9.]+)\s*(B|kB|KB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*", value)
    if match is None:
        raise ValueError("Unrecognized docker stats memory unit")
    unit = match[2]
    powers = {
        "B": 0,
        "kB": 1,
        "KB": 1,
        "MB": 2,
        "GB": 3,
        "TB": 4,
        "KiB": 1,
        "MiB": 2,
        "GiB": 3,
        "TiB": 4,
    }
    return int(float(match[1]) * (1024 if "i" in unit else 1000) ** powers[unit])


def worker_container(concurrency: int) -> str:
    container = subprocess.check_output(
        ["docker-compose", "ps", "-q", "worker"],
        text=True,
        timeout=20,
    ).strip()
    if not container or len(container.splitlines()) != 1:
        raise ValueError("Expected exactly one running worker container")
    command = json.loads(
        subprocess.check_output(
            ["docker", "inspect", "--format", "{{json .Config.Cmd}}", container],
            text=True,
            timeout=20,
        )
    )
    if f"--concurrency={concurrency}" not in command:
        raise ValueError("Worker command does not match --concurrency; recreate worker first")
    return container


def worker_memory(container: str) -> int:
    value = (
        subprocess.check_output(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
            text=True,
            timeout=20,
        )
        .strip()
        .split(" / ")[0]
    )
    return memory_bytes(value)


async def sample_memory(
    result: Measurement,
    read_memory: Callable[[], int],
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        value = await asyncio.to_thread(read_memory)
        result.memory_peak_sampled_bytes = max(result.memory_peak_sampled_bytes or 0, value)
        result.memory_samples += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass


async def upload_files(
    client: httpx.AsyncClient,
    files: list[Path],
    result: Measurement,
) -> None:
    for index, path in enumerate(files, 1):
        try:
            with path.open("rb") as stream:
                response = await client.post(
                    "/api/documents",
                    files={"file": (path.name, stream)},
                    data={
                        "purpose": "publication",
                        "batch_id": result.batch_id,
                        "llm_mode": "mock",
                    },
                )
            response.raise_for_status()
            if response.status_code != 202:
                raise ValueError("Expected HTTP 202")
        except (httpx.HTTPError, ValueError) as exc:
            # Never log response bodies: only index and error kind / HTTP status.
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else ""
            result.errors.append(f"upload {index}: {type(exc).__name__} {status}".strip())


async def poll_batch(
    client: httpx.AsyncClient,
    result: Measurement,
    uploaded: asyncio.Task[None],
    started: float,
    poll_interval: float,
) -> None:
    while True:
        response = await client.get(f"/api/batches/{result.batch_id}")
        response.raise_for_status()
        summary = response.json()
        elapsed = time.perf_counter() - started
        result.documents_done = summary["documents_done"]
        result.documents_failed = summary["documents_failed"]
        if result.documents_done and result.first_report_s is None:
            result.first_report_s = elapsed
        # An empty/partially uploaded batch can already be complete by contract.
        expected = FILE_COUNT - len(result.errors)
        if uploaded.done() and summary["complete"] and summary["documents_total"] == expected:
            await uploaded
            result.complete_s = elapsed
            for document in summary["documents"]:
                if document["status"] == "failed":
                    result.errors.append(f"task {document['task_id']}: failed")
            if summary["documents_total"] != FILE_COUNT:
                result.errors.append(
                    f"expected {FILE_COUNT}, got {summary['documents_total']} documents"
                )
            return
        await asyncio.sleep(poll_interval)


async def run_batch(
    client: httpx.AsyncClient,
    files: list[Path],
    result: Measurement,
    *,
    timeout: float = 1800,
    poll_interval: float = 0.5,
) -> None:
    started = time.perf_counter()
    response = await client.post(
        "/api/batches",
        json={"name": f"T-082 mock c={result.concurrency}", "purpose": "publication"},
    )
    response.raise_for_status()
    result.batch_id = response.json()["batch_id"]
    uploaded = asyncio.create_task(upload_files(client, files, result))
    try:
        await asyncio.wait_for(
            poll_batch(client, result, uploaded, started, poll_interval),
            timeout=timeout,
        )
    finally:
        uploaded.cancel()
        await asyncio.gather(uploaded, return_exceptions=True)


async def measure(args: argparse.Namespace) -> Measurement:
    files = demo_files(ROOT / "data" / "demo")
    container = worker_container(args.concurrency)
    result = Measurement(concurrency=args.concurrency)
    result.memory_before_bytes = await asyncio.to_thread(worker_memory, container)
    result.memory_peak_sampled_bytes = result.memory_before_bytes
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_memory(result, lambda: worker_memory(container), stop))
    try:
        async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
            await run_batch(client, files, result, timeout=args.timeout)
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError) as exc:
        result.errors.append(f"run: {type(exc).__name__}")
    finally:
        stop.set()
        try:
            await sampler
            result.memory_after_bytes = await asyncio.to_thread(worker_memory, container)
            result.memory_peak_sampled_bytes = max(
                result.memory_peak_sampled_bytes or 0,
                result.memory_after_bytes,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            result.errors.append(f"memory: {type(exc).__name__}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 4), required=True)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        result = asyncio.run(measure(args))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.error("Cannot start load test: %s", type(exc).__name__)
        return 1
    payload = json.dumps(asdict(result), ensure_ascii=False, indent=2)
    logger.info(payload)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
    return int(bool(result.errors) or result.documents_done != FILE_COUNT)


if __name__ == "__main__":
    raise SystemExit(main())
