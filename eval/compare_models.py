"""Сравнение моделей LLM на эталонном корпусе. Задача T-029b.

Тонкая обёртка над evaluate_corpus с LlmDetector — метрики считаются так же, как
`run_eval.py --llm`. Для каждой модели из --models прогоняет корпус и пишет:
- <out>/compare.json — сводная таблица по всем моделям;
- <out>/<модель>/metrics.json и errors.json — как у run_eval (для разбора пропусков).

Модель переключается переменной окружения LLM_MODEL на время прогона; кэш настроек
(get_settings, lru_cache) сбрасывается до и после каждой модели. Остальные настройки
(LLM_BASE_URL, LLM_API_KEY, LLM_TIMEOUT_S) — из окружения / .env, как у приложения.

Локальная модель (Ollama в профиле local-llm), PowerShell из корня репозитория:
    docker-compose run --rm -e LLM_PROVIDER=openai_compatible `
        -e LLM_BASE_URL=http://ollama:11434/v1 -e LLM_API_KEY=ollama -e LLM_TIMEOUT_S=120 `
        -v "${PWD}/eval:/eval" -v "${PWD}/eval/results:/out" api `
        python /eval/compare_models.py --corpus /eval/seed --models qwen2.5:3b,qwen2.5:7b `
        --out /out/compare-seed

Код выхода: 0 — успех; 2 — ошибка корпуса, нейросеть не настроена (как run_eval --llm) или
сервер модели не ответил на прогревочный запрос (не запущен ollama, неверный LLM_BASE_URL,
модель не скачана) — тогда корпус не прогоняется, уже готовые модели остаются в compare.json.

Время на документ — по часам, включая ответы из кэша Redis: для честного замера скорости
используйте --no-cache (ответы не читаются и не пишутся в кэш). Перед замером модель
прогревается одним синтетическим запросом без текста документа (--no-warmup — отключить);
время прогрева — это загрузка модели в память («холодный старт»).

--semantic-only — абляция: из находок словарей и регулярных выражений убираются
SPECIAL_CATEGORY и TRADE_SECRET, и их ищет только нейросеть. На eval/seed детерминированные
детекторы уже дают recall 1.0 по этим типам, поэтому без абляции модели неразличимы: этот режим
меряет вклад самой модели (recall/precision LLM по смысловым типам). ПДн по-прежнему
маскируются до вызова LLM; текст смысловых фрагментов уходит в модель как есть — как в
конвейере для фрагментов, которые словарь не нашёл (корпус синтетический).

eval/blind сюда не передавать до финального замера (27.10).
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from run_eval import ensure_app_importable

logger = logging.getLogger("eval")

REPORT_TYPES = ("SPECIAL_CATEGORY", "TRADE_SECRET", "FINANCIAL", "PERSON")
COMPARE_FILE = "compare.json"
# Контекст Ollama по умолчанию на CPU и GPU < 24 ГиБ (docs.ollama.com/context-length).
OLLAMA_DEFAULT_CONTEXT = 4096
SEMANTIC_TYPES = frozenset({"SPECIAL_CATEGORY", "TRADE_SECRET"})
WARMUP_TEXT = "Ответь JSON-объектом с полем ok, равным true."


@dataclass
class RequestStats:
    """Реальные HTTP-запросы к модели (без кэша): время, токены, сбои."""

    requests: int = 0
    failed: int = 0           # LLMRequestError: все попытки — таймаут / сеть / 5xx
    timeout_attempts: int = 0  # отдельные попытки, оборвавшиеся по таймауту
    seconds: list[float] = field(default_factory=list)
    max_prompt_tokens: int = 0
    max_total_tokens: int = 0


@dataclass
class ModelRow:
    """Строка сводной таблицы compare.json."""

    model: str
    documents: int
    pd_recall: float
    trade_secret_recall: float
    by_type: dict[str, dict[str, float | int]]
    llm_requests: int
    llm_calls_billed: int
    cached_answers: int
    unknown_answers: int
    failed_requests: int
    timeout_attempts: int
    invalid_json: int
    doc_seconds_avg: float
    doc_seconds_max: float
    request_seconds_avg: float
    request_seconds_max: float
    max_prompt_tokens: int
    max_total_tokens: int
    warmup_seconds: float | None
    llm_timeout_s: float


class _TimeoutCounter(logging.Handler):
    """Считает предупреждения providers «LLM: таймаут запроса» (сами тексты не сохраняет)."""

    def __init__(self, stats: RequestStats) -> None:
        super().__init__(level=logging.WARNING)
        self.stats = stats

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(record.msg, str) and record.msg.startswith("LLM: таймаут запроса"):
            self.stats.timeout_attempts += 1


class ModelUnavailable(Exception):
    """Прогревочный запрос не прошёл: сервер модели недоступен — прогон корпуса бессмыслен."""


class _NullCache:
    """Кэш, который ничего не хранит: для --no-cache."""

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ttl: int) -> None:
        return None


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сравнение моделей LLM на корпусе")
    parser.add_argument("--corpus", type=Path, required=True, help="папка корпуса (seed, dev)")
    parser.add_argument(
        "--models", required=True, help="модели через запятую: qwen2.5:3b,qwen2.5:7b",
    )
    parser.add_argument("--out", type=Path, required=True, help="папка для compare.json")
    parser.add_argument(
        "--no-cache", action="store_true", help="не читать и не писать кэш Redis (честное время)",
    )
    parser.add_argument("--no-warmup", action="store_true", help="без прогревочного запроса")
    parser.add_argument(
        "--semantic-only", action="store_true",
        help="абляция: SPECIAL_CATEGORY и TRADE_SECRET ищет только нейросеть (вклад модели)",
    )
    return parser.parse_args(argv)


def split_models(raw: str) -> list[str]:
    """«a, b,,a» → ["a", "b"]: без пустых и повторов, порядок сохраняется."""
    seen: list[str] = []
    for name in (part.strip() for part in raw.split(",")):
        if name and name not in seen:
            seen.append(name)
    return seen


def safe_dirname(model: str) -> str:
    """Имя модели → имя папки: «hf.co/t-tech/x:Q4_K_M» → «hf.co_t-tech_x_Q4_K_M»."""
    return re.sub(r"[^\w.\-]+", "_", model).strip("_") or "model"


@contextmanager
def model_env(model: str) -> Iterator[None]:
    """LLM_MODEL=model на время блока; кэш get_settings сбрасывается на входе и выходе."""
    from app.core.config import get_settings

    previous = os.environ.get("LLM_MODEL")
    os.environ["LLM_MODEL"] = model
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("LLM_MODEL", None)
        else:
            os.environ["LLM_MODEL"] = previous
        get_settings.cache_clear()


@contextmanager
def instrumented_client(stats: RequestStats, no_cache: bool) -> Iterator[None]:
    """Оборачивает client.request_chat (время, токены, сбои); при no_cache — кэш-пустышка.

    Подмена только на время блока и только в этом процессе скрипта; код приложения не меняется.
    """
    from app.services.llm import client
    from app.services.llm.providers import LLMRequestError

    original_request = client.request_chat
    original_cache = client.get_default_cache

    def timed_request(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        stats.requests += 1
        try:
            result = original_request(*args, **kwargs)
        except LLMRequestError:
            stats.failed += 1
            raise
        finally:
            stats.seconds.append(time.perf_counter() - started)
        stats.max_prompt_tokens = max(stats.max_prompt_tokens, result.usage.get("prompt_tokens", 0))
        stats.max_total_tokens = max(stats.max_total_tokens, result.usage.get("total_tokens", 0))
        return result

    counter = _TimeoutCounter(stats)
    providers_logger = logging.getLogger("app.services.llm.providers")
    providers_logger.addHandler(counter)
    client.request_chat = timed_request
    if no_cache:
        client.get_default_cache = lambda url: _NullCache()
    try:
        yield
    finally:
        client.request_chat = original_request
        client.get_default_cache = original_cache
        providers_logger.removeHandler(counter)


def warmup() -> float | None:
    """Один запрос с синтетическим текстом мимо кэша: загрузка модели в память. Секунды.

    LLMRequestError (сеть, таймаут всех попыток, HTTP 4xx/5xx — например, модель не скачана)
    → ModelUnavailable. Неразобранный ответ — только предупреждение: сервер отвечает.
    """
    from pydantic import BaseModel

    from app.core.config import get_settings
    from app.services.llm.providers import (
        LLMError,
        LLMRequestError,
        build_messages,
        request_chat,
    )

    class _Ok(BaseModel):
        ok: bool = True

    started = time.perf_counter()
    try:
        request_chat(get_settings(), build_messages("Ты проверяешь связь.", WARMUP_TEXT, _Ok))
    except LLMRequestError as exc:
        raise ModelUnavailable(str(exc)) from None
    except LLMError as exc:
        logger.warning("Прогрев не удался: %s — время первого документа включит загрузку",
                       type(exc).__name__)
        return None
    return round(time.perf_counter() - started, 2)


@dataclass
class SemanticOnlyDetector:
    """Как LlmDetector, но без находок SPECIAL_CATEGORY/TRADE_SECRET от словарей и регулярок.

    Порядок тот же, что в конвейере: detect → mask → detect_special_llm → (mask заново) →
    detect_trade_secrets_llm. ПДн остальных типов маскируются, как обычно.
    """

    calls: int = 0
    documents: int = 0

    def __call__(self, doc: Any) -> Any:
        from app.services.detection.detect import detect
        from app.services.llm.client import llm_call_budget
        from app.services.llm.special_category import detect_special_llm
        from app.services.llm.trade_secret import detect_trade_secrets_llm
        from app.services.masking.masker import mask

        entities = [e for e in detect(doc) if e.type.value not in SEMANTIC_TYPES]
        masked = mask(doc.text, entities)
        with llm_call_budget() as budget:
            special = detect_special_llm(doc, masked, entities)
            if len(special) != len(entities):
                masked = mask(doc.text, special)
            found = detect_trade_secrets_llm(doc, masked, special)
        self.calls += budget.used
        self.documents += 1
        return found


@dataclass
class TimedDetector:
    """LlmDetector + журнал LlmCall на документ: время, unknown, ответы из кэша."""

    inner: Callable[..., Any]
    doc_seconds: list[float] = field(default_factory=list)
    unknown: int = 0
    cached: int = 0

    def __call__(self, doc: Any) -> Any:
        from app.services.llm.checks import llm_call_log

        started = time.perf_counter()
        with llm_call_log() as calls:
            found = self.inner(doc)
        self.doc_seconds.append(time.perf_counter() - started)
        self.unknown += sum(1 for c in calls if c.verdict == "unknown")
        self.cached += sum(1 for c in calls if c.cached)
        return found


def run_model(model: str, docs: list[Any], corpus_name: str, args: argparse.Namespace) -> ModelRow:
    """Прогон корпуса одной моделью; пишет metrics.json/errors.json в <out>/<модель>/."""
    from app.core.config import get_settings
    from app.evaluation.report import (
        LLM_CORPUS_SUFFIX,
        LlmDetector,
        build_metrics,
        evaluate_corpus,
        format_table,
        write_results,
    )

    stats = RequestStats()
    with model_env(model), instrumented_client(stats, args.no_cache):
        timeout_s = get_settings().llm_timeout_s
        warm = None if args.no_warmup else warmup()
        stats.timeout_attempts = 0  # прогрев (мимо обёртки) в статистику документов не входит
        llm = SemanticOnlyDetector() if args.semantic_only else LlmDetector()
        timed = TimedDetector(llm)
        result = evaluate_corpus(docs, timed)
    mode = ", только LLM" if args.semantic_only else ""
    metrics = build_metrics(result, corpus=f"{corpus_name}{LLM_CORPUS_SUFFIX} ({model}{mode})")
    write_results(metrics, result.errors, args.out / safe_dirname(model))
    logger.info("\n=== %s ===\n%s", model, format_table(metrics))
    return _make_row(model, metrics, llm.calls, timed, stats, warm, timeout_s)


def _make_row(
    model: str, metrics: Any, billed: int, timed: TimedDetector, stats: RequestStats,
    warm: float | None, timeout_s: float,
) -> ModelRow:
    by_type = {
        m.type: {"precision": m.precision, "recall": m.recall, "support": m.support}
        for m in metrics.by_type if m.type in REPORT_TYPES
    }
    docs_s, req_s = timed.doc_seconds or [0.0], stats.seconds or [0.0]
    return ModelRow(
        model=model, documents=metrics.documents, pd_recall=metrics.pd_recall,
        trade_secret_recall=metrics.trade_secret_recall, by_type=by_type,
        llm_requests=stats.requests, llm_calls_billed=billed, cached_answers=timed.cached,
        unknown_answers=timed.unknown, failed_requests=stats.failed,
        timeout_attempts=stats.timeout_attempts,
        invalid_json=max(0, timed.unknown - stats.failed),
        doc_seconds_avg=round(sum(docs_s) / len(docs_s), 2), doc_seconds_max=round(max(docs_s), 2),
        request_seconds_avg=round(sum(req_s) / len(req_s), 2),
        request_seconds_max=round(max(req_s), 2),
        max_prompt_tokens=stats.max_prompt_tokens, max_total_tokens=stats.max_total_tokens,
        warmup_seconds=warm, llm_timeout_s=timeout_s,
    )


def _fmt(row: ModelRow, entity_type: str) -> str:
    m = row.by_type.get(entity_type)
    return "   –   " if m is None else f"{m['recall']:.2f}/{m['precision']:.2f}"


def format_compare(rows: list[ModelRow]) -> str:
    """Сводная таблица: R/P по типам, recall критериев, unknown, время."""
    head = (f"{'Модель':<34}{'SPEC R/P':>11}{'TS R/P':>11}{'FIN R/P':>11}{'PER R/P':>11}"
            f"{'ПДн R':>7}{'КТ R':>7}{'запр.':>7}{'unkn':>6}{'с/док ср':>10}{'макс':>7}")
    lines = [head]
    for r in rows:
        lines.append(
            f"{r.model[:33]:<34}" + "".join(f"{_fmt(r, t):>11}" for t in REPORT_TYPES)
            + f"{r.pd_recall:>7.3f}{r.trade_secret_recall:>7.3f}{r.llm_requests:>7}"
            f"{r.unknown_answers:>6}{r.doc_seconds_avg:>10.1f}{r.doc_seconds_max:>7.1f}"
        )
    return "\n".join(lines)


def context_warnings(rows: list[ModelRow]) -> list[str]:
    """Предупреждение, если запрос близок к контексту Ollama по умолчанию (4096 токенов)."""
    limit = int(OLLAMA_DEFAULT_CONTEXT * 0.9)
    return [
        f"{r.model}: max total_tokens={r.max_total_tokens} ≥ {limit} — возможна обрезка "
        "промпта контекстом Ollama 4096 (см. OLLAMA_CONTEXT_LENGTH в docs/local-llm.md)"
        for r in rows if r.max_total_tokens >= limit
    ]


def write_compare(rows: list[ModelRow], corpus_name: str, args: argparse.Namespace) -> None:
    """Пишет <out>/compare.json по готовым строкам (в том числе при досрочной остановке)."""
    if not rows:
        return
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {
        "corpus": corpus_name, "no_cache": args.no_cache, "semantic_only": args.semantic_only,
        "rows": [asdict(r) for r in rows],
    }
    (args.out / COMPARE_FILE).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if hasattr(sys.stderr, "reconfigure"):  # консоль Windows (cp1251)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # httpx/httpcore — строка на каждый запрос к модели; ошибки видны в логах app.services.llm
    for noisy in ("app", "pymorphy2", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ensure_app_importable()
    from app.evaluation.corpus import load_corpus
    from app.services.llm.client import is_llm_enabled

    models = split_models(args.models)
    if not models:
        logger.error("--models: пустой список моделей")
        return 2
    with model_env(models[0]):
        if not is_llm_enabled():
            logger.error(
                "Нейросеть не настроена: нужен LLM_PROVIDER=openai_compatible и LLM_BASE_URL "
                "(для Ollama — http://ollama:11434/v1). Сравнение без LLM не имеет смысла",
            )
            return 2
    try:
        docs = load_corpus(args.corpus)
    except ValueError as exc:
        logger.error("Ошибка корпуса: %s", exc)
        return 2
    corpus_name = args.corpus.resolve().name
    rows: list[ModelRow] = []
    for model in models:
        try:
            rows.append(run_model(model, docs, corpus_name, args))
        except ModelUnavailable as exc:
            logger.error(
                "Модель %s не ответила на прогревочный запрос (%s). Проверьте: "
                "docker-compose --profile local-llm ps (ollama — healthy), "
                "docker-compose exec ollama ollama list (модель скачана), LLM_BASE_URL. "
                "Корпус не прогонялся", model, exc,
            )
            write_compare(rows, corpus_name, args)
            return 2
    write_compare(rows, corpus_name, args)
    logger.info("\n%s", format_compare(rows))
    for warning in context_warnings(rows):
        logger.warning("%s", warning)
    logger.info("Результаты: %s", args.out / COMPARE_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
